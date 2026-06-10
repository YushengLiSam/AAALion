<!-- 头部使用表格布局，让图标与标题/副标题各占一格、互不重叠。
     （此前用右浮动 <img> 时，副标题的引用竖条会在 GitHub 上压到图标。） -->
<table border="0">
  <tr>
    <td width="150" valign="middle" align="center">
      <img width="130" src="client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png" alt="狮选 LionPick app icon"/>
    </td>
    <td valign="middle">
      <h1>狮选 LionPick</h1>
      <b>基于 RAG 的多模态电商智能导购 AI Agent</b>
      <br/><br/>
      团队：<b>AAALion</b> · 比赛：ByteDance 2026 AI 全栈挑战赛
      <br/>
      代码冻结：2026-06-10 · 答辩：2026-06-11 → 2026-06-19
    </td>
  </tr>
</table>

狮选 LionPick 是一款移动端的智能导购 Agent：iOS 原生客户端 + FastAPI 流式后端 + 向量检索 + 多模态大模型。用户可以用文字、语音、相机或图片描述需求，Agent 基于真实商品库进行多轮对话推荐，杜绝幻觉。

LionPick 是一款原生 iOS 购物助手。FastAPI 后端**已部署至 GCP 云端虚拟机，并配有推送到 `main` 即触发的持续部署**——通过 SSE 流式返回回复，从向量索引（Chroma + `bge-small-zh-v1.5` + OpenCLIP ViT-B/32）中检索真实商品，并经由 TokenRouter 调用具备视觉能力的大模型，做有据可依的生成。支持多轮对话、否定/排除（反选）、商品对比、拍照找货、主动澄清反问、对话式购物车、语音输入与 TTS 播报。

> **初次接触本项目，或者不是工程背景？** 建议从通俗导览
> [`docs/explainers/README.md`](docs/explainers/README.md) 开始 ——
> 共 15 篇短篇专题讲解，面向只有计算机入门基础的读者，无需机器学习背景。

## 当前进展（Round 10 —— 云端部署 + 购物车深化 + 延迟/UX 打磨）

**后端现已运行在云端**（GCP 虚拟机，由 `systemd` 托管，经 Cloudflare tunnel 提供公网 HTTPS），并具备**持续部署**能力：推送到 `main` 后约 2 分钟自动完成部署，带 `/ready` 健康检查与失败自动回滚（`tools/cloud-autodeploy.sh`）。iOS App 默认指向云端，因此**演示不再依赖任何人的 Mac 处于开机状态**。

**检索核心指标（生产 Hybrid+Rerank 链路，R13 全量重测 2026-06-10）：92 例 canonical golden 集 recall@5 0.947、MRR 0.860、否定/反选准确率 1.000、无匹配正确率 0.952；61 例 compositional 难集（多轮/对比/组合）分数召回 0.832、二元召回 0.983、反选 1.000。** 数据与方法论见 [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)，看板见 [`docs/eval_report.html`](docs/eval_report.html)。运行 `python -m rag.eval.run` 可获得实时数据。

**R10 已交付内容（全部在云端 + iPhone 12 Pro Max / iPad Air 上实机验证）：**
- **4.1 购物车深化（完整）** —— 对话式加购、**修改数量**（"把数量改成2" / "第二个改成3个"）、**删除**（"删掉第二个"）、左滑删除，以及结算流程（地址 + 订单摘要 + 模拟下单完成）。
- **4.4 延迟优化** —— **首屏极速**：商品卡片在 LLM 文本*之前*先行流式返回（纯顺序调整，召回不变）→ 缓存命中时卡片约 **0.3s** 出现；**两层缓存**（响应缓存 + 检索记忆，重复请求 8s→0.3s），指标暴露在 `GET /cache/stats`；LLM provider 连接复用；重排（rerank）开销可通过环境变量调节。
- **4.4 端侧打磨** —— **骨架屏** shimmer 占位、带弹簧动效与触感反馈的**收藏 ❤️**、购物车**滑动**操作；此外回复支持真正的 **Markdown 渲染**（渲染出真实的表格/标题/加粗，而非裸语法）。
- **#5 主动反问** —— 当需求过于模糊、无法推荐时（"推荐个礼物" / "随便看看"），Agent 会**主动追问澄清**而不是瞎猜，并在输入框上方提供**可点选的快捷回复 chips**。
- **拍照找货上云** —— OpenCLIP 图到图检索（145 条商品图向量）现已运行在生产 VM 上，而非 A100。

