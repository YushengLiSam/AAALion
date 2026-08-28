# 狮选 LionPick — 从零通读导览(给雨晟)

> 目标:按顺序走一遍,看完脑子里有**完整逻辑框架**,知道每个文件在整个系统里干啥、跟谁连。
> 这不是"挑重点"(那是 `DEFENSE_PREP.md §7`),这是**通读**。
>
> **贯穿全程的一条主线**(随时回到它):
> ```
> iOS 发一句话 → 后端【意图判断 → 约束解析 → 混合检索 → 重排 → 过滤 → 喂给LLM生成 → 流式回推】→ iOS 渲染
> ```
> 每读一个文件,问自己一句:"它在这条主线的哪一步?" 答得上来,就说明框架建起来了。
>
> **读法**:这套代码中文注释极全。每个文件**先读顶部 docstring 和函数注释**,再扫函数体,
> 看不懂的行直接跳过——你要的是"它干啥、跟谁连",不是逐行。
> **顺序很重要**:严格按"幕"的顺序,后面的依赖前面的理解。别跳。

---

## 第 0 幕:先看地图(纯文档,别碰代码)· 约 40 分钟

先在脑子里建立"系统长什么样",再看代码才不会迷路。

| 顺序 | 看 | 读完你知道 |
|---|---|---|
| 1 | `DEFENSE_PREP.md` §1(那张"一条 query 的旅程") | **整个系统的主线**——这是地图本身,务必先吃透 |
| 2 | `README.md` | 项目是啥、怎么跑起来的 5 条命令 |
| 3 | `docs/ARCHITECTURE.md` | 架构图 + 为什么这么设计(配合 DEFENSE_PREP §2 一起看) |
| 4 | `docs/explainers/01-what-is-rag.md` → `02` → `04` → `05` | 用大白话讲 RAG/检索/多轮/拍照,每篇 5 分钟,给非工程背景的人写的,最适合你打底 |

✅ **检查点**:不看任何东西,在白纸上默画那条主线的 7 步。画得出来 → 进第 1 幕。

---

## 第 1 幕:数据契约(看清楚"水管里流的是什么")· 约 30 分钟

代码是处理数据的,所以先看数据长什么样,后面看函数才有意义。

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `server/app/schemas/chat.py` | **iOS 发给后端的东西**:`ChatRequest`(messages 对话历史 / filters / user_id / language)。请求的"入参表" |
| 2 | `docs/API.md`(重点看 SSE 事件那节) | **后端回给 iOS 的东西**:`product_card`(商品卡)/`delta`(文字逐字)/`cart_intent`(购物车)/`clarify`(反问)/`claim_summary`(来源统计)/`done`。回包的"出参表" |
| 3 | `data/seed/1_美妆护肤/data/` 里**随便打开一个 .json** | **核心数据对象**:一个商品长啥样(标题/品牌/类目/sub_category/价格/provenance产地来源/rag_knowledge营销描述)。整个系统都在搬运这个东西 |

✅ **检查点**:你能说出"用户发一句话,后端最终吐回哪几种事件",以及"一个商品有哪些字段"。

---

## 第 2 幕:服务器怎么启动 · 约 25 分钟

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `server/app/main.py` | 入口。看三处:`lifespan`(启动时**预热**检索模型,热完才把 `retrieval_ready` 置 True)、一串 `include_router`(挂了哪些功能路由)、`/static` 挂载(商品图) |
| 2 | `server/app/config.py` | 配置从 env 读什么(LLM key、provider、各种开关) |
| 3 | `server/app/services/retrieval_readiness.py` | `warm_retrieval_pipeline`——为什么要预热(模型懒加载冷启动 4-14s,预热 + `/ready` 门控让第一条请求不卡) |

✅ **检查点**:你能讲"为什么有个 `/ready` 接口、它在防什么"(冷启动慢 → 预热完才接客)。

---

## 第 3 幕:主干 —— 跟着一条请求走完全程 · 约 1 小时(最重要的一幕)

**只看一个文件:`server/app/routes/chat.py`(1301 行)。但不要逐行读。**

读法分两遍:
- **第一遍(骨架)**:读顶部 docstring(7 步)→ 直接跳到 `chat_stream` 函数(约 835 行起,主函数)→ **从上往下扫**。每当它调用一个别的函数(比如 `_needs_clarification`、`_is_out_of_domain`、`top_k`、`_detect_cart_intent`、`_build_catalog`、最后那个嵌套的 `generator`),**先别跳进去**,只在旁边记一句"第 N 步,它在做 X"。扫完你就有了整条主干的骨架。
- **第二遍(填肉)**:回头看这几个**短小的意图判断函数**(都在同文件,各 20-40 行,纯规则好懂):
  - `_needs_clarification`(686行):啥时候触发"主动反问"
  - `_is_out_of_domain`(726行):啥时候判定"越界"(问机票)不出卡
  - `_detect_cart_intent`(409行):怎么从文字识别 加购/结算/删除/清空/改数量
  - `_build_catalog`(120行)+ `_PROMPT`(约71-118行):怎么把 5 个商品拼成"目录"塞进给 LLM 的提示词,以及**来源标签 [目录✓]/[推断?] + 目录护栏纪律**就写在这段 prompt 里

