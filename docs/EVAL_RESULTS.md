# RAG 检索质量评测 — 当前结果

> 评测脚本:`rag/eval/run.py`(CLI)+ `rag/eval/report.py`(HTML 看板)
> 评测代码:`rag/eval/core.py`(可复用核心,所有指标定义在此)
> Golden 集:`rag/eval/golden.jsonl` — 56 cases / 41 valid / 6 类场景 / 6 带 forbidden_product_ids

跑法:

```bash
# CLI 速查
python -m rag.eval.run

# 完整 HTML 看板(总表 + 分场景 + 逐 case + 自动结论)
python -m rag.eval.report
open docs/eval_report.html
```

---

## 当前数据(2026-05-24,Round 6,商品库 145 件 × 8 类目)

### 三策略整体对比

| 策略 | recall@5 | recall@10 | MRR | precision@5 | **反选准确率** | 无匹配正确率 | 延迟 |
|---|---|---|---|---|---|---|---|
| Dense | 0.768 | 0.864 | 0.689 | 0.278 | 0.633 | 0.855 | **130 ms** |
| Hybrid (Dense + BM25) | 0.752 | 0.835 | 0.639 | 0.273 | 0.667 | 0.849 | 36 ms |
| **Hybrid + Rerank ★生产路径** | **0.780** | **0.888** | **0.701** | 0.259 | **0.733** | 0.856 | 1 951 ms |

★ 加粗 = 该列最优;**生产路径**(`server/app/services/rag_client.top_k`)走 Hybrid + Rerank。

### 按场景拆解(hybrid_rerank,生产路径)

| 场景 | n cases | recall@5 | MRR | precision@5 | 反选准确率 | 备注 |
|---|---|---|---|---|---|---|
| 基础推荐 basic | 13 | 0.861 | 0.892 | 0.250 | — | 系统强项 |
| 条件筛选 filter | 8 | 0.810 | 0.469 | 0.314 | — | recall@10 = 1.000(候选完整) |
| 反选/排除 negation | 8 | 0.667 | 0.544 | 0.233 | **0.733** | 6 case 带 forbidden,反选准确率是头牌 |
| 多轮追问 multiturn | 5 | 0.900 | 0.867 | 0.280 | — | 受益于 `contextual_query` |
| 多商品对比 compare | 6 | 0.900 | 0.900 | 0.360 | — | 双匹配场景表现最好 |
| 无匹配 no-match | 11 | — | — | — | — | 看 no_match_correctness = 0.820 |

---

## 指标定义

| 指标 | 含义 | 何时适用 |
|---|---|---|
| **recall@k** | 命中的 expected_ids 占 expected 的比例 | expected 非空 |
| **MRR** | expected 第一次出现的位置的倒数 | expected 非空 |
| **precision@k** | top-k 里 expected 的占比 | expected 非空 |
| **反选准确率** | top-k 中不含任何 forbidden id 的比例(粒度到单 slot) | 带 forbidden_product_ids |
| **无匹配正确率** | 启发式:top-k 商品标题与 query 的字符重叠度倒数 — 越低说明系统越"诚实地承认无匹配" | expected = `[]` |
| **延迟** | 单次 retrieve() 的 wall-clock 毫秒 | 每个 case 都测 |

**没用 LLM**:全部指标基于检索回的 product_id 列表 + golden 答案集计算,无 LLM 调用,可重复跑。

---

## 历史对比

| 时间 | Golden 规模 | recall@5 (rerank) | MRR | 备注 |
|---|---|---|---|---|
| Round 5(2026-05-24 上午) | 31 cases / 19 valid | **0.711** | 0.695 | 初版 hybrid+rerank 落地 |
| Round 6 同义词扩展(2026-05-24 下午) | 31 cases / 19 valid | **0.816** | 0.705 | 加了 `synonyms.py` 后 |
| Round 6 扩 golden 集(当前) | **56 cases / 41 valid** | **0.780** | 0.701 | 加入 negation+filter+compare+multiturn,数字微降是因为新增场景更难 |

**为什么扩 golden 后数字反而降?** 因为新 case 是按"难"加的(反选、多轮、跨类目筛选),不是按"易"凑。0.780 在 6 类场景全覆盖下比之前 0.816 在偏 spec 类的小集上更**真实**。

---

## 局限性 / 已知问题

1. **precision@5 ~0.26** — 看上去低,实际是 expected 多为单/双商品的自然下限。要拉高需要扩 expected 到每 case 3-5 个相似商品。这属于 golden 集设计取舍,非检索 bug。
2. **延迟 ~2 秒** — Cross-encoder rerank 是主要成本(默认模型 `BAAI/bge-reranker-base`)。生产路径靠 `services/cache.py` LRU 兜底(命中后 first_delta 可降到 ~300ms,见 `routes/chat.py` 的 timing log)。
3. **no_match_correctness 是启发式**(query-title 字符 Jaccard),不是绝对真理。真正的"反幻觉"由 LLM-side system prompt 守门(`rag/prompts/system.md` + demo 02)。
4. **2 条 multiturn case** 还跑得不够稳,因为 `contextual_query` 重写后 query 形态变化大,recall 波动 ±0.2 都正常。

---

## 看板亮点(答辩可讲)

1. **反选准确率从 dense 的 0.633 提升到 rerank 的 0.733(+10pp)** — 这是 negation_filter + synonym + rerank pipeline 真实贡献的硬证据。
2. **延迟 130ms → 1950ms** — 精度换延迟的工程取舍,缓存抵消是合理的"4.4 工程优化"加分论据。
3. **6 场景全覆盖,不是只跑 spec 类的好看分** — 反选、多轮、对比、无匹配都进了 golden 集,看板按场景拆开后,弱项暴露而非隐藏。

---

## 复现命令

```bash
cd ~/Desktop/rag/AAALion-/
source .venv/bin/activate

# Quick CLI
python -m rag.eval.run
#   → 56 cases (41 with expected, 5 multi-turn, 15 no-match)
#   → 三档 7 个指标的总表

# Full HTML dashboard
python -m rag.eval.report
#   → docs/eval_report.html (+ docs/eval_report.json 原始数据)
open docs/eval_report.html
```

Env 控制:
- `RAG_RERANK=0` — 跳过最慢的 hybrid_rerank 档(快速 sanity 跑)
- 看板可指定 modes / k:`python -m rag.eval.report --modes dense hybrid_rerank --k 5`
