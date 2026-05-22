# WeChat 更新 — 2026-05-23

> 准备直接粘贴。三段：① 一句话进度 ② PDF 评分项 vs 现状 ③ 你们的 TODO。文档链接全部指向远端仓库。

---

@雨晟 @图杰 跟一下进度。22 号晚到 23 号凌晨我把 iOS + 后端 + RAG 全链路打通了，跑了 6 个 demo，截图都 push 到 `docs/demos/2026-05-22/`，仓库里就能看。

## 一句话进度

**端到端跑通：** SwiftUI 应用 → SSE → FastAPI → Chroma 检索 (`bge-small-zh-v1.5`, 992 条) → TokenRouter 上的 `claude-haiku-4-5`（视觉模型，多模态 OK）。模拟器 iPhone 17 Pro 验证通过；物理 iPhone 13 Pro 部署只差一次 Xcode 里点 Team。

## 对照 PDF 评分项

| PDF 维度 | 权重 | 我们做了什么 | 截图 / 证据 |
|---|---|---|---|
| 基础功能完整性 | 35% | 端到端链路完整：客户端对话 → 后端 RAG 检索 → 模型生成 → 流式返回 → 商品卡片展示 | [01-basic-recommendation](demos/2026-05-22/01-basic-recommendation.png) |
| 工程质量 | 25% | Conventional Commits、`docs/commits/` 重大提交记录、`tools/check-secrets.sh` 防泄露、`Makefile` + `aaalion` 全局工具、xcodegen 可复现项目、多 Provider LLM（回退到 echo 永不 500）| `docs/commits/`, `tools/`, `Makefile` |
| 效果与可靠性 | 20% | 反幻觉验证：200 元以下蓝牙耳机查询，模型诚实地说"目录中没有"而不是编造 | [02-conditional-filter](demos/2026-05-22/02-conditional-filter.png) |
| 加分项 4.1 (购物车) | 0% (out of scope) | — | — |
| 加分项 4.2 (多模态拍照找货) | ✓ 视觉 LLM 已通 | iOS PhotosPicker → base64 → backend → claude-haiku-4-5 (多模态) → 检索匹配商品。CLIP 图像索引留到 Round 3 | [06-photo-upload](demos/2026-05-22/06-photo-upload.png) |
| 加分项 4.3 (对话深度) | ✓ 全部覆盖 | 多轮对话 / 反选（"不要含酒精，不要日系"）/ 多商品对比，都跑通了 | [04-negation](demos/2026-05-22/04-negation.png), [05-comparison](demos/2026-05-22/05-comparison.png) |

完整 demo 索引：[`docs/demos/2026-05-22/README.md`](demos/2026-05-22/README.md)

## 关键决策与现状

1. **客户端 = iOS**（不是 Android）。SwiftUI 17+，xcodegen 管理 `.xcodeproj`，PhotosPicker 拿图，URLSession.bytes 解 SSE。模拟器 + 物理设备都能跑。
2. **后端 = FastAPI + SSE**，单一 `/chat/stream` 同时支持纯文本和多模态。`server/app/services/llm_provider.py` 把 Anthropic / TokenRouter / Doubao / OpenAI / echo 都接进来了。Doubao Key 一发到群里改 `LLM_PROVIDER=doubao` 就行。
3. **向量库 = Chroma 进程内**（不需要 docker，单人开发更轻）。文本 embedding 用 `BAAI/bge-small-zh-v1.5`，免费、本地、中文友好。Qdrant 也保留接口。
4. **数据**：组织方那份 100 条是 AI 生成的，我跑了 Perplexity 三个提示词，结论是**目前没有免费且可用的真实中文电商完整数据集**——所有公开数据集要么字段不全（缺价格/品牌/评价），要么匿名化（JDsearch），要么需要营业执照 / 大学认证。详见 [`docs/research/README.md`](research/README.md)。**策略**：用现在的 AI 生成 seed 作为流水线 demo，加手工整理 10-15 条真实 Tmall/JD 商品作为侧验证集。
5. **Doubao Key**：群里的旧 Key 已确认作废（被人提交到公开仓库被滥用），等组织方重发。在等的期间用 TokenRouter 上的 claude-haiku-4-5 顶着，跟最终模型替换是一行 env 变量。
6. **A100 (`uc`)**：建了 `~/shufeng/AAALion-/`，跟你们之前那个 `cuda-fuzzing/` 完全分开，那个文件夹一根头发都没动。`nvidia-smi` 报驱动版本不匹配，但我们决定**不碰系统驱动**——后面跑 CLIP 用 CPU torch 就好，零风险。