自评得分：**约 93–94 / 100**。逐项评分映射见 [`docs/RUBRIC_MAPPING.md`](docs/RUBRIC_MAPPING.md)，各轮次开发叙述见 [`docs/DEV_LOG.md`](docs/DEV_LOG.md)。

### 逐轮进展

| 轮次 | 交付内容 | recall@5 | MRR |
|---|---|---:|---:|
| R3（2026-05-23） | 主题 + 图标 + 语音/TTS + 设置 + 相机 + A100 CLIP | — | — |
| R4（2026-05-23） | Files 导入器修复、README 打磨、IMPLEMENTATION_GUIDE | — | — |
| R5（2026-05-24 上午） | Hybrid+rerank + 购物车+结算 + 评审视角自评 | 0.711 | 0.695 |
| R6（2026-05-24 下午） | 45 个真实商品 + 溯源 UI + 趣味加载文案 + CLAUDE.md | 0.684 | 0.647 |
| R6.5（2026-05-25 上午） | Tujie：同义词 + 上下文多轮 + 价格意图合并入主线 | 0.816（31 例） | 0.705 |
| R7（2026-05-25 下午） | Sam 的评测看板合并 + 品牌产地否定修复 + 重录演示 | 0.746（59 例，审计前） | 0.674 |
| **R7 + golden 审计（2026-05-25，Tujie）** | **对照商品目录修正错误标注；重新生成看板** | **0.830（59 例 / 49 正例）** | **0.771** |
| R7.2（2026-05-25，Tujie 分支） | 海外商品按实时参考汇率展示 CNY + 感知 CNY 的预算过滤 | 0.830 | 0.778 |
| **R7.3（2026-05-25，已合并 main，当前）** | **R7.2 + 队友的否定/品牌产地审计合并** | **0.880** | **0.828** |
| **R7.4（2026-05-25，Tujie）** | **类目 / 品牌 / 人民币预算过滤在 dense 与 BM25 检索阶段生效** | **0.981（64 例）** | **0.846** |
| **R7.5（2026-05-25，Tujie）** | **多轮约束状态：预算与品牌过滤的继承 / 替换 / 取消** | **0.982（68 例）** | **0.856** |
| **R7.6（2026-05-25，Tujie）** | **Docker 模型预置（model bake）+ 启动检索预热 + `/ready` 就绪门控** | **0.982（68 例）** | **0.856** |
| R8（2026-05-25 晚，Shufeng） | 缓存命中率面板、多轮否定持久化、品牌产地 KR/DE/GB、Cloudflare Tunnel、开发者模式门控、语音静默自动停止、多附件（≤10） | 0.880 → 0.982（沿用） | 0.856 |
| **R9.A（2026-05-28，Shufeng）** | **Agent 化/可信层：话题切换重置 · 溯源标签 · "为什么推荐"卡片 · 语音加购 · 降价监控 · 对比/场景 · 跨语言品牌别名** | *UX 层 —— 检索持平* | — |
| **R10（2026-06-01，Yusheng）** | **云端部署 + CD（自动部署/回滚）· 4.1 对话式改数量/删除 · 4.4 首屏卡片先行 + 两层缓存 + `/cache/stats` · 端侧打磨（骨架屏/❤️/滑动）· Markdown 渲染 · #5 主动反问 + chips · CLIP 上云 · 图片路径修复** | 0.93–0.96（82 例） | — |

### 能力矩阵

