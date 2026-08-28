# RAGAS 评估接入方案 — 狮选 LionPick

> 状态:方案(未实现)。前置:本地后端可跑、TokenRouter key 可用。
> 一句话:给现有"确定性检索评测"补上"生成质量评测"这条腿,并保持我们的
> 特色——**能确定性断言的绝不交给 LLM 裁判,LLM 裁判只干规则干不了的活**。

---

## 1. 现状缺口:我们测了"检索",没测"生成"

| 问题 | 现有评测(golden.jsonl 92例) | 缺口 |
|---|---|---|
| 检索找得对吗? | ✅ recall@5 0.947 / MRR 0.860(靠人工标注期望商品) | — |
| 否定被遵守吗? | ✅ negation 1.000(确定性断言) | — |
| **LLM 的回答忠实于目录吗?** | ❌ 只有 [目录✓]/[推断?] 标签,是**模型自己声明的**,没人验证它标得对不对 | ← RAGAS faithfulness |
| **回答切题吗?** | ❌ 完全没测 | ← RAGAS answer_relevancy |
| 喂给 LLM 的 5 张卡都有用吗? | ❌ recall 只看"该来的来没来",没看"来的里面有多少噪声" | ← RAGAS context_precision |

RAGAS 的价值:**不依赖人工标注**,用 LLM-as-judge 自动评生成侧。
对我们的意义:[目录✓] 标签只是"自我声明的忠实",RAGAS 给出**第三方审计的忠实**
——两者对照本身就是一个亮点分析(§6)。

## 2. 指标选型(取 3 个 RAGAS + 1 个自研确定性)

| 指标 | 测什么 | 怎么算 | 为什么选 |
|---|---|---|---|
| **faithfulness** | 回答里每个论断是否能被检索上下文支撑 | judge LLM 拆句→逐条比对上下文 | 抗幻觉的第三方审计,核心卖点 |
| **answer_relevancy** | 回答是否切题 | judge 由答案反推问题 + **本地 bge 向量**比相似度 | 免标注;embedding 用我们自己的 bge-small-zh(中文、免费) |
| **context_precision** | 5 张卡里有多少真有用(信噪比) | judge 逐卡判相关性 | 补 recall 的盲区:召回全≠噪声低 |
| **fact_grounding(自研)** | 回答中出现的**价格/品牌**是否真的在目录里 | **纯正则+集合比对,零 LLM** | 我们的哲学:数字幻觉这种能确定性断言的,不浪费 judge 配额;LLM 裁判常漏数字错误 |
| ~~context_recall~~ | ~~上下文覆盖度~~ | 需要参考答案 | **不要**:我们的 recall@5 已用标注商品 id 确定性地测了同一件事,更硬 |

## 3. 架构与数据流

```
golden.jsonl 抽样(默认 20 条:12 正常 + 5 否定 + 3 多跳)
   │
   ▼  P1:采样收集器 rag/eval/ragas_collect.py
打本地后端 /chat/stream(复用响应缓存,预热过的 query 零 LLM 成本)
   │  抓:question / answer(拼接 delta) / contexts(product_card 重建目录行,
   │      与 _build_catalog 同格式 —— LLM 实际看到的就是它)
   ▼
triples.jsonl(question, answer, contexts[], expected_ids, claim_summary)
   │
   ▼  P2:RAGAS 评测 rag/eval/run_ragas.py
judge = TokenRouter claude-haiku-4-5(OpenAI 兼容端点,LangChain wrapper 直连)
embeddings = 本地 bge-small-zh-v1.5(sentence-transformers,零成本)
   │  + judge 结果按 (query,answer,contexts) 哈希落盘缓存 → 重跑免费
   ▼
faithfulness / answer_relevancy / context_precision(逐条 + 汇总)
   │
   ▼  P3:合并报告
+ fact_grounding(确定性) + 与 claim_summary 标签的相关性分析
→ 控制台表格 + docs/eval_ragas.json
```

## 4. 成本控制(重要:TokenRouter workspace 共享 1000 请求配额)

粗估 20 样本全跑 ≈ **~160 次 judge 调用**(faithfulness ~2/条 + relevancy ~1/条 + precision ~5/条)+ 生成 0~20 次(缓存命中则 0)。控制手段:
- `--n 20` 抽样数可调,`--metrics faith,rel` 可只跑子集
- **judge 结果落盘缓存**:同 (query,answer,contexts) 不重复调用,迭代重跑≈免费
- answer_relevancy 的 embedding 端完全本地(bge),不耗配额
- fact_grounding 零成本,可以对**全量 92 例**跑

## 5. 实现要点

- 依赖:`ragas`(锁定当前稳定版)+ `langchain-openai`(接 TokenRouter)+ 现有 sentence-transformers。全新依赖只进 dev,不进服务端 requirements
- judge 走 `OPENAI_BASE_URL=TokenRouter` 的 ChatOpenAI wrapper;RAGAS 默认英文裁判 prompt 对中文内容可用(haiku 中文没问题);若某指标不稳可覆写为中文 prompt(RAGAS 支持自定义 prompt)
- **裁判偏置声明**(答辩/面试要主动说):judge 与生成是同一个模型(haiku 裁 haiku)存在自评偏置;缓解:fact_grounding 提供无偏的确定性对照,且 judge 可一键换 TokenRouter 上另一家模型交叉验证
- 多轮/多跳样本:question 取最后一轮用户话,contexts 含锚点卡——faithfulness 顺带审计多跳回答

## 6. 亮点分析(免费送的差异化)

**"自声明 vs 审计"对照**:每条回答我们有 claim_summary(N 条[目录✓] M 条[推断?]),
RAGAS 有 faithfulness 分。做个散点/相关:
- 高[目录✓]比例 ↔ 高 faithfulness → 证明标签机制真实有效(答辩金句:"我们的
  来源标签不是装饰,经 RAGAS 第三方审计相关性为 X")
- 若发现"标了[目录✓]但 faithfulness 低"的条目 → 那就是标签作弊的实锤,反过来
  帮我们改 prompt。两个方向都有故事。

## 7. 分期与工作量

| 阶段 | 内容 | 量 |
|---|---|---|
| P1 | 采样收集器(triples.jsonl,复用缓存) | ~0.5 天 |
| P2 | RAGAS 三指标 + TokenRouter judge + 本地 bge + judge 缓存 | ~1 天 |
| P3 | fact_grounding(全量 92 例)+ 合并报告 + 标签相关性 | ~0.5 天 |

## 8. 风险

| 风险 | 缓解 |
|---|---|
| ragas 版本 API 变动快 | 锁版本;评测代码隔离在 rag/eval/,不碰服务端 |
| judge 配额烧超 | §4 四层控制;先 `--n 5` 冒烟再放量 |
| 中文裁判不稳 | 可覆写中文 prompt;fact_grounding 兜底数字类错误 |
| 同模自评偏置 | 主动声明 + 确定性对照 + 可换裁判模型 |

## 9. 面试/答辩叙事(一句话)

> "我们的评测是三层的:检索层用**人工标注+确定性指标**(recall@5 0.947/否定 1.000),
> 生成层引入 **RAGAS LLM-as-judge**(faithfulness/relevancy/precision,免标注),
> 数字类事实用**零成本正则断言**兜底(fact_grounding)——能确定性验证的绝不浪费
> 裁判配额,LLM 裁判只干规则干不了的活;并用 RAGAS 反向审计了我们的 [目录✓]
> 来源标签机制的真实性。"