## 现在到 6/10 的路线图

| 时间 | 我（澍枫） | 雨晟 | 图杰 |
|---|---|---|---|
| 5/23–5/25 | iPhone 13 Pro 物理设备部署、提示词调优（demo 06 视觉匹配自信度） | 跟组织方要 Doubao Key；接入；写错误重试/退避 | OpenCLIP 跑 CPU torch on uc，做图像索引（不动驱动） |
| 5/26–5/30 | UI 打磨（骨架屏、动画、加购按钮 stub）| 缓存层；私有化部署 Dockerfile 验证 | golden eval 扩到 30+ 条，跑 recall@5 |
| 6/1–6/3 | demo 视频脚本初稿 | 流量/压力测试（4.4 加分项）| 多模态对齐：CLIP 图像向量 + 文本向量混合检索 |
| 6/6–6/8 | 答辩 PPT | 答辩问答演练 | 答辩问答演练 |
| 6/9–6/10 | Buffer / 提交 | Buffer | Buffer |

## 你们现在要做的事

1. **clone 仓库**：`git clone https://github.com/YushengLiSam/AAALion-.git && cd AAALion-`
2. **跟着 `docs/DEPLOY_GUIDE.md` 走一遍**，确认在你们各自的 Mac + iPhone (≥13) 上能跑起来。45 分钟，多数时间是 Xcode 和 Python 下载。有问题就群里 @ 我，今天我在线。
3. **雨晟**：明早跟一下组织方拿真实 Doubao Key。新 Key 拿到丢 `server/.env` 即可，不需要改代码。
4. **图杰**：先 clone + ingest 跑一遍熟悉 chunk 输出。然后看 `rag/ingest/embed_image.py`，那是 CLIP 图像 embedding 的 TODO，CPU torch 装好直接接进去，预计 100 张图 20 秒。**绝对不要动 A100 的 nvidia 驱动。**
5. **两位都**：用 AI 工具读一遍 `docs/PLAN_2026-05-22.md`（最新计划） + `docs/research/README.md`（数据现状），给我反馈架构 / 接口 / 数据策略上的意见。我对自己的判断有信心，但你们的二人视角能避免盲区。
6. **周日 5/24 晚 8 点同步会**，30 分钟，把三件事敲定：(a) Doubao Key 状态，(b) 真实数据策略（接受我手工 10-15 条 + AI seed 的做法吗），(c) 答辩分工。

辛苦。

---

## 故障排查 / 你们一定会遇到的坑

我把今天踩过的所有坑（iOS 签名、Team ID vs Cert ID、Untrusted Developer、7 天过期、Backend env 不读、TokenRouter 激活、Doubao Key 作废、nvidia 驱动等等）整理到了一个文件：[`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)。**部署前先扫一眼，遇到错误时直接搜关键词。**

最常见的两个：
1. **iPhone 装好 app 但点开提示 "Untrusted Developer"** → Settings → General → VPN & Device Management → Apple Development → Trust（5 步）。
2. **`make` 报 "No rule to make target ..."** → 你不在 repo 目录里。装 `aaalion` 全局命令：`ln -sf <repo>/tools/aaalion ~/.local/bin/aaalion`，然后从哪都能 `aaalion ios-sim` / `aaalion backend` 等。

3. **设备装好但点开 app 闪退/白屏** → 多半是 `PUBLIC_BACKEND_URL` 指错。手机和 Mac 同 Wi-Fi，Mac 上 `ipconfig getifaddr en0` 拿 IP，Xcode scheme env 里设 `PUBLIC_BACKEND_URL=http://<ip>:8000`，重装。

## 附：仓库重要路径速查

- 主仓库：`https://github.com/YushengLiSam/AAALion-`
- 最新计划：[`docs/PLAN_2026-05-22.md`](PLAN_2026-05-22.md)
- 6 个 demo：[`docs/demos/2026-05-22/`](demos/2026-05-22/)
- 数据调研：[`docs/research/`](research/)
- 部署指南：[`docs/DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)
- **故障排查 / FAQ**：[`docs/TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- 当下问题诚实交底：[`docs/HONEST_ANSWERS.md`](HONEST_ANSWERS.md)
- 我的兜底执行计划：[`docs/SOLO_DEV_PLAN.md`](SOLO_DEV_PLAN.md)