| 能力 | 状态 | 负责人 | 佐证 |
|---|---|---|---|
| iOS 聊天界面 + SSE 流式输出 | ✅ | Shufeng | [`docs/demos/`](docs/demos/) |
| 经 TokenRouter 接入真实 LLM（claude-haiku-4-5） | ✅ | Shufeng | 全部演示 |
| 混合检索（dense + BM25 + 交叉编码器(cross-encoder)重排） | ✅ | Shufeng（R5） | [`rag/retrieve/`](rag/retrieve/) |
| **精选同义词扩展** | ✅ NEW | **Tujie（R6.5）** | [`rag/retrieve/synonyms.py`](rag/retrieve/synonyms.py) |
| **多轮查询 + 约束状态**（过滤条件的继承 / 替换 / 取消） | ✅ NEW | **Tujie（R7.5）** | [`server/app/services/contextual_query.py`](server/app/services/contextual_query.py) + [`constraint_state.py`](server/app/services/constraint_state.py) |
| **价格意图解析 + 排序**（"200元以下"、"便宜"） | ✅ NEW | **Tujie（R6.5）** | [`server/app/services/price_intent.py`](server/app/services/price_intent.py) |
| **海外价格 CNY 归一化**（最新参考汇率 + 保留原币价格可溯源） | ✅ NEW | **Tujie（R7.2）** | [`server/app/services/currency.py`](server/app/services/currency.py) |
| **约束感知检索**（类目 / 子类目 / 品牌 / 人民币预算） | ✅ NEW | **Tujie（R7.4）** | [`rag/retrieve/constraints.py`](rag/retrieve/constraints.py) + [`query.py`](rag/retrieve/query.py) |
| **Docker 就绪预热**（首位用户无需等待模型下载） | ✅ NEW | **Tujie（R7.6）** | [`Dockerfile.rag`](Dockerfile.rag) + [`retrieval_readiness.py`](server/app/services/retrieval_readiness.py) + [`/ready`](docs/API.md) |
| 否定 / 排除（4.3 ⭐⭐） | ✅ **已审计：准确率 1.000** | Shufeng + Yusheng | [`docs/demos/2026-05-25/03-negation.png`](docs/demos/2026-05-25/03-negation.png) + [`brand_origin.py`](rag/retrieve/brand_origin.py) |
| 多商品对比（4.3 ⭐⭐⭐） | ✅ | Shufeng | [`docs/demos/2026-05-25/05-compare.png`](docs/demos/2026-05-25/05-compare.png) |
| OpenCLIP 图到图检索（4.2 ⭐⭐⭐） | ✅ **已上云 VM** | Shufeng + Yusheng | 145 条图向量；`rag/retrieve/query.py:query_image` |
| 语音输入 + TTS（4.2 ⭐ + ⭐⭐） | ✅ | Shufeng（R3） | Speech / AVSpeechSynthesizer |
| **4.1 购物车 —— 完整**（加购 · 对话式**改数量** · **删除** · 滑动 · 结算） | ✅ **R10** | Shufeng + Yusheng | `_detect_cart_intent` + `CartStore` + `CheckoutView` |
| **4.4 首屏极速 + 两层缓存 + `/cache/stats`** | ✅ **R10** | Yusheng | 卡片先行重排 + `rag_client` 检索记忆 |
| **4.4 端侧打磨**（骨架屏 · ❤️ 收藏 · 滑动 · Markdown 渲染） | ✅ **R10** | Yusheng | `SkeletonCardView` · `FavoritesStore` · `MarkdownMessageView` |
| **#5 主动反问**（模糊查询 → 澄清 + 可点选 chips） | ✅ **R10** | Yusheng | `_needs_clarification` + `clarify` SSE 事件 |
| **云端部署 + CD**（自动部署 + 回滚） | ✅ **R10** | Yusheng | `tools/cloud-autodeploy.sh` |
| **趣味加载文案**（5-10s 等待体验） | ✅ NEW | Shufeng（R6） | [`client/.../Views/LoadingSentence.swift`](client/AAALionApp/AAALionApp/Views/LoadingSentence.swift) |
| **45 个真实商品 + 溯源 UI**（国内 + Amazon 美/日） | ✅ NEW | Shufeng（R6） | [`docs/research/2026-05-24-real-products.md`](docs/research/2026-05-24-real-products.md) |
| **延迟与缓存埋点** | ✅ | Shufeng（R5） | [`server/app/services/cache.py`](server/app/services/cache.py) |
| **评测看板（68 例审计/回归 golden 集，分场景，HTML）** | ✅ 多轮状态合入后已刷新 | Sam + Tujie | [`docs/eval_report.html`](docs/eval_report.html) + [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md) |
| iPhone 13 Pro 实机部署 | ✅ | Shufeng | 每周执行 `aaalion resign` |
| **多轮话题切换重置**（修复 sub_categories 污染） | ✅ NEW | Shufeng（R9.A） | [`server/app/services/rag_client.py`](server/app/services/rag_client.py) Path C + [`test_context_contamination.py`](server/tests/test_context_contamination.py) |
| **逐条断言溯源标签** `[目录✓]` / `[推断?]` + 单条消息计数 | ✅ NEW | Shufeng（R9.A） | [`server/app/routes/chat.py`](server/app/routes/chat.py) 提示词 + [`MessageBubbleView.swift`](client/AAALionApp/AAALionApp/Views/MessageBubbleView.swift) |
| **"为什么推荐这个"卡片**（dense / BM25 / RRF / rerank 分数 + 来源引用） | ✅ NEW | Shufeng（R9.A） | [`ProductDetailView.swift`](client/AAALionApp/AAALionApp/Views/ProductDetailView.swift) |
| **语音加购**（"加入购物车 / 结算" 直接触发购物车动作） | ✅ NEW | Shufeng（R9.A） | [`ChatViewModel.swift`](client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift) |
| **降价监控**（"提醒我降价" → SQLite 监控 + 提醒） | ✅ NEW | Shufeng（R9.A） | [`price_watch_db.py`](server/app/services/price_watch_db.py) + [`price_watch.py`](server/app/routes/price_watch.py) |
| **对比表格 · 场景/搭配组合 · 跨语言品牌别名** | ✅ NEW | Shufeng（R9.A） | [`chat.py`](server/app/routes/chat.py) + [`brand_origin.py`](rag/retrieve/brand_origin.py) |

