# 实现指南 — 狮选 LionPick

> 写给所有初次接触本仓库的人(队友、评委、未来的 Shufeng)的单页索引。本页**不重复**其他文档的内容——而是按主题、按意图链接过去。

## 狮选 LionPick 是什么

一款为字节跳动 2026 AI 全栈挑战赛打造的原生 iOS 导购助手应用。用户用文字描述(或拍照)商品需求;应用从向量索引中检索真实商品,调用以检索到的商品目录为事实依据(grounded)、具备视觉能力的 LLM,并将推荐结果以文本 + 可点按商品卡片的形式流式返回。

## 60 秒看懂架构

```
 ┌────────────┐  text+image   ┌──────────┐   text   ┌──────────────┐
 │  iOS app   │ ─────────────►│ FastAPI  │ ───────► │  Chroma text │
 │ (SwiftUI)  │                │  /chat   │          │  1082 chunks │
 │ Speech /   │ ◄────SSE──────│  /stream │ ◄───────  └──────────────┘
 │ AVSpeech / │                └──────────┘   image   ┌──────────────┐
 │ Photos /   │                     │ ▲     ────────► │ Chroma image │
 │ Camera /   │                prompt│ │ deltas       │   100 vectors│
 │ Files      │                     ▼ │     ◄───────  └──────────────┘
 └────────────┘             ┌─────────────────┐                ▲
                            │  TokenRouter:   │           CLIP │ via OpenCLIP
                            │ claude-haiku-4-5│           on A100
                            └─────────────────┘
```

深入内容见 [`ARCHITECTURE.md`](ARCHITECTURE.md)。

## 子系统地图

| 子系统 | 负责人 | 关键文件 | 深入文档 |
|---|---|---|---|
| iOS 聊天界面 | 陈澍枫 | `client/AAALionApp/AAALionApp/Views/ChatView.swift` + `MessageBubbleView.swift` | [`IOS_SETUP.md`](IOS_SETUP.md) |
| iOS 视图模型 + 状态 | 陈澍枫 | `client/.../ViewModels/ChatViewModel.swift` (@Observable) | — |
| iOS 网络层(SSE) | 陈澍枫 | `client/.../Services/ChatService.swift` | [`API.md`](API.md) |
| iOS 图像输入(3 个来源) | 陈澍枫 | `Views/CameraPicker.swift` + PhotosPicker + .fileImporter | — |
| iOS 语音(输入 + 输出) | 陈澍枫 | `Services/SpeechService.swift`(zh-CN ASR)+ `Services/TTSService.swift` | — |
| iOS 设置 | 陈澍枫 | `Views/SettingsView.swift`(后端 URL 经 UserDefaults 持久化) | — |
| iOS 主题 | 陈澍枫 | `Views/Theme.swift` + `client/AAALionApp/design-tokens.json` | — |
| 后端 SSE 路由 | 李雨晟 | `server/app/routes/chat.py`(文本 + 多模态) | [`API.md`](API.md) |
| 后端就绪检查 | 管图杰 | `server/app/services/retrieval_readiness.py` + `/ready` + `Dockerfile.rag` | [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) |
| LLM 供应商抽象层 | 李雨晟 | `server/app/services/llm_provider.py`(TokenRouter / Anthropic / Doubao / OpenAI / Echo) | [`POLICY.md`](POLICY.md) §"Secrets" |
| 后端缓存 | (拟议) | `server/app/services/cache.py`(已写好;接线暂缓) | [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) |
| 文本 RAG | 管图杰 | `rag/retrieve/constraints.py` + `query.py` + `hybrid.py` + `server/app/services/constraint_state.py`(bge-small-zh-v1.5、BM25、有状态约束过滤) | [`ARCHITECTURE.md`](ARCHITECTURE.md) §"3. RAG" |
| 图像 RAG(CLIP) | 管图杰 | `rag/ingest/embed_image.py`(OpenCLIP ViT-B/32)+ `rag/ingest/run_image.py` | [`HARDWARE.md`](HARDWARE.md) §"A100" |
| 提示词(Prompt) | 管图杰 | `rag/prompts/system.md` | — |
| 评测 | 管图杰 | `rag/eval/golden.jsonl` + `rag/eval/report.py`(68 例看板) | [`EVAL_RESULTS.md`](EVAL_RESULTS.md) |
| 种子数据(100 件商品) | 管图杰 | `data/seed/{1..4}_<category>/data/*.json` + `images/*.jpg` | [`DATA.md`](DATA.md) + [`research/`](research/) |
| 工具链 | 陈澍枫 | `Makefile` + `tools/aaalion` + `tools/check-secrets.sh` | — |
| A100 SSH 工作流 | 陈澍枫 | `tools/ssh_a100.sh` | [`HARDWARE.md`](HARDWARE.md) |

## 构建与运行流程

```bash
# 5 commands from fresh clone to running app on simulator
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example server/.env   # set TOKENROUTER_API_KEY
aaalion ingest && aaalion backend &
until curl -fsS http://127.0.0.1:8000/ready; do sleep 1; done # wait before chat
aaalion ios-sim               # builds + installs + launches
```

iPhone 真机部署与每周重签节奏:见 [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)。

A100 上的 CLIP 图像索引构建(已完成;仅在数据集变更时一次性执行):
```bash
ssh uc
cd ~/shufeng/AAALion-
source .venv/bin/activate
CHROMA_TELEMETRY=False python -m rag.ingest.run_image
# (back on Mac:) rsync -az uc:~/shufeng/AAALion-/data/.chroma/ data/.chroma/
```

