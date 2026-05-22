# WeChat 更新 — 2026-05-22

> 准备直接粘贴到群里。可以一段段发，也可以分段发。文末附了"重点速读"和"我需要你们做的事"。

---

各位 @雨晟 @图杰，22 号晚上我把项目仓库搭起来了。比赛是字节 **AI 全栈挑战赛**（不是工程训练营），课题"基于 RAG 的多模态电商智能导购 AI Agent"，截止 6/10，答辩 6/11-19。我先把当前进度全部同步一下，建议大家拿 Trae / Cursor / Claude Code 之类的 AI 工具直接喂 `docs/EXECUTION_SUMMARY.md` 让它先帮你们梳理一遍，效率高一些。

## 项目命名

- **团队名**：AAALion（不变）
- **产品名**：**狮选 LionPick** — 一只狮子帮你"选品"，中英都顺口，跟团队品牌呼应。如果有更好的名字大家提，没意见我就用这个了。

## 仓库

- 远端：`https://github.com/YushengLiSam/AAALion-`（@雨晟 你授权我了，我已经 push 了 `main` 和 `shufeng` 两个分支）
- 本地路径：`~/Desktop/rag/AAALion-/`
- A100 上路径：`uc:~/shufeng/AAALion-/`（雨晟 / 图杰：如果你们用 A100，**不要碰** `~/shufeng/cuda-fuzzing/`，那是我另一个项目）

## 仓库结构

```
client/    iOS 客户端 (SwiftUI)        ← 我（澍枫）负责
server/    FastAPI 后端                ← 雨晟负责
rag/       检索 / embedding / 评测     ← 图杰负责
data/      seed/（100 条已解压）+ extra/（gitignored）
docs/      架构、流水线、政策、路线图等全套文档
meetings/  会议纪要
tools/     截屏助手 + ssh 助手 + mock 后端
```

## 关键决策（征求意见）

1. **客户端选 iOS**（不再 Android）。我已经搭了 SwiftUI 17+ 的骨架，含聊天界面、SSE 流式解析、商品卡片、详情页跳转。
2. **后端 FastAPI + SSE**。骨架包含 `/health`、`/chat/stream`、`/products/{id}`，已有 fixture 流可跑通端到端冒烟。
3. **向量库用 Qdrant**（Chroma 作为 fallback 文档了）。原因：Qdrant 的 payload filter 更灵活，多模态拼接也更顺。docker-compose 一行起。
4. **大模型用 Doubao-Seed-2.0-lite**（比赛提供）。**但是注意**：PDF 里给的 API Key 我测试了一下返回 401 "key doesn't exist"，可能是占位符或者过期。请雨晟跟组织方确认一下真实 Key，发到群里我们写进 `.env`。
5. **加分项专攻 2 个**（PDF 原话"做精一项胜过浅尝三项"）：
   - 4.3 多轮 + 反选/排除 + 多商品对比
   - 4.2 拍照找货（CLIP on A100）
   - 不做：购物车 / 下单 / 语音 / TTS（除非 6/3 前主链路稳定）

## 数据

- 100 条 seed 数据已经解压在 `data/seed/`，分四类：美妆护肤、数码电子、服饰运动、食品生活。
- **重点**：组织方确认这批数据是 AI 生成的，演示不能直接用，要换真实数据。`docs/DATA.md` 里我写了三套思路：
  1. Perplexity 搜真实数据集（HuggingFace / Kaggle / GitHub）— 有现成 prompt
  2. Gemini 帮忙做格式归一化
  3. 兜底：我们三人手动整理 50 条真实商品（4 小时三人分摊）

## 我已经做完的事

仓库初始化全套：

