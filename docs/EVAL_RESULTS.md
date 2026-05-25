# RAG 检索质量评测 — 当前结果

> 评测脚本:`rag/eval/run.py`(CLI)+ `rag/eval/report.py`(HTML 看板)
> 评测代码:`rag/eval/core.py`(可复用核心,所有指标定义在此)
> Golden 集:`rag/eval/golden.jsonl` — **59 cases / 44 valid / 9 带 forbidden / 5 multi-turn**

---

## 怎么跑(三种用法 + 必踩坑提示)

### Step 0 — 必须激活 venv

```bash
cd ~/Documents/字节项目/AAALion-
source .venv/bin/activate      # 终端提示符前会出现 (.venv)
```

**不激活会报 `[rerank] cross-encoder unavailable: No module named 'sentence_transformers'`** —
系统 Python 没装依赖,`hybrid_rerank` 那一档会 silently 退化为"截断 hybrid",数字不准。

如果连 .venv 都没建过:
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
```

### Step 1 — CLI 速查(~1 分钟)

```bash
python -m rag.eval.run
```

输出三档 × 7 列的总表。**适合改一个 RAG 参数后立刻验证有没有变好/变坏。**

跳过最慢的 rerank(快速 sanity):
```bash
RAG_RERANK=0 python -m rag.eval.run        # ~10 秒
```

### Step 2 — HTML 看板(~2 分钟)

```bash
python -m rag.eval.report
open docs/eval_report.html
```

生成两个文件:
- `docs/eval_report.html` — 自包含 HTML,总表 + 6 场景拆解 + 逐 case 明细 + 自动结论
- `docs/eval_report.json` — 原始 evaluate() 结果(下游消费用)

**适合答辩现场展示、截图进 PPT、给评委看可点开的逐 case 明细。**

### 必踩坑:Chroma 遥测噪声

跑的时候会刷一堆:
```
Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given
```

**这不是 error**,是 Chroma + PostHog 一个版本兼容性 bug。**对评测结果零影响**,只是看着烦。
关掉:

```bash
CHROMA_TELEMETRY=False python -m rag.eval.report
```

或一劳永逸:
```bash
echo 'export CHROMA_TELEMETRY=False' >> ~/.zshrc && source ~/.zshrc
```

### 命令选项

| 选项 | 含义 |
|---|---|
| `python -m rag.eval.report --modes dense hybrid_rerank` | 只跑指定 mode |
| `python -m rag.eval.report --k 5` | 改 top-k(默认 10) |
| `python -m rag.eval.report --out path.html` | 自定义输出路径 |
| `RAG_RERANK=0 python -m rag.eval.run` | 跳过 rerank 档(快) |
| `CHROMA_TELEMETRY=False ...` | 关 Chroma 噪声 |

---

## 当前数据(2026-05-25,Round 7,商品库 145 件 × 8 类目)

### 三策略整体对比

| 策略 | recall@5 | recall@10 | MRR | precision@5 | **反选准确率** | 无匹配正确率 | 延迟 |
|---|---|---|---|---|---|---|---|
| Dense | 0.727 | 0.828 | 0.650 | 0.264 | 0.733 | 0.855 | 85 ms |
| Hybrid (Dense + BM25) | 0.712 | 0.824 | 0.607 | 0.259 | 0.733 | 0.849 | **20 ms** |
| **Hybrid + Rerank ★生产路径** | **0.784** | **0.884** | **0.683** | 0.259 | **0.822** | 0.853 | 457 ms |

★ 加粗 = 该列最优;**生产路径**(`server/app/services/rag_client.top_k`)走 Hybrid + Rerank。

### 按场景拆解(生产路径 vs Dense 基线)— 真实 tradeoff 暴露在这

| 场景 | n | dense r@5 | rerank r@5 | Δ | 含义 |
|---|---|---|---|---|---|
| 基础推荐 basic | 13 | **0.958** | 0.861 | -0.097 | spec 类 dense 已经精准,rerank 反而引入近邻噪声 |
| 条件筛选 filter | 8 | **0.905** | 0.810 | -0.095 | 价格 filter 由后置 `price_intent` 兜底,rerank 在此弱势 |
| 反选/排除 negation | 11 | 0.463 | **0.611** | **+0.148** | 🎯 rerank + brand_origin 真贡献 |
| 多轮追问 multiturn | 5 | 0.700 | **0.900** | **+0.200** | 🎯 contextual_query + rerank 配合起飞 |
| 多商品对比 compare | 6 | 0.900 | 0.900 | 0.000 | 三档持平 |
| 无匹配 no-match | 11 | — | — | — | 看 no_match_correctness ≈ 0.82 |

**结论**:rerank 在 negation / multiturn 这种"语义复杂"场景大幅胜出,在 spec / filter 这种"已经明确指向"场景略输。Shufeng R7 加的 fast-path(spec 类自动跳过 rerank)就是为这个 tradeoff 准备的。

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

| 时间 | Golden 规模 | recall@5 (rerank) | MRR | 反选准确率 | 延迟 | 备注 |
|---|---|---|---:|---:|---:|---|
| R5(2026-05-24 上午) | 31 / 19 valid | 0.711 | 0.695 | — | — | 初版 hybrid+rerank 落地 |
| R6 同义词扩展(2026-05-24 下午) | 31 / 19 valid | 0.816 | 0.705 | — | — | 加了 `synonyms.py` |
| R6 扩 golden 集(2026-05-24 晚) | 56 / 41 valid | 0.780 | 0.701 | 0.733 | 1951ms | 加入 6 场景 |
| **R7 brand-origin + fast-path(当前)** | **59 / 44 valid** | **0.784** | 0.683 | **0.822** | **457ms** | 反选 +0.089,延迟 −76% |

### R7 这一步发生了什么

1. **反选准确率 0.733 → 0.822(+0.089)** — Shufeng `brand_origin.py` + Yusheng `negation.py` 本地国别兜底,联合关掉"不要日系"漏安热沙的洞
2. **延迟 1951ms → 457ms(−76%)** — Shufeng fast-path 在 spec 类查询自动跳过 rerank
3. **场景 negation recall@5: 0.667 → 0.611** — 加了 3 个更难的 brand-origin case,数字微降但更真实
4. **场景 multiturn recall@5: 0.900 持平** — contextual_query 稳定

---

## 看板亮点(答辩可讲)

1. **反选准确率 dense 0.733 → rerank 0.822(+12%)** + **场景 recall@5 0.463 → 0.611(+32%)** — 这是 Round 7 brand-origin + 同义词 + rerank pipeline 的硬证据。
2. **延迟 1951ms → 457ms(-76%)** + **rerank 路径有 `/cache/stats` 实时监控** — 工程优化 4.4 ⭐ 的可观测证据。
3. **6 场景全覆盖,弱项暴露而非隐藏** — basic / filter rerank 略弱 / negation / multiturn rerank 大胜,真实 tradeoff 拍在评委脸上。
4. **honest gap**:negation_accuracy 0.822 != 1.0,17.8% forbidden 还会漏。golden 里点开"逐 case 明细 → 反选/排除"可以指给评委看具体是哪几个 case。

---

## 局限性 / 已知问题

1. **precision@5 ~0.26** — 看上去低,实际是 expected 多为单/双商品的自然下限。要拉高需要扩 expected 到每 case 3-5 个相似商品。这属于 golden 集设计取舍,非检索 bug。
2. **rerank 延迟 ~500ms** — Cross-encoder 是主要成本(`BAAI/bge-reranker-base`)。生产路径靠 LRU 缓存抵消,看 `/cache/stats`。
3. **no_match_correctness 是启发式**(query-title 字符 Jaccard),不是绝对真理。真正的"反幻觉"由 LLM-side system prompt 守门(`rag/prompts/system.md` + demo 02)。
4. **negation 还有 17.8% 漏网** — 多数是 brand_origin 字典还没收录的偏冷品牌。下一轮可扩到 ~100 个品牌。
5. **filter MRR 在 rerank 档低于 hybrid** — rerank 找到了 expected 商品但没排到 top-3。price_intent 后置排序在 rerank 之后跑,弥补了一部分。

---

## 看板各区块怎么读(打开 HTML 后从上到下)

| 区块 | 看什么 | 答辩用法 |
|---|---|---|
| 🔭 自动观察 | 基于数据自动写的 5-6 条结论 | 直接抄给评委 |
| 📊 三策略总览 | 黄色高亮 = 该列最优;⭐生产路径 = `hybrid_rerank` | 截图进 PPT |
| 📊 Recall@5 柱状图 | 内联 SVG,视觉对比 | 截图 |
| 🎯 分场景拆解 ×6 | basic / filter / negation / multiturn / compare / no-match | 演示 tradeoff 真实存在 |
| 🔍 逐 case 明细 | 点开看具体 query、expected、3 个 mode 各自返回了什么 | 评委问"具体哪个 case 漏了"时打开 |

---

## 改动 golden.jsonl 后怎么重跑

```bash
source .venv/bin/activate
python -m rag.eval.report      # 直接重新跑就好
open docs/eval_report.html
```

不用重启后端,不用 `aaalion ingest`(eval 不经过 LLM,只用检索 + golden 答案对比)。

---

## 一键复现(给评委/新队员)

```bash
git clone https://github.com/YushengLiSam/AAALion-.git
cd AAALion-
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
aaalion ingest                       # ~90 秒,建 Chroma 索引(一次性)

# 看板
CHROMA_TELEMETRY=False python -m rag.eval.report
open docs/eval_report.html
```
