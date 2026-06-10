# 评分标准映射 — ByteDance §4/§7 → 代码/产出物

本文将 ByteDance 2026 AI 全栈挑战赛评分标准中的每一项,显式映射到本仓库中的
具体产出物,并标注**验证状态**(已编码 vs. 实机验证)。可在答辩问答中直接引用。
最近更新:**R10 (2026-06-01)** — 反映了云端部署、购物车闭环深度(4.1)
以及延迟/可观测性工作(4.4)。

> 验证状态图例:**✅ 实机验证** = 本轮已在运行中的系统(云端或真机)上实际
> 跑通 · **✅ 已编码** = 已实现且可构建,本轮未重新演示 · 🟡 部分完成 ·
> ⏳ 暂缓(附理由)。

---

## 评分维度 (§7.1)

### 基础功能完整性 (35%)

| 子项 | 状态 | 产出物 / 证据 |
|---|---|---|
| 客户端对话 | ✅ 实机 | `client/.../Views/ChatView.swift` — SwiftUI 原生实现,可运行于 iPhone 13 Pro 真机 + 模拟器 |
| 后端 RAG 检索 | ✅ 实机 | 混合检索:稠密向量(bge-small-zh)+BM25 → RRF → 交叉编码器(cross-encoder)重排;`rag/retrieve/{hybrid,bm25,rerank,query}.py`;Chroma `products_text`(1082 个分块)。多轮品类/品牌/人民币价格过滤:`services/constraint_state.py` + `contextual_query.py` |
| 模型生成 | ✅ 实机 | `services/llm_provider.py` — TokenRouter `claude-haiku-4-5`(多模态,OpenAI 兼容);改一个环境变量即可切换到 Doubao/方舟 |
| 流式返回 | ✅ 实机 | SSE 实现于 `routes/chat.py`;事件类型:cart_intent / product_card / delta / claim_summary / done;iOS 端解析见 `Services/ChatService.swift` |
| 商品卡片展示 | ✅ 实机 | `Views/ProductCardView.swift`(国旗角标、加购胶囊按钮、**收藏爱心**),相对 URL 解析见 `Models/ProductCard.swift` |

### 工程质量 (25%)

| 子项 | 状态 | 产出物 / 证据 |
|---|---|---|
| 代码结构清晰 | ✅ | `client/ server/ rag/ docs/` 目录分层清晰;`docs/ARCHITECTURE.md` |
| 接口设计合理 | ✅ | `docs/API.md`;Pydantic v2 内容联合类型(content-union)schema;SSE 事件分类有完整文档 |
| 错误处理完善 | ✅ 实机 | SSE 错误事件 + 重试/退避(`_stream_chat_with_retry`);iOS 错误横幅;模型预热完成前由 `/ready` 门禁拦截聊天请求;echo provider 兜底 |
| **部署 / 运维** | ✅ 实机 | **GCP VM + systemd**,通过 Cloudflare tunnel 提供公网 HTTPS;**持续部署(CD):push 到 main 后约 2 分钟自动部署,含 `/ready` 检查 + 自动回滚**(`tools/cloud-autodeploy.sh`;发现并修复一次误回滚 bug 后,ready 等待窗口加固至 150 s)。已验证零代码漂移(线上部署 == origin/main) |
| **可观测性** | ✅ 实机 | `GET /cache/stats` 同时暴露**两层**缓存的命中率(响应缓存 + 检索缓存);iOS 设置面板可视化展示 |
| 文档齐全 | ✅ | `docs/` 目录:ARCHITECTURE、PIPELINE、DEPLOY_GUIDE、TROUBLESHOOTING、本映射文档、COMPETITIVE_ANALYSIS、PROPOSAL、DEV_LOG |

### 效果与可靠性 (20%)

| 子项 | 状态 | 产出物 / 证据 |
|---|---|---|
| 运行流畅 | ✅ 实机 | 云端后端在线运行;缓存命中时商品卡片 1 秒内流式呈现(实测) |
| 界面美观 | ✅ | Claude 设计的设计令牌(`design-tokens.json`)、暖象牙白主题、生成的狮子图标、SF Pro Rounded 字体、骨架屏加载、弹簧微交互 |
| 检索准确率 | ✅ 实机 | 黄金评测集(59 例)**recall@5 = 0.964 / MRR = 0.817**(全召回配置);线上部署的延迟优化重排参数下为 **0.941 / 0.816**。运行:`python -m rag.eval.run` |
| 无幻觉输出 | ✅ 实机 | `routes/chat.py` 中的 `_PROMPT` 强制仅基于商品目录作答;逐条断言的 `[目录✓]/[推断?]` 溯源标记直接渲染在气泡内;诚实的"无匹配"路径 |
| 复杂场景处理 | ✅ 实机 | 反选排除(`除了耐克`→0 条 Nike 商品,实机)、商品对比(markdown 表格,实机)、多轮相对细化(`再便宜点`→同品类均价 ¥362→¥238,实机) |

---

## 加分项 (20%, §4)

### 4.1 业务闭环深度 (购物车与下单) — **全部层级已交付**