> **R7.6 实测**：多轮检索可正确地持久化或取消过滤条件；Docker 构建已缓存文本/重排模型权重，FastAPI 在 `/ready` 返回成功前会完成一次端到端检索预热。详见 [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)。

---

## 团队

| 中文名 | 英文名 | 角色 | 模块 |
|---|---|---|---|
| 陈澍枫 | Shufeng Chen | 客户端 / iOS 负责人 · 项目兜底 | `client/` |
| 李雨晟 | Yusheng Li | 后端 | `server/` |
| 管图杰 | Tujie Guan | 检索 / RAG | `rag/` |

> 陈澍枫是项目负责人，也是项目兜底人。

## 技术栈

- **客户端**：Swift 5.9、SwiftUI、iOS 17+。Speech.framework + AVSpeechSynthesizer + PhotosPicker + UIImagePickerController + .fileImporter。
- **后端**：Python 3.12、FastAPI、SSE、Pydantic v2 多模态 content union。
- **汇率展示**：Frankfurter v2 最新参考汇率（免密钥；服务端缓存；保留原始来源价格）。
- **向量库**：Chroma 进程内运行。两个 collection：`products_text`（1082 个分块，经 `BAAI/bge-small-zh-v1.5` 嵌入）+ `products_image`（145 条向量，经 OpenCLIP ViT-B/32 嵌入）—— 均由云端 VM 提供服务。
- **LLM**：`claude-haiku-4-5`（具备视觉能力），经 TokenRouter 接入。通过 `LLM_PROVIDER` 环境变量可切换为 Doubao、OpenAI、Anthropic 或本地 echo。
- **部署**：GCP VM + `systemd`（`lionpick`、`lionpick-tunnel`、`lionpick-autodeploy.timer`）+ Cloudflare tunnel 提供公网 HTTPS。推送到 `main` → 约 2 分钟自动部署，带 `/ready` 检查 + 回滚（`tools/cloud-autodeploy.sh`）。
- **设计令牌（Design tokens）**：Claude 设计的暖象牙白 + 琥珀金 + 深咖啡配色（见 [`client/AAALionApp/design-tokens.json`](client/AAALionApp/design-tokens.json)）。

## 快速开始

```bash
# 1. 安装 aaalion 辅助命令(任意目录可用)
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"

# 2. 配置(密钥从 https://www.tokenrouter.com/console/token 获取)
cp .env.example server/.env
$EDITOR server/.env   # 设置 TOKENROUTER_API_KEY

# 3. 后端 + Chroma 文本索引
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
aaalion ingest                       # 1082 个文本分块;元数据变更后需重跑
aaalion backend                      # uvicorn 监听 0.0.0.0:8000

# 4. iOS 模拟器
aaalion ios-sim                      # 重新生成 .xcodeproj、构建、安装、启动

# 5. (可选) RAG 检索质量评测
python -m rag.eval.run               # 命令行: 7 项指标 × 3 种检索策略
python -m rag.eval.report            # HTML 看板 → docs/eval_report.html
```

### Windows 下的 Docker 部署（复制即用）

在仓库根目录运行下面这段 PowerShell。它会部署一个无需 API key、功能完整的
本地 RAG 后端：检索、过滤、汇率换算与商品卡片均为真实功能；只有回答生成
使用确定性的 `echo` provider。

