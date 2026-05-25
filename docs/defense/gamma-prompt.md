# Gamma Slide-Deck Prompt — 狮选 LionPick Defense (2026-06-11)

> Paste this into [gamma.app](https://gamma.app)'s "generate from text" field.
> Gamma will produce a polished 10-slide deck with cover, content, and notes
> we can refine. Export to PDF and store at `docs/defense/slides-<date>.pdf`.

## Quick disagreement with the "use Gamma for everything" idea

Gamma is **excellent for slide decks** — text-to-deck workflow, good design defaults, easy export. But it is **NOT a good fit for demo videos**: it doesn't capture live UI / SSE streaming behavior. My recommendation is to split:

| Artifact | Tool | Why |
|---|---|---|
| **Slide deck** (Tier 2 #5) | **Gamma** with the prompt below | Strong for static narrative |
| **Demo video** (Tier 2 #6) | **QuickTime → New Screen Recording** on the iPhone 17 Pro simulator | Captures real streaming; voiceover added after |
| **Backup demo** | The 6 screenshots already in `docs/demos/2026-05-25/` | Static fallback if simulator crashes during demo |

The Gamma deck **references** the demo video (link to YouTube unlisted) rather than embedding it. If a slide needs the video inline, Gamma supports embedding via URL.

---

## The prompt (paste below into Gamma)

```
Create a 10-slide defense presentation in Chinese (with key English terms in parentheses) for 狮选 LionPick, a RAG-based multimodal e-commerce shopping AI agent, submitted to ByteDance 2026 AI 全栈挑战赛 (AI Full-Stack Challenge).

Tone: technical but accessible. Avoid corporate fluff. Cite concrete numbers from QUALITY_REVIEW.md and EVAL_RESULTS.md. Use the warm-ivory + amber-gold color palette from the app's theme tokens. Sans-serif rounded font for headers (matches the iOS app's SF Pro Rounded typography).

---

Slide 1 — 标题页 (cover)
- 标题: 狮选 LionPick
- 副标题: 基于 RAG 的多模态电商智能导购 AI Agent
- 团队: AAALion (陈澍枫 · 李雨晟 · 管图杰)
- 比赛: ByteDance 2026 AI 全栈挑战赛
- 答辩日期: 2026-06-11

Slide 2 — 60 秒看懂架构 (architecture in 60 seconds)
- ASCII / 简洁示意图:
  iPhone (SwiftUI) → FastAPI SSE → Chroma (text + image) + BM25 → 跨模型重排 (cross-encoder rerank) → TokenRouter (claude-haiku-4-5) → SSE 回流
- 关键决策:
  - iOS 原生客户端，非 H5；
  - Chroma in-process，不依赖外部数据库服务；
  - 多 provider 抽象层 (Anthropic / Doubao / TokenRouter / OpenAI / Echo)，环境变量切换；
  - A100 GPU 仅用于 CLIP 图像索引构建 (build-time)，不在请求路径。

Slide 3 — 三项核心创新 (top 3 engineering wins)
- 混合检索 + 跨模型重排 (Hybrid retrieval + cross-encoder rerank)
  - dense (sentence-transformers BGE-zh) + BM25 (jieba 中文分词) + RRF 融合
  - 跨模型 BAAI/bge-reranker-base，在 20 候选 → 5 输出
- 多轮上下文继承 (multi-turn contextual query)
  - 「再便宜点的呢」自动继承上文 anchor 词「洗面奶」，并用价格 intent 排序
  - 实现: server/app/services/contextual_query.py
- 品牌产地反选 (brand-origin negation, R7 新)
  - 「不要日系品牌」自动剔除 安热沙 / 资生堂 / Sony …
  - 实现: rag/retrieve/brand_origin.py (70-brand 字典 + ISO-2 country codes)

Slide 4 — 真实商品 + 来源可验证 (real products + provenance)
- 145 个商品 / 8 类目: 美妆 / 数码 / 服饰 / 食品 / 母婴 / 家居 / 图书 / 户外
- 45 个真实商品 (Tmall / JD / Amazon US / Amazon JP)
  - 每张卡片显示: 国旗 emoji (🇨🇳/🇺🇸/🇯🇵/🇫🇷)、人民币主价格、外币原价与来源平台
  - 「去原页」按钮一点跳真实商品详情页 - 评委可现场验证
- 100 个 AI-gen demo (用「演示」徽章标识，与真实商品视觉区分)
- 外币标准化: 后端查询 Frankfurter 最新参考汇率，美元商品展示为人民币，
  商品详情披露原价、汇率日期与来源；购物车统一人民币合计
- 诚实边界: 这是展示用参考换算而非支付结算价；若汇率不可用则保留原币并不计入人民币合计

Slide 5 — 检索质量量化 (measured retrieval quality)
- 评测体系: 59 audited cases (49 positive / 10 no-match) / 6 场景 / 3 策略 (dense, hybrid, hybrid+rerank)
- 看板: docs/eval_report.html (HTML 仪表盘，逐 case 可点)
- 生产路径 (hybrid+rerank) 整体指标:
  | recall@5 | recall@10 | MRR | 反选准确率 | 无匹配正确率 | 平均延迟 |
  |---|---|---|---|---|---|
  | 0.830 | 0.936 | 0.778 | 0.780 | 0.902 | 4,489 ms |
- 指标口径: 2026-05-25 按商品目录审计并修正 19 条 golden 标签后的新基线，
  不将审计前后差值宣称为纯算法增益
- 分场景亮点:
  - multiturn: recall@5 = 1.000 (contextual_query + price intent 推动)
  - compare: recall@5 = 0.917
  - basic: recall@5 = 0.903
  - filter: MRR 0.469 → 0.576 (外币按人民币比较后排序改善)

Slide 6 — 现场 Demo (live demos)
- 6 个场景全过一遍，每个场景对应 docs/demos/2026-05-25/<NN>.md 的详细日志:
  1. 基础推荐: 推荐一款适合油皮的洗面奶 → 珊珂洁面 ¥52
  2. 条件筛选: 200元以下的蓝牙耳机 → 诚实回答「无现货」+ 推荐次低价
  3. 反选 (R7 头牌): 不要日系防晒霜 → 巴黎欧莱雅 + 理肤泉 (零日系)
  4. 多轮: 「再便宜点的呢」自动继承上文，按价格升序
  5. 对比: 雅诗兰黛 vs 兰蔻熬夜适合度，4 维度对比表
  6. 无匹配: 推荐量子计算机 → 诚实拒绝 + 推荐最接近替代
- 备份视频: QuickTime 屏幕录制 (3 分钟版本，docs/defense/demo-video.mov)

Slide 7 — 工程质量 (engineering quality)
- 多 Provider LLM 抽象层 (5 种 provider，环境变量切换)
- LRU 缓存 (TTL 10 min)，命中后首字延迟 ~300ms (24× 加速)
- 上游错误指数退避重试 (3 次)
- 流式取消: 客户端断开 → 服务端立刻停止调用 LLM (省 quota)
- 压测: 20 并发 × 45 秒，**100% 成功率**，p50 first-delta 2.3 秒 (docs/stress_test_2026-05-25.md)
- 私有化部署: Dockerfile + docker-compose.yml (server/)
- 24 + 篇文档: CLAUDE.md (新代理 bootstrap) + ARCHITECTURE / API / DEPLOY_GUIDE / RUBRIC_MAPPING 等

Slide 8 — 答辩三问预演 (anticipated Q&A)
- Q: 商品数据是真的吗？
  A: 45 个真实 (有真 URL 可验证，去原页一点即跳)，100 个 AI 演示 (UI 用「演示」徽章明示，不糊弄)
- Q: 反幻觉 (no-hallucination) 怎么做到？
  A: System prompt 严格约束「仅基于目录回答」；docs/demos/02 / 06 demo 中模型主动承认无匹配；审计后评测中 no_match_correctness = 0.902
- Q: 检索做了什么独特工作？
  A: 混合检索 + 中文同义词扩展 + 多轮上下文继承 + 价格意图解析 + 品牌产地反选 — 同一套 pipeline 端到端测量

Slide 9 — 团队分工 + 时间线 (team + timeline)
- 陈澍枫 (Shufeng): iOS 客户端 + 项目兜底 (fallback for everything)
- 李雨晟 (Yusheng): 后端 + 评测看板
- 管图杰 (Tujie): RAG 检索 + 同义词维护
- 时间线: 2026-05-22 起步 → 2026-05-25 R7 收尾 → 2026-06-10 代码冻结 → 2026-06-11 答辩

Slide 10 — 现状与下一步 (where we are + what's next)
- 当前估分: 90 / 100 (自评 docs/QUALITY_REVIEW.md)
- 已知局限 (诚实披露):
  - 商品图为 AI 渲染占位 (image_source: "ai-gen-placeholder")，去原页是真链接
  - 新增 4 类目 (母婴/家居/图书/户外) 每类目仅 5 件，覆盖窄
  - 压测仅 20 并发，真实 100 RPS 未跑 (LLM quota 限制)
- 答辩前还要做: 演示视频录制 (QuickTime) + 真实热门商品的官方图替换 (10 件)
- 项目仓库: github.com/YushengLiSam/AAALion-

(结束)
```

## How to use

1. Paste the prompt into Gamma's "generate from text" prompt box.
2. Choose **deck** (not website/document) and let Gamma generate.
3. Review each slide. Pull in screenshots from `docs/demos/2026-05-25/` where slides reference visuals (especially slides 4 and 6).
4. Export to PDF: `File → Export → PDF`. Save as `docs/defense/slides-2026-05-25.pdf` (commit) and a Gamma URL link in this file's footer.
5. Keep the Gamma URL too — easier to update right up to defense day.

## When to regenerate

Re-run this prompt **after each major round** (R7.5 if anything material changes before code-freeze). The prompt is the durable source; the Gamma artifact is a regenerable derivative.

## Fallback if Gamma output is poor

Use the same prompt content to manually compose in Keynote or PowerPoint. Each slide section above is already broken out — copy-paste content directly. The Gamma layout aesthetics are the only loss.
