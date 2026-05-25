# RAG 检索质量评测 - 当前结果

> 评测脚本: `rag/eval/run.py` (CLI) + `rag/eval/report.py` (HTML 看板)
> 评测代码: `rag/eval/core.py` (可复用核心, 所有指标定义在此)
> Golden 集: `rag/eval/golden.jsonl` - 59 cases / 49 positive / 10 no-match / 10 带 `forbidden_product_ids`

跑法:

```bash
# CLI 速查
python -m rag.eval.run

# 完整 HTML 看板 (总表 + 分场景 + 逐 case + 自动结论)
python -m rag.eval.report
open docs/eval_report.html
```

---

## 当前数据 (2026-05-25, Round 7.2 CNY 价格标准化后, 商品库 145 件 x 8 类目)

### 三策略整体对比

| 策略 | recall@5 | recall@10 | MRR | precision@5 | **反选准确率** | 无匹配正确率 | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dense | 0.803 | 0.920 | 0.733 | **0.302** | **0.780** | 0.900 | 250 ms |
| Hybrid (Dense + BM25) | 0.755 | 0.897 | 0.701 | 0.294 | 0.740 | 0.897 | **94 ms** |
| **Hybrid + Rerank (生产路径)** | **0.830** | **0.936** | **0.778** | 0.298 | **0.780** | **0.902** | 4,489 ms |

生产路径 `server/app/services/rag_client.top_k` 走 Hybrid + Rerank。本轮数据由 Docker 中重新 ingest 后运行 `python -m rag.eval.report` 生成，原始记录见 [`eval_report.json`](eval_report.json)，可视化见 [`eval_report.html`](eval_report.html)。

### 按场景拆解 (Hybrid + Rerank)

| 场景 | n cases | recall@5 | MRR | precision@5 | 反选准确率 | 备注 |
|---|---:|---:|---:|---:|---:|---|
| 基础推荐 `basic` | 13 | 0.903 | 0.850 | 0.250 | - | recall@10 = 0.972 |
| 条件筛选 `filter` | 8 | 0.810 | 0.576 | 0.314 | - | CNY 预算比较后 MRR 改善 |
| 反选/排除 `negation` | 11 | 0.517 | 0.543 | 0.220 | **0.780** | 下一步重点是提升合规候选召回 |
| 多轮追问 `multiturn` | 5 | **1.000** | 0.900 | 0.280 | - | `contextual_query` + CNY price intent 生效 |
| 多商品对比 `compare` | 6 | 0.917 | 0.917 | **0.400** | - | 品牌/型号对比稳定 |
| 无匹配标签 `no-match` | 6 | - | - | - | - | correctness = 0.866 |

共有 10 条 `expected_product_ids=[]` 的案例；其中 6 条显式标记 `no-match`，另外 4 条为 vague/rewrite 或排除后无替代商品的测试。

---

## Golden 标签审计 (2026-05-25)

本轮没有改变检索算法，而是将评估标准与实际目录重新对齐，共修正 19 条条例:

| 问题类型 | 修正示例 |
|---|---|
| 类型错配 | “油皮洗面奶”移除精华 `p_beauty_004`; “理肤泉防晒”从欧莱雅改为 `p_beauty_023` |
| 目录已有却标为 no-match | 恢复轻量跑鞋、FreeBuds/AirPods 对比、非 Apple 笔记本、保湿面霜、iPad Air、入门相机的正例 |
| 约束正/负样本跨品类 | “不要日系防晒”不再把法系面霜列为正例; “不要法系面霜”不再把防晒列为禁例 |
| 候选范围不完整或不符合意图 | 非 Apple 手机补齐非 Apple 智能手机; “轻量碳板”仅保留具有碳板属性的特步 160X |

因此，审计前后的扩展集指标不能作为纯算法 A/B 差值使用；Round 7.1 的审计后数值才是后续行为修改的可比基线。

## CNY 价格标准化 (Round 7.2)

- `server/app/services/currency.py` 在返回商品卡片前为外币商品获取最新
  Frankfurter 参考汇率，增加 `price_cny` 与 `exchange_rate`，保留原始
  `base_price` / `provenance.currency`。
- `price_intent.py` 现在优先按 `price_cny` 执行“200元以下”“便宜点”等
  人民币条件，不再把美元数值当人民币。
- 相对 Round 7.1 标签审计基线，生产路径 `recall@5` 保持 `0.830`，
  `MRR` 从 `0.771` 升至 `0.778`；`filter` 标签 MRR 从 `0.469` 升至
  `0.576`。
- 外部汇率调用只用于最新参考展示并有一小时缓存；它不是支付结算报价。

---

## 指标定义

| 指标 | 含义 | 何时适用 |
|---|---|---|
| **recall@k** | 命中的 expected ids 占 expected 的比例 | expected 非空 |
| **MRR** | expected 第一次出现的位置的倒数 | expected 非空 |
| **precision@k** | top-k 中 expected 的占比 | expected 非空 |
| **反选准确率** | top-5 中没有落入 `forbidden_product_ids` 的槽位比例 | 带 forbidden ids |
| **无匹配正确率** | 启发式: top-5 商品标题与 query 的字符重叠度越低越好 | expected 为空 |
| **延迟** | 单次 retrieve 的 wall-clock 毫秒 | 每个 case |

所有指标基于返回的 `product_id` 与 golden 标签计算，不调用 LLM，可重复运行。

---

## 历史对比

| 时间 | Golden 规模 | recall@5 (rerank) | MRR | 备注 |
|---|---|---:|---:|---|
| Round 5 (2026-05-24 上午) | 31 cases / 19 positive | 0.711 | 0.695 | 初版 hybrid + rerank |
| Round 6.5 同义词扩展 | 31 cases / 19 positive | 0.816 | 0.705 | 同一小集上的算法对照 |
| Round 7 扩集 (审计前) | 59 cases, 标签含错配 | 0.746 | 0.674 | 仅保留为发现标签问题的记录 |
| Round 7.1 标签审计后 | 59 cases / 49 positive | 0.830 | 0.771 | 标签对齐后的行为基线 |
| **Round 7.2 CNY 价格标准化 (当前)** | **59 cases / 49 positive** | **0.830** | **0.778** | **外币显示及人民币预算排序落地** |

---

## 局限性 / 下一步

1. `brand-origin` 场景仍弱: 生产路径该标签 `recall@5=0.333`, `negation_accuracy=0.800`; 需要继续检查产地元数据和约束后的候选补位。
2. 全量 Docker 冷跑平均延迟约 `4.49 s`: cross-encoder rerank 仍是主要成本，首个外币结果还可能产生一次 FX 请求；fast-path/缓存的线上延迟需要单独测量。
3. `no_match_correctness` 是基于标题字符重叠的启发式分数，不等同于生成端的反幻觉保证。
4. 下一轮应进行双人标注复核，尤其是泛意图查询允许哪些候选，避免扩充商品库后再次出现答案漂移。

---

## 复现命令

```bash
# 在已有依赖环境中
python -m rag.ingest.run
python -m rag.eval.report

# Windows + Docker 验证路径
docker run --rm -e ANONYMIZED_TELEMETRY=False -e RAG_FAST_PATH=1 \
  --mount type=bind,source="$PWD",target=/app \
  aaalion-rag:latest bash -lc "python -m rag.ingest.run && python -m rag.eval.report"
```

本次 Docker 运行结果: `1082` chunks indexed, `59` cases evaluated, three modes all completed with `0` errors。