```powershell
# 首次创建本地配置,然后显式选择免费的冒烟测试 provider。
if (-not (Test-Path server/.env)) { Copy-Item .env.example server/.env }
(Get-Content server/.env -Raw) -replace '(?m)^LLM_PROVIDER=.*$', 'LLM_PROVIDER=echo' |
  Set-Content server/.env -Encoding UTF8

# 构建带模型缓存的镜像,持久化 Chroma 文本索引,并启动服务。
docker compose -f server/docker-compose.yml down
docker compose -f server/docker-compose.yml build backend
docker compose -f server/docker-compose.yml run --rm --no-deps backend python -m rag.ingest.run
docker compose -f server/docker-compose.yml up -d

# 等待模型与完整检索链路预热完成。
do {
  Start-Sleep -Seconds 1
  try { $ready = Invoke-RestMethod http://127.0.0.1:8000/ready } catch { $ready = $null }
} until ($ready.status -eq "ready")
$ready
```

打开 `http://127.0.0.1:8000/docs` 即可测试 API。若要把正在运行的部署切换为
由 TokenRouter 生成真实回答，粘贴并运行下面这段；它会以不回显的方式提示
输入密钥，且密钥只保存在已被 gitignore 忽略的 `server/.env` 文件中。

```powershell
$secureKey = Read-Host "TOKENROUTER_API_KEY" -AsSecureString
$tokenKey = [System.Net.NetworkCredential]::new("", $secureKey).Password
$envText = Get-Content server/.env -Raw
$envText = $envText -replace '(?m)^LLM_PROVIDER=.*$', 'LLM_PROVIDER=tokenrouter'
if ($envText -match '(?m)^TOKENROUTER_API_KEY=') {
  $envText = $envText -replace '(?m)^TOKENROUTER_API_KEY=.*$', "TOKENROUTER_API_KEY=$tokenKey"
} else {
  $envText += "`r`nTOKENROUTER_API_KEY=$tokenKey`r`n"
}
Set-Content server/.env $envText -Encoding UTF8
Remove-Variable tokenKey, secureKey, envText
docker compose -f server/docker-compose.yml up -d --force-recreate backend
do {
  Start-Sleep -Seconds 1
  try { $ready = Invoke-RestMethod http://127.0.0.1:8000/ready } catch { $ready = $null }
} until ($ready.status -eq "ready")
$ready
```

首次构建会下载 embedding 与重排（reranker）模型权重并固化到 Docker 镜像中。
ingest 命令将 Chroma 索引写入宿主机的 `data/.chroma/`，因此容器替换后索引
依然保留。商品数据变更后请重新运行 ingest 命令。后端只有在 `/ready` 确认
embedding、BM25、重排以及一条完整检索链路均已预热后，才会接受聊天流量。

### 后端地址：App 指向哪里

`Config.swift` 中的 `defaultBackendURL` 是**线上云端隧道**（Cloudflare），
因此新安装的 App 在**任何网络（Wi-Fi 或蜂窝数据）下都可直接使用、无需任何
配置**，演示也不依赖某台 Mac 开机。`Config.swift` 按以下顺序解析：

| 场景 | 操作 | 持久化方式 |
|---|---|---|
| **云端（默认）** | 无需操作。内置的隧道 URL 在真机/模拟器上直接可用。 | 编译期默认值 |
| **本地后端（开发）** | 打开 App → ⚙ 设置 → 输入 `http://localhost:8000`（模拟器）或 `http://<你的 Mac 局域网 IP>:8000`（真机）→ 测试 → 保存 | UserDefaults，重启后仍生效 |
| **一次性测试** | Xcode → Edit Scheme → Run → Environment Variables → `PUBLIC_BACKEND_URL=http://…:8000` | 仅在通过 Xcode 调试时生效 |

> Cloudflare **quick-tunnel** 的 URL 在隧道进程运行期间保持稳定，但进程
> 重启后可能变化；变化时由 Yusheng 重新固化并广播。升级为 named-tunnel
> （永久域名）是目前唯一未关闭的运维事项。若使用本地后端，运行
> `aaalion backend`（绑定 `0.0.0.0` 而非 `127.0.0.1`）；Mac 的局域网 IP
> 用 `ipconfig getifaddr en0` 查询。

