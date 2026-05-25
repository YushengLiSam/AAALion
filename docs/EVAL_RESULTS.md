# RAG 检索质量评测 - 当前结果

> 评测脚本: `rag/eval/run.py` (CLI) + `rag/eval/report.py` (HTML 看板)
> 评测代码: `rag/eval/core.py`
> Golden 集: `rag/eval/golden.jsonl` - 64 cases / 54 positive / 10 no-match / 10 带 `forbidden_product_ids`

## 怎么跑

在装好 `server/requirements.txt` 的 Python 环境中:

```bash
python -m rag.ingest.run
python -m rag.eval.report
```

Windows 上可用项目已有 Docker 镜像复现:

```powershell
docker run --rm -e ANONYMIZED_TELEMETRY=False -e CHROMA_TELEMETRY=False `
  -e RAG_FAST_PATH=1 -e RAG_HARD_FILTERS=1 `
  --mount "type=bind,source=$((Get-Location).Path),target=/app" `
  aaalion-rag:latest bash -lc "python -m rag.ingest.run && python -m rag.eval.report"
```

生成文件:

- `docs/eval_report.html`: 总表、按场景拆解和逐 case 明细。
- `docs/eval_report.json`: 可复用的原始结果。

Chroma/PostHog 的 `capture() takes 1 positional argument but 3 were given`
属于遥测兼容噪声，不影响检索结果；设置 `ANONYMIZED_TELEMETRY=False`
可避免该输出。

## 当前数据 (2026-05-25, constraint-aware retrieval + CNY normalization)

商品库包含 145 件商品、8 个类目。本次结果基于队友刚合入 `main` 的
反选/品牌产地修复，以及 `Tujie` 的 golden 审计、外币人民币标准化与
检索阶段硬约束。本轮新增 5 条 `constraint-filter` 回归样例。

### 三策略整体对比

| 策略 | recall@5 | recall@10 | MRR | precision@5 | 反选准确率 | 无匹配正确率 | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dense | 0.804 | 0.919 | 0.736 | 0.311 | 0.780 | 0.900 | 250 ms |
| Hybrid (Dense + BM25) | 0.769 | 0.907 | 0.729 | 0.311 | 0.740 | 0.897 | 100 ms |
| **Hybrid + Rerank (生产路径)** | **0.981** | **0.994** | **0.846** | **0.400** | **1.000** | **0.941** | 881 ms |

生产路径为 `server/app/services/rag_client.top_k`。Docker 重建索引后运行
`python -m rag.eval.report`，三种策略均为 `0` errors。

### 按场景拆解 (Hybrid + Rerank)

| 场景 | n cases | recall@5 | MRR | precision@5 | 反选准确率 | 备注 |
|---|---:|---:|---:|---:|---:|---|
| 基础推荐 `basic` | 13 | **1.000** | 0.917 | 0.300 | - | 稳定 |
| 条件筛选 `filter` | 13 | 0.972 | 0.917 | 0.467 | - | 检索阶段硬约束生效 |
| 新增约束回归 `constraint-filter` | 5 | **1.000** | **1.000** | **0.560** | - | 类别/品牌/人民币预算 |
| 反选/排除 `negation` | 11 | 0.929 | 0.783 | 0.480 | **1.000** | 队友反选修复与产地审计生效 |
| 多轮追问 `multiturn` | 5 | **1.000** | 0.800 | 0.280 | - | 上下文继承与价格 intent 生效 |
| 多商品对比 `compare` | 6 | **1.000** | 0.917 | 0.433 | - | 稳定 |
| 无匹配标签 `no-match` | 6 | - | - | - | - | correctness = 0.962 |
| 品牌产地 `brand-origin` | 3 | **1.000** | 0.611 | 0.400 | **1.000** | 约束后的合规替代召回提升 |

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

### 检索阶段硬约束 (Tujie)

- `rag/retrieve/constraints.py` 从查询和 API filters 解析类目、子类目、
  指定品牌、排除品牌与人民币价格上下限。
- 同一个 `Filter` 进入 Chroma dense 与 BM25，再进行 hybrid fusion；
  不再依赖召回后才尝试筛掉明显不满足条件的商品。
- `currency` 写入 Chroma 元数据。人民币商品可提前按预算收窄；美元等
  外币商品保留到实时换算后，再做严格人民币判断，避免将 `$20` 当成
  `¥20`，也避免过早漏召回。
- 目录复核发现 `300元以下的面霜` 还应包含折算后约 `¥156` 的
  `p_1_intl_05`，已加入 golden 预期结果；本节数字基于修正后的标签。
- 在同一 64-case 集上切换 `RAG_HARD_FILTERS`：`filter` MRR
  `0.635 → 0.917`，`filter` recall@5 `0.889 → 0.972`；
  整体 recall@5 `0.891 → 0.981`。两组均为 `0` errors。

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
| Merged main + Tujie | 59 / 49 positive | 0.880 | 0.828 | 1.000 | 反选审计与 CNY 标准化共同生效 |
| **R7.4 约束过滤 (当前)** | **64 / 54 positive** | **0.981** | **0.846** | **1.000** | **dense/BM25 同步按条件召回；新增 5 条回归** |

队友原分支记录的 `59 / 44 valid` 评测早于本次 golden 标签审计，因口径
不同不与当前表直接计算增益。

## 性能与限制

- 当前 Docker 全量评测生产路径平均延迟为 `0.88 s`；cross-encoder
  rerank 是主要成本，第一次遇到外币商品还可能请求 FX 服务。
- 队友已有 20 并发 SSE 压测记录，成功率为 `100%`；缓存命中路径应与
  冷跑评测分开解释。
- 约束词典目前聚焦目录已有、含义清晰的类别/子类别和品牌别名；扩展规则
  前应同时补充 golden 样例，以防把模糊需求过早硬过滤。
- `no_match_correctness` 是启发式指标，不等同于生成端反幻觉保证。
- Golden 集仍建议由第二位标注者复核，特别是泛需求的可接受候选范围。