✅ **检查点(关键)**:你能对着 `chat_stream` 从头讲到尾:"先判断要不要反问 → 不反问就检索 → 检测购物车意图 → 拼目录 + 系统提示 → 进 generator 流式发 商品卡/文字/事件"。讲得出来 → 主干通了,后面都是给主干上的某一步"放大看细节"。

---

## 第 4 幕:检索子系统 —— 主干第③④⑤步的放大 · 约 1.5 小时(系统的心脏)

主干里那一行 `top_k(...)` 背后是整个检索流水线。**按下面顺序读,从底层往上,每个都给前一个补全**:

| 顺序 | 文件(行数) | 读完你知道 |
|---|---|---|
| 1 | `rag/retrieve/query.py`(222) | 检索的**目录页/编排器**:它怎么依次调用下面这些。先看它就有了检索的骨架 |
| 2 | `rag/retrieve/bm25.py`(74) | **关键词路**:jieba 中文分词 + BM25 精确匹配("防晒"这种词) |
| 3 | `rag/retrieve/hybrid.py`(69) | **融合**:把 向量召回 + BM25 召回 用 **RRF(倒数排名融合)** 合并。**很短,核心,必读** |
| 4 | `rag/retrieve/rerank.py`(136) | **重排**:cross-encoder 把 query 和每个候选拼起来重新打分;含**中英文语言路由**(中文用 base,英文用 v2-m3)。recall 0.72→0.958 主要靠它 |
| 5 | `rag/retrieve/constraints.py`(442) | **硬约束解析**:把一句话解析成 类目/品牌/价格 过滤器。看 `_SUB_CATEGORY_RULES`(类目映射表,治串货的)、`build_retrieval_filter`、`_brands`+`_is_negated`("华为以外"那个招牌 bug 就在这) |
| 6 | `rag/retrieve/negation.py`(271) + `brand_origin.py` | **产地/否定过滤**:`apply_negation` 查"品牌→国家"表删掉日系。**这是"不要日系靠查表不靠 LLM"的证据**,负例准确率 1.000 |
| 7(略读) | `synonyms.py` / `rewrite.py` / `english_terms.py` / `preferences.py` | query 增强:同义词扩展、改写、英文词→中文类目、👍/👎 偏好重排。知道有这些就行 |
| 8 | `server/app/services/rag_client.py`(918,**只读 2 个函数**) | `top_k`(382行,对外入口,主干就是调它)和 `_heavy_retrieve`(181行,混合+重排+过滤+**缓存**全在这)。这是"检索流水线"和"主干"的接缝 |

✅ **检查点**:你能讲"`top_k` 被调用后,内部发生了什么"——向量+BM25 两路 → RRF 融合 → cross-encoder 重排 → 应用否定/类目/价格过滤 → 返回 top-5,且结果进缓存。这一段讲顺了,你就拿下了整个项目最硬核的部分。

---

## 第 5 幕:主干剩下的分支 —— 多轮 / 生成 / 缓存 · 约 45 分钟

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `server/app/services/contextual_query.py` | **多轮**:把历史几轮拼成"检索用的 query"(`build_retrieval_query`) |
| 2 | `server/app/services/constraint_state.py` | **多轮约束继承**:把历史里的 品类/预算/排除品牌 累积下来(`build_conversation_filter`);"华为以外"靠它继承上文 |
| 3 | `server/app/services/llm_provider.py` | **LLM 调用**:provider 抽象,env 切换 TokenRouter / Doubao;主干里 `get_provider()` 来自这 |
| 4 | `server/app/services/cache.py` | **两层缓存**:`make_key`(缓存键怎么算——含对话/图片hash/汇率/偏好)+ 回放逻辑。主干里 cache hit 直接回放就是它 |

✅ **检查点**:你能讲"多轮怎么记住上文约束"和"缓存键为什么要包含偏好(否则 👍/👎 后回放旧结果)"。

---

## 第 6 幕:数据怎么进库(没有这步,检索没东西可检)· 约 40 分钟

主干在"查"商品,这一幕讲商品当初怎么"入库"变成向量的。

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `data/seed/`(扫一眼目录结构) | 原料:8 个类目文件夹,每个商品一个 json + 一张图 |
| 2 | `rag/ingest/chunk.py` | 把商品文本切成"块"(标题/描述分开,便于检索) |
| 3 | `rag/ingest/embed_text.py` | 用 **bge-small-zh-v1.5** 把文本块变成向量,存进 Chroma 文本集合 |
| 4 | `rag/ingest/embed_image.py` | 用 **OpenCLIP ViT-B/32** 把商品图变成向量,存进 Chroma 图像集合(拍照找货靠它) |
| 5 | `rag/ingest/run.py` / `run_image.py` | 跑入库的脚本(`aaalion ingest` 就是它) |