海外来源的商品保留其原始金额（例如 `$398.00 USD`），并按后端获取的最新可用参考汇率以人民币展示与合计。这只是购物展示层的换算，不是支付结算报价；商品卡详情中会展示汇率日期与数据提供方。

文本检索现在会在候选召回之前抽取类目、子类目、具名品牌与人民币预算约束。在多轮对话中，这些约束构成权威的会话状态：后续轮次可以继承、替换或取消品牌/预算限制，且不会被过期的锚定文本恢复。dense 与 BM25 检索使用同一套过滤；海外定价的商品先通过第一道价格闸门，换算为当前 CNY 后再做严格校验。设置 `RAG_HARD_FILTERS=0` 可对推断出的约束运行 A/B 基线对照。

评测看板（[`docs/eval_report.html`](docs/eval_report.html)）按场景（basic / filter / negation / multiturn / compare / no-match）拆解检索质量，报告 recall@5/10、MRR、precision@5、**反选准确率**（negation accuracy）、无匹配正确率与延迟。当前生产链路结果为 68 例上 **recall@5 0.982 / MRR 0.856 / 反选准确率 1.000**；9 例 `multiturn` 切片达到 **recall@5 1.000 / MRR 0.889**，4 例新增的 `constraint-state` 回归用例达到 **MRR 1.000**。在 Docker 预热不计入计时用例的前提下，平均检索延迟为 **610 ms**；此前一次未预热的运行为 **6156 ms**，原因是包含了一次性的模型加载离群值。方法论与实测细节见 [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)。

iPhone 实机部署见 [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md)；A100 上的 CLIP 图像索引见 [`docs/IMPLEMENTATION_GUIDE.md`](docs/IMPLEMENTATION_GUIDE.md)。

## 项目结构

```
client/    iOS 客户端 (SwiftUI, Speech, AVFoundation)  ← 陈澍枫
server/    FastAPI 后端 (SSE、多模态、缓存)             ← 李雨晟
rag/       数据入库 / 检索 / 提示词 / 评测 / CLIP       ← 管图杰
data/      seed/ (已提交) + .chroma/ (gitignore 忽略)
docs/      架构、流水线、政策、demos、research、proposals
meetings/  会议记录
tools/     aaalion + screenshot + check-secrets
```

## 接下来该读这些

| 文档 | 用途 |
|---|---|
| ⭐ [docs/README.md](docs/README.md) | **文档索引 —— 从这里开始** |
| [docs/IMPLEMENTATION_GUIDE.md](docs/IMPLEMENTATION_GUIDE.md) | 单页实现讲解 |
| 📓 [docs/DEV_LOG.md](docs/DEV_LOG.md) | 滚动开发日志 —— 最新交付在最上方 |
| 📋 [docs/ROADMAP.md](docs/ROADMAP.md) | 当前前瞻计划（直至代码冻结） |
| [docs/COMPETITIVE_ANALYSIS.md](docs/COMPETITIVE_ANALYSIS.md) | 狮选 vs 市场同类产品（基于网络调研） |
| 📊 [docs/QUALITY_REVIEW.md](docs/QUALITY_REVIEW.md) · [docs/EVAL_RESULTS.md](docs/EVAL_RESULTS.md) | 评审视角自评 · RAG 指标 |
| [docs/RUBRIC_MAPPING.md](docs/RUBRIC_MAPPING.md) | PDF §4 → 代码/产物映射（供答辩用） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/API.md](docs/API.md) · [docs/PIPELINE.md](docs/PIPELINE.md) | 设计 · 接口 · 开发 SOP |
| [docs/DEPLOY_GUIDE.md](docs/DEPLOY_GUIDE.md) · [docs/IOS_SETUP.md](docs/IOS_SETUP.md) | 队友环境搭建 · Xcode/签名 |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · [docs/HARDWARE.md](docs/HARDWARE.md) · [docs/POLICY.md](docs/POLICY.md) | 踩坑记录 · 设备/SSH · 团队规则 |
| [docs/DEFENSE_DECK_PROMPT.md](docs/DEFENSE_DECK_PROMPT.md) · [docs/explainers/](docs/explainers/) | 答辩幻灯片提示词 · 通俗讲解系列 |
| [docs/demos/](docs/demos/) · [docs/research/](docs/research/) · [docs/commits/](docs/commits/) | 演示截图 · 市场调研 · 变更记录 |

## 许可证

MIT —— 见 [LICENSE](LICENSE)。