## 实现时间线(3 个回合,3 段讲完)

**第 1 轮(2026-05-22 夜间,[`commits/20260522-001..003`](commits/))**:从空白工作区到完整仓库脚手架。iOS 骨架(SwiftUI、MVVM、手写 SSE),带 fixture 流的 FastAPI 脚手架,含分块器与检索桩(stub)的 RAG 模块,提取出 100 件商品的种子数据集,写成 9 篇文档,tools/screenshot_watcher.py,配置好 git 远端,A100 命名空间设在 `~/shufeng/AAALion-/`(与 `cuda-fuzzing/` 分开)。

**第 2 轮(2026-05-22 晚间至 2026-05-23 凌晨,[`commits/20260522-004..009`](commits/))**:使用 `bge-small-zh-v1.5` 向量真正灌入 Chroma(992 个分块)。多供应商 LLM(TokenRouter / Anthropic / Doubao / OpenAI / Echo)。多模态载荷(Pydantic v2 内容联合类型)。在 iPhone 17 Pro 模拟器上完成端到端验证。iPhone 13 Pro 真机部署(Personal Team 经 `V8KDBHKA3P` 签名)。局域网联网 bug 暴露并修复(`Config.swift` 局域网 URL + uvicorn 0.0.0.0)。录制 6 个演示并附结论。3 份 Perplexity 调研产出归入 `docs/research/`。诚实评估:公开渠道没有可用的真实中文电商数据集。

**第 3 轮(2026-05-23 上午,[`commits/20260522-010`](commits/) 及之后)**:UX 打磨 + A100 真正用上。设置页通过 `UserDefaults` 持久化后端 URL。编辑/复制/朗读上下文菜单。在相册之外新增相机与文件附件入口。语音输入采用 `Speech.framework`。TTS 采用 `AVSpeechSynthesizer`。Claude 设计的暖象牙白 + 琥珀金主题。应用图标由 TokenRouter `openai/gpt-5.4-image-2` 生成。**A100 CUDA 以 cu124 torch 跑通(未动系统驱动)**;100 张商品图用 OpenCLIP 在 <10 秒内完成嵌入;后端接通图像优先检索。`docs/demos/2026-05-23/` 新增 3 张演示截图。`RUBRIC_MAPPING.md` 逐条记录 PDF §4 的每个子项。

## 下一步看哪里(按任务导向)

| 如果你想…… | 从这里开始 |
|---|---|
| 更换 LLM 模型 | `server/app/services/llm_provider.py` + `.env.example`([`POLICY.md`](POLICY.md) §"Secrets") |
| 新增一个商品类目 | [`DATA.md`](DATA.md) §"Schema" + [`research/`](research/) 了解有哪些可用数据 |
| 接入热门查询缓存 | `server/app/services/cache.py`(已写好)+ [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) 第 3 项 |
| 排查 iPhone 问题 | [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) ——未受信任的开发者(Untrusted Developer)、局域网联网、文件选择器、证书过期等 |
| 复现 iPhone 部署 | [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)(从全新克隆到完成 45 分钟) |
| 准备答辩(6/11) | [`RUBRIC_MAPPING.md`](RUBRIC_MAPPING.md) + [`demos/`](demos/) + [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) |
| 理解某个决策 | [`HONEST_ANSWERS.md`](HONEST_ANSWERS.md) + [`commits/`](commits/) 记录文件 |
| 规划下一轮迭代 | [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) ——目前等待团队评审 |
| 新队友上手 | 本文件 → [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) → [`PIPELINE.md`](PIPELINE.md) → `client/server/rag/` 中各领域的 README |

## 约定

- **提交**:遵循 Conventional Commits 规范(`<type>(<scope>): <summary>`)。规则见 [`POLICY.md`](POLICY.md)。
- **重大提交配套一份记录文件**,放在 `docs/commits/<YYYYMMDD>-<NNN>-<slug>.md` 下。规则见 `docs/POLICY_LOCAL.md`(该文件被 gitignore;记录文件本身会提交)。
- **密钥保持在仓库之外**:`~/.config/lionpick/credentials.env` + `server/.env`(两者均被 gitignore)。预提交钩子 `tools/check-secrets.sh` 扫描 ARK/Anthropic/OpenAI 密钥形态。
- **A100 边界**:所有命令均在 `~/shufeng/AAALion-/` 下运行。绝不 `cd` 进 `~/shufeng/cuda-fuzzing/`(另一个进行中的项目)。硬性规则见 [`HARDWARE.md`](HARDWARE.md)。

## 答辩就绪状态清单

- [x] 端到端链路跑通(演示见 `docs/demos/2026-05-23/`)
- [x] iPhone 部署已验证
- [x] 抗幻觉证据(`02-conditional-filter.md`)
- [x] 多模态(视觉 LLM 与 CLIP 均已接入)
- [x] 语音输入 + 语音输出
- [x] 多轮对话 + 否定 + 比较
- [x] 评分细则映射已成文
- [ ] 真实商品数据(手工精选 5-10 件;[`research/`](research/) 解释了为何没有现成可用的)
- [ ] 缓存接入(`services/cache.py` 已就绪)
- [ ] 演示视频录制(3-5 分钟)
- [ ] 答辩幻灯片
- [ ] 遵守每周证书重签节奏(当前证书约 2026-05-29 过期)

未完成项正是 [`PROPOSAL_2026-05-24.md`](PROPOSAL_2026-05-24.md) 的核心内容——请团队各位发表意见。
