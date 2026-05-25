# RAG 检索质量评测 - 当前结果

> 评测脚本: `rag/eval/run.py` (CLI) + `rag/eval/report.py` (HTML 看板)
> 评测代码: `rag/eval/core.py`
> Golden 集: `rag/eval/golden.jsonl` - 59 cases / 49 positive / 10 no-match / 10 带 `forbidden_product_ids`

## 怎么跑

在装好 `server/requirements.txt` 的 Python 环境中:

```bash
python -m rag.ingest.run
python -m rag.eval.report
```

Windows 上可用项目已有 Docker 镜像复现:

```powershell
docker run --rm -e ANONYMIZED_TELEMETRY=False -e RAG_FAST_PATH=1 `
  --mount "type=bind,source=$((Get-Location).Path),target=/app" `
  aaalion-rag:latest bash -lc "python -m rag.ingest.run && python -m rag.eval.report"
```

生成文件:

- `docs/eval_report.html`: 总表、按场景拆解和逐 case 明细。
- `docs/eval_report.json`: 可复用的原始结果。

Chroma/PostHog 的 `capture() takes 1 positional argument but 3 were given`
属于遥测兼容噪声，不影响检索结果；设置 `ANONYMIZED_TELEMETRY=False`
可避免该输出。

## 当前数据 (2026-05-25, merged main + Tujie CNY normalization)

商品库包含 145 件商品、8 个类目。本次结果基于队友刚合入 `main` 的
反选/品牌产地修复，以及 `Tujie` 的 golden 审计与外币人民币标准化。

### 三策略整体对比

| 策略 | recall@5 | recall@10 | MRR | precision@5 | 反选准确率 | 无匹配正确率 | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dense | 0.803 | 0.920 | 0.733 | 0.302 | 0.780 | 0.900 | 284 ms |
| Hybrid (Dense + BM25) | 0.755 | 0.897 | 0.701 | 0.294 | 0.740 | 0.897 | 99 ms |
| **Hybrid + Rerank (生产路径)** | **0.880** | **0.965** | **0.828** | **0.331** | **1.000** | **0.902** | 4,346 ms |

生产路径为 `server/app/services/rag_client.top_k`。Docker 重建索引后运行
`python -m rag.eval.report`，三种策略均为 `0` errors。

### 按场景拆解 (Hybrid + Rerank)

| 场景 | n cases | recall@5 | MRR | precision@5 | 反选准确率 | 备注 |
|---|---:|---:|---:|---:|---:|---|
| 基础推荐 `basic` | 13 | 0.903 | 0.850 | 0.250 | - | 稳定 |
| 条件筛选 `filter` | 8 | 0.810 | 0.576 | 0.314 | - | 人民币预算排序生效 |
| 反选/排除 `negation` | 11 | 0.762 | 0.785 | 0.380 | **1.000** | 队友反选修复与产地审计生效 |
| 多轮追问 `multiturn` | 5 | **1.000** | 0.900 | 0.280 | - | 上下文继承与价格 intent 生效 |
| 多商品对比 `compare` | 6 | 0.917 | 0.917 | **0.400** | - | 稳定 |
| 无匹配标签 `no-match` | 6 | - | - | - | - | correctness = 0.866 |
| 品牌产地 `brand-origin` | 3 | 0.500 | 0.450 | 0.200 | **1.000** | 已无 forbidden 泄漏，召回仍可提升 |

共有 10 条 `expected_product_ids=[]` 的案例；其中 6 条显式标记
`no-match`，另外 4 条为 vague/rewrite 或排除后无替代商品的测试。

## 本轮合并的三层变化

### Golden 标签审计 (Tujie)

对照实际目录修正了 19 条错误或不完整标签，包括类型错配、目录已有却
标成 no-match、跨品类的反选正负样本，以及不完整的候选范围。审计前后
的数字不可作为纯算法 A/B；`59 cases / 49 positive` 是后续比较口径。

### 反选与品牌产地审计 (merged from main)

队友在 `rag/retrieve/negation.py` 与 `rag/retrieve/brand_origin.py`
补充了品牌明示反选、跨语言别名和产地修正，并更正相关真实商品来源数据。
在审计后的 10 条 forbidden 案例上，合并结果的生产路径
`negation_accuracy=1.000`；`brand-origin` 召回仍只有 `0.500`，说明
“不违规”已做到，“更充分地推荐合规替代品”仍值得继续优化。

### CNY 价格标准化 (Tujie)

- `server/app/services/currency.py` 为外币商品查询最新可用 Frankfurter
  参考汇率，返回 `price_cny` 与 `exchange_rate`，不覆盖原始价格。
- `price_intent.py` 按人民币价格执行“200元以下”“便宜点”等条件；
  汇率不可用的外币不会被错误视为满足人民币预算。
- 在仅加入 CNY 排序的 Tujie 分支上，MRR 从 `0.771` 提升到 `0.778`，
  `filter` MRR 从 `0.469` 提升到 `0.576`。
- 汇率仅用于展示与检索比较，有一小时缓存，不是支付结算报价。

## 指标定义

| 指标 | 含义 | 何时适用 |
|---|---|---|
| `recall@k` | 命中的 expected IDs 占 expected 的比例 | expected 非空 |
| `MRR` | 第一个 expected 出现位置的倒数 | expected 非空 |
| `precision@k` | top-k 中 expected 的占比 | expected 非空 |
| 反选准确率 | top-5 中未落入 `forbidden_product_ids` 的槽位比例 | 带 forbidden IDs |
| 无匹配正确率 | 基于 query/title 字符重叠的启发式诚实度指标 | expected 为空 |
| 延迟 | 单次 retrieve 的 wall-clock 毫秒 | 每个 case |

指标仅依赖返回的 `product_id` 与 golden 标签，不调用生成模型。

## 历史对比

| 时间 | Golden 口径 | recall@5 | MRR | 反选准确率 | 备注 |
|---|---|---:|---:|---:|---|
| Round 5 | 31 / 19 positive | 0.711 | 0.695 | - | 初版 hybrid + rerank |
| Round 6.5 同义词扩展 | 31 / 19 positive | 0.816 | 0.705 | - | 同一小集算法对照 |
| Round 7 扩集 (审计前) | 标签含错配 | 0.746 | 0.674 | - | 仅保留为发现标签问题的记录 |
| Round 7.1 标签审计后 | 59 / 49 positive | 0.830 | 0.771 | 0.780 | 标签对齐后的行为基线 |
| Round 7.2 CNY 标准化 (`Tujie`) | 59 / 49 positive | 0.830 | 0.778 | 0.780 | 人民币预算排序落地 |
| **Merged main + Tujie (当前)** | **59 / 49 positive** | **0.880** | **0.828** | **1.000** | **反选审计与 CNY 标准化共同生效** |

队友原分支记录的 `59 / 44 valid` 评测早于本次 golden 标签审计，因口径
不同不与当前表直接计算增益。

## 性能与限制

- 当前 Docker 全量冷跑生产路径平均延迟为 `4.35 s`；cross-encoder
  rerank 是主要成本，第一次遇到外币商品还可能请求 FX 服务。
- 队友已有 20 并发 SSE 压测记录，成功率为 `100%`；缓存命中路径应与
  冷跑评测分开解释。
- `brand-origin` 已无 forbidden 泄漏，但 `recall@5=0.500`，下一步可做
  约束过滤后的候选补位。
- `no_match_correctness` 是启发式指标，不等同于生成端反幻觉保证。
- Golden 集仍建议由第二位标注者复核，特别是泛需求的可接受候选范围。