✅ **检查点**:你能讲"商品图和用户拍的照片为什么能匹配"——CLIP 把图和文映射到**同一向量空间**,入库时编码,拍照时编码,找最近邻。

---

## 第 7 幕:怎么证明它好(评测)· 约 30 分钟

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `rag/eval/golden.jsonl`(**翻几条就行**) | 评测样例长啥样:query + 期望命中的商品 + `forbidden`(禁止出现的) |
| 2 | `rag/eval/core.py` | 怎么算 recall@5 / MRR / negation_accuracy 这些指标 |
| 3 | `rag/eval/run.py` + `docs/EVAL_RESULTS.md` | 怎么跑(`aaalion eval`)、最新结果(recall@5 0.958 / 否定 1.000) |

✅ **检查点**:评委问"0.958 怎么来的",你能打开 golden.jsonl 指着说"92 个这样的样例,每个查完看前 5 个命中没有,平均出来的"。

---

## 第 8 幕:客户端(略读,知道形状即可——细节归陈澍枫)· 约 30 分钟

后端讲完,看一眼 iOS 的对称结构就够了。**对照第 1 幕的数据契约看,你会发现客户端就是后端的镜像**。

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `client/.../AAALionAppApp.swift` + `Config.swift` | App 入口 + **后端 URL 配在哪**(`defaultBackendURL`=那个隧道地址) |
| 2 | `Models/`(ChatDelta / ProductCard / Message) | 客户端的数据模型——**和第 1 幕后端的 SSE 事件一一对应** |
| 3 | `Services/ChatService.swift` | **SSE 解析**:逐行读 `data:` 事件——这是后端 `chat.py` generator 的"反面" |
| 4 | `ViewModels/ChatViewModel.swift` | 聊天状态(消息列表、cart 意图字段) |
| 5 | `Views/ChatView.swift` | 主界面 + **收到 cart_intent 后怎么操作购物车**(那个 switch: add/checkout/remove/clear) |
| 6 | `Stores/CartStore.swift` / `FavoritesStore.swift` | 购物车/收藏本地状态(按用户隔离持久化) |

✅ **检查点**:你能讲"后端发 `product_card` 事件 → iOS 哪个文件解析 → 渲染成卡片"的全链路。

---

## 第 9 幕:怎么上线(运维)· 约 20 分钟

| 顺序 | 文件 | 读完你知道 |
|---|---|---|
| 1 | `README.md` / `docs/DEPLOY_GUIDE.md` 的部署节 | GCP 单 VM + systemd 三服务(后端 / cloudflared 隧道 / 自动部署 timer) |
| 2 | `tools/cloud-autodeploy.sh` | **CD 脚本**:每 2 分钟 git fetch,main 有更新就 reset + 重启 + `/ready` 健康检查 + 失败回滚 |
| 3 | `Makefile` / `tools/aaalion` | 所有命令(backend / ingest / eval / ios-device)从哪来 |

✅ **检查点**:你能讲"我 push 到 main 之后,云上是怎么自动更新的,失败了会怎样"。

---

## 附:外围功能路由(主干通了之后随便翻,都是独立小模块)

这些是锦上添花的功能,各自一个 route + 一个 db,互不依赖主干,**最后看、快速看**:
`routes/auth.py`(登录/JWT)· `products.py`(商品详情)· `currency.py`(汇率)· `repurchase.py`(复购提醒)· `preferences.py`(偏好)· `group_buy.py`(拼团)· `price_watch.py`(降价提醒)。看一个就懂套路:`routes/X.py` 收请求 → `services/X_db.py` 读写 SQLite。

---

## 总时间预算 & 建议节奏

| | 幕 | 累计 |
|---|---|---|
| **第 1 天(框架)** | 第 0→1→2→3 幕(地图 + 契约 + 启动 + 主干) | 看完你**知道整个系统在干啥** |
| **第 2 天(心脏)** | 第 4→5 幕(检索 + 多轮/生成/缓存) | 看完你**能为每个技术取舍辩护** |
| **第 3 天(收尾)** | 第 6→7→8→9 幕(入库 + 评测 + 客户端 + 运维) | 看完你**对全链路无死角** |

**唯一的纪律**:每读完一个文件,回到开头那条主线,把它**贴到对应的步骤上**。
读完第 3 幕你就有骨架了;读完第 4 幕你就有了底气;读完全部,这个项目对你不再是黑盒。

> 卡在哪一幕看不懂,直接跟我说"带我过 第 X 幕 的 Y 文件",我把代码贴出来用大白话逐段讲。