| 层级 | 状态 | 证据 |
|---|---|---|
| ⭐ 对话式加购 | ✅ 实机 | `_detect_cart_intent` 加购路径 → `cart_intent` SSE 事件 → iOS `CartStore.add`;每张卡片均有内联加购 + 胶囊按钮 |
| ⭐⭐ 购物车管理 | ✅ 实机 | **对话式改数量** `把数量改成2`/`第二个改成3个`(`_parse_set_quantity`→`CartStore.setQuantity`,已在云端实机验证)+ **对话式删除** `删掉第二个`(`_REMOVE_FROM_CART`+序数词)+ 滑动删除 + 数量步进器 |
| ⭐⭐⭐ 下单确认流程 | ✅ | `Views/CheckoutView.swift` — 地址确认 + 逐项明细汇总 + 人民币总价 + 模拟"下单完成";聊天中的 cart_intent `checkout` 可直接打开该页面 |

### 4.2 多模态交互能力

| 层级 | 状态 | 证据 |
|---|---|---|
| ⭐ 语音输入 (ASR) | ✅ | `Services/SpeechService.swift`(Speech.framework zh-CN,实时部分结果,静音自动停止)+ 麦克风按钮 |
| ⭐⭐ TTS 语音播报 | ✅ | `Services/TTSService.swift`(AVSpeechSynthesizer zh-CN)+ 朗读菜单 |
| ⭐⭐⭐ 拍照找货 | ✅ 实机 | 基于 `products_image`(145 个向量)的 **CLIP ViT-B/32 图到图(image→image)**检索 — 已在云端实机验证(Nike T 恤照片 → 精确匹配 1.000 + 相似商品)。图片**同时**送入多模态 LLM 做属性锚定(attribute grounding)。`rag/retrieve/query.py:query_image`、`rag/ingest/embed_image.py` |

### 4.3 对话智能与 RAG 增强

| 层级 | 状态 | 证据 |
|---|---|---|
| ⭐ 多轮上下文记忆 | ✅ 实机 | `build_conversation_filter` + `build_retrieval_query`;`再便宜点的呢` 会继承品类并降低均价(已实机验证) |
| ⭐⭐ 反选与排除 | ✅ 实机 | `rag/retrieve/negation.py`(不要/除了/不含/排除);**黄金集反选准确率 = 1.000**;`除了耐克` → 特步/安踏/阿迪,0 条 Nike(实机) |
| ⭐⭐⭐ 多商品对比决策 | ✅ 实机 | `_is_comparison_query` + 系统提示词中的表格输出指令(价格/成分/场景/优劣势);`对比防晒霜` → markdown 表格(实机) |

### 4.4 工程质量与性能优化

| 层级 | 状态 | 证据 |
|---|---|---|
| ⭐ 热门查询缓存 | ✅ 实机 | **两层缓存**:响应缓存(`services/cache.py`,TTL 600s)+ **检索缓存**(`rag_client._heavy_retrieve` 记忆化,TTL 300s)。重复查询实测 17.9s→**0.3s**;两层命中率均可在 `/cache/stats` 查看 |
| ⭐⭐ 首屏极速响应 | 🟡→✅ 实机 | **卡片优先管线**(商品卡片先于 LLM 文本输出 — 纯顺序调整,召回不变)+ 复用 provider 连接 + 重排参数调优(提速 3.8×)。首屏:**缓存命中 0.3s / 冷启动 0.14–2.2s**,比 LLM 文本提前约 1s。严格的 1 秒内 LLM 首 token 在热/缓存场景下达成;冷启动下限受上游 LLM 制约(CPU VM 的诚实极限) |
| ⭐⭐⭐ 端侧体验打磨 | ✅ 实机 | **骨架屏(skeleton)**微光占位、**收藏 ❤️** 爱心带弹簧回弹 + 触感反馈(UserDefaults `FavoritesStore`)、**滑动**购物车滑动操作(删除/收藏);18 处 `withAnimation`、弹簧动画、scaleEffect、触感反馈 |

---

## 减分项 (§7.3) — 自查

| 风险 | 状态 | 应对 |
|---|---|---|
| AI 编造不存在的商品 | ✅ 已规避 | `_PROMPT` 仅限商品目录作答 + 逐条断言溯源标记;诚实的"无匹配" |
| 使用纯 Web/H5 替代原生 App | ✅ 已规避 | SwiftUI 原生 iOS 17+,可运行于 iPhone 13 Pro 真机(Personal Team 签名) |
| Demo 无法正常运行 | ✅ 已加固 | **云端后端**(不依赖笔记本)+ CD 自动回滚;演示前预热提前缓存查询 → 1 秒内响应。**风险提示**:公网 URL 为 Cloudflare quick-tunnel(运行期间稳定;升级为 named-tunnel 是唯一待办项) |
| 完全依赖 AI 生成而无法解释原理 | ✅ 已规避 | 本映射文档 + `docs/ARCHITECTURE.md` + `docs/COMPETITIVE_ANALYSIS_2026-05-30.md` 解释了每一项设计决策 |

---

## 复现步骤(供评委参考)

```bash
git clone https://github.com/YushengLiSam/AAALion-.git && cd AAALion-
brew install xcodegen
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
cp .env.example server/.env          # set TOKENROUTER_API_KEY
aaalion ingest                       # 1082 text chunks + 145 image vectors
aaalion backend &                    # http://localhost:8000  (or use the live cloud URL)
aaalion ios-sim                      # iPhone 17 Pro simulator
python -m rag.eval.run               # recall@5 / MRR / negation-accuracy table
```

答辩流程:运行实机演示场景,每个场景对应说明其覆盖的评分项;展示
`/cache/stats` 讲延迟优化的故事;展示黄金评测表讲准确率的故事。