- iOS 骨架：`AAALionAppApp`、`ChatView`、`MessageBubbleView`、`ProductCardView`、`ProductDetailView`、`ChatViewModel`（@Observable）、`ChatService`（手写 SSE on `URLSession.bytes`）。
- 后端骨架：FastAPI app、三个 route、`DoubaoClient` 用 openai SDK 兼容写法（key 一填就通）、`stub_top_k` 关键词兜底检索。
- RAG 骨架：`chunk.py`（真）、`embed_text.py` / `embed_image.py`（待图杰接 Doubao embedding + CLIP）、`retrieve/query.py`（关键词 fallback，待图杰换 Qdrant）、`prompts/system.md`（反幻觉系统提示词模板）、`eval/golden.jsonl`（10 条种子用例）。
- 截屏助手：`tools/screenshot_watcher.py`，shift+ctrl+cmd+4 之后会在 `screenshots/` 自动落一份。
- 文档：`docs/` 下完整 9 份（架构 / 流水线 / 硬件 / 政策 / 路线图 / 数据 / API / 加分项 / 执行总结）。雨晟图杰**强烈建议先看 `docs/EXECUTION_SUMMARY.md`**，5 分钟读完知道全貌。
- 政策：`docs/POLICY.md` 是团队共享规则；`docs/POLICY_LOCAL.md` 是 gitignored 的私人规则。
- 自动化：`Makefile`（`make backend` / `make ingest` / `make ios` 等）、`xcodegen` 配置（`client/AAALionApp/project.yml`）、mock 后端（`tools/mock_backend.py` — iOS 完全离线开发用）。
- A100：`~/shufeng/AAALion-/` 已 rsync 过去，264 个文件。`cuda-fuzzing/` 没动过我手动确认了。`nvidia-smi` 报驱动版本不匹配，跑 CLIP 前要处理一下，不急。
- Commit 格式：从今天起用 Conventional Commits（`feat(scope): xxx` / `fix(scope): xxx` / `docs(scope): xxx` 等）。详见 `docs/POLICY.md`。
- 重大 commit 记录：每个重大 commit 在 `docs/commits/` 下有一份 markdown 记录（包括动机、改动、过程、验证、后续）。

## 你们的 TODO（雨晟 / 图杰）

非常清晰的入口在各自模块的 README：

**雨晟**：`server/README.md` + `server/app/services/doubao_client.py` + `server/app/routes/chat.py`
1. 跟组织方确认真实 Doubao API Key，发我私聊或群里。
2. `services/doubao_client.py` 我已经用 openai SDK 兼容写法写好了流式调用；key 填进 `.env` 就通。
3. `routes/chat.py` 已经有"key 存在就走真模型，不存在就走 fixture"的分支逻辑，你接管后看下 prompt 和编排逻辑要不要调。
4. 接 RAG：现在用的是关键词 fallback (`stub_top_k`)，等图杰的 Qdrant 接好就换。

**图杰**：`rag/README.md`
1. Qdrant docker 起来（`cd server && docker compose up -d qdrant`）。
2. 接 Doubao embedding（key 同 Doubao 一个）到 `rag/ingest/embed_text.py`。
3. 把 `rag/retrieve/query.py` 从关键词 fallback 换成真正的 Qdrant search + payload filter。
4. CLIP 图像 embedding 等 A100 驱动修好再上，不急。
5. 把 `rag/eval/golden.jsonl` 从 10 条扩到 30+，自己跑 `python -m rag.eval.run` 看 recall@5。

## 风险 & 兜底

我跟两位说一下我的判断和心理准备：
- 时间紧（19 天），三个人各扛一段最理想；
- 但**我自己也准备好兜底所有部分单人做完**（详见 `docs/SOLO_DEV_PLAN.md`），所以你们如果有时间就接，没时间也提前说，不要 silently slip；
- 我会在 5/25、5/26 节点 check 一下，谁的部分到点没动我就接手，不会让链路卡住。

## 重点速读 TL;DR

| 项目 | 状态 |
|---|---|
| 仓库初始化 | ✅ done，已 push |
| iOS 骨架 | ✅ done，待生成 .xcodeproj 跑起来 |
| 后端骨架 + fixture | ✅ done，可跑 |
| RAG 关键词 fallback | ✅ done，待换真 Qdrant |
| 文档全套 | ✅ done |
| A100 命名空间 | ✅ done |
| Doubao API Key | ❌ PDF 给的不能用，需要雨晟确认真实 Key |
| 真实数据 | ❌ 需要全员一起搞，建议 6/1 前完成 |
| CLIP 图像索引 | ❌ 等 A100 驱动修好 |
| iOS .xcodeproj | ❌ 我下一步生成 |
| 第一次端到端 demo | 🎯 目标 5/28 |

## 我需要你们做的事（具体）

1. **雨晟**：明早能不能问下组织方 Doubao 真实 API Key？PDF 上发的 Key 已确认被泄露作废（见群里 Shida 老师通知），等新 Key 下来。在等的期间后端已切到 Anthropic Claude 作为占位 provider，能正常出 SSE 流式回复。
2. **图杰**：clone 下来跑一遍 `make ingest`（会用 fallback 跑通），熟悉 chunk 出来的内容，然后告诉我 Qdrant 起容器你这边要几天。
3. **两位**：用你们的 AI 工具读一遍 `docs/EXECUTION_SUMMARY.md` 和 `docs/ARCHITECTURE.md`，反馈你们对架构 / 接口 / 流程的意见，越早越好。
4. **两位**：我们什么时候开第一次同步会？我建议本周日（5/24）晚上 30 分钟视频，把架构 + Doubao Key + 数据来源三件事敲定。

辛苦了，有问题群里 at 我。
