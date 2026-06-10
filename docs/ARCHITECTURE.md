# 架构

基于 RAG 的多模态电商导购 Agent 的端到端设计。

> **本文档的"大二学生也能读懂"版本**,见
> [`docs/explainers/10-app-architecture.md`](explainers/10-app-architecture.md)。
> 该讲解版用通俗语言覆盖相同内容,并附具体文件路径;
> 而本文档是面向工程师的深度参考。
> 请按受众选择合适的版本。

## 写给非计算机背景读者的导读

如果你从未做过 Web 应用,本项目可以拆成
四层:

1. **一个 iPhone 应用**,用 SwiftUI 构建。用户打字或语音输入;
   应用展示答案。
2. **一个后端**(一个小型 Python Web 服务器),iPhone 与它通信。
   它接收聊天消息,判断哪些商品相关,然后
   请大语言模型(LLM)生成回复。
3. **一条检索管线**(称为 RAG),在我们的商品
   目录中搜索值得推荐的商品。这是"聪明"的部分 ——
   它结合了关键词检索、语义检索和最终的重排
   环节。
4. **一个外部 LLM**(通过 TokenRouter 调用 claude-haiku-4-5),根据检索
   管线找到的商品,
   撰写自然语言回复。

本文档其余部分是面向工程师的细节。如果下文
出现你不认识的术语,
[`docs/explainers/`](explainers/) 中的讲解文档会先用通俗语言
定义每个概念。

## 高层流程

```
 ┌──────────────┐   text+image   ┌─────────────┐   query    ┌──────────┐
 │ iOS App      │ ─────────────► │  FastAPI    │ ─────────► │ Chroma   │
 │ (SwiftUI)    │                │  /chat      │            │ text +   │
 │              │ ◄────────────  │  /stream    │ ◄────────  │ image    │
 └──────────────┘  SSE tokens    └─────────────┘   top-k    └──────────┘
                                       │  ▲
                                  prompt│  │ deltas
                                       ▼  │
                              ┌─────────────────┐
                              │ Doubao-Seed-2.0 │
                              │     -lite       │
                              └─────────────────┘
```

## 组件

### 1. iOS 客户端(`client/`)
- **SwiftUI**,目标 iOS 17+;每个会话一个 `ChatViewModel`(`@Observable`)。
- **SSE** 通过 `URLSession.bytes(for:)` 消费 → `AsyncStream<ChatDelta>`;取消逻辑挂接到 `.task {}` 生命周期。
- **商品卡片**内联渲染在聊天流中;点击 → `ProductDetailView`。图片用 `AsyncImage` 加 `URLCache.shared`。
- **图片上传**路径:`PhotosPicker`(iOS 17+)→ JPEG 压缩 → multipart POST 到 `/chat/multimodal`。
- **不持有任何 API 密钥** —— 只知道 `PUBLIC_BACKEND_URL`。

### 2. 后端(`server/`)
- **FastAPI** 配 `uvicorn`。流式端点输出 `text/event-stream` 行。
- **`/chat/stream`**(POST,SSE):{ messages, filters? } → token 流 + 商品卡片。
- **`/chat/multimodal`**(POST,SSE):multipart(图片 + 文本)→ 相同的 SSE。
- **`/products/{id}`**(GET):按 id 返回详情,数据来自已索引的 JSON。
- **`/health`**(GET):进程存活;**`/ready`**(GET):检索模型
  与完整查询路径已预热完毕、可承接用户流量。
- **汇率归一化**:`services/currency.py` 为非人民币标价的商品获取并缓存
  最新参考汇率,在响应负载中补充
  `price_cny` + `exchange_rate`,同时保持目录原价不变。
- **编排**:合并多轮对话/API 检索约束状态 → 混合 RAG → 重排
  → 严格的折算人民币预算校验 → 组装 prompt 并流式输出模型
  回复;商品卡片由返回的目录记录生成。
- **Doubao 客户端**:对 ARK API(OpenAI 兼容)的轻量封装。密钥从 `.env` 读取。
- **加固**:超时(端到端 30s)、5xx 重试(退避 0.5s × 2)、按 IP 限流(推迟到 v2)。

### 3. RAG(`rag/`)
- **入库(Ingest)**:
  - `chunk.py`:每个商品 JSON → 多个 chunk:`marketing_description`、每条 `official_faq`、每条 `user_reviews`。每个 chunk 携带 `product_id`、`category`、`sub_category`、`brand`、`base_price` 及来源 `currency`。
  - `embed_text.py`:`BAAI/bge-small-zh-v1.5` 嵌入向量,存入 Chroma 的 `products_text`。
  - `embed_image.py`:在 A100 上用 OpenCLIP ViT-B/32 处理每个商品主图 → 存入 Chroma 的 `products_image`。
- **检索(Retrieve)**:
  - `constraints.py`:由查询文本及可选 API 字段 → 生成 `Filter`,覆盖品类、子品类、品牌包含/排除以及人民币预算。
  - `query.py` + `bm25.py` + `hybrid.py`:稠密与稀疏候选检索在倒数排名融合(RRF)之前使用同一个过滤器。
  - `rerank.py`:交叉编码器(cross-encoder)重排,top-20 → top-5。
- **会话约束**:`server/app/services/constraint_state.py` 将
  用户各轮发言折叠成权威 `Filter`;后续轮次可以继承、替换、
  排除或取消品类/品牌/人民币预算条件。
- **就绪预热**:`server/app/services/retrieval_readiness.py` 加载
  嵌入模型/BM25/重排器,并在开放聊天前先执行一次真实的 `top_k` 调用;
  `Dockerfile.rag` 在镜像构建期缓存模型权重。
- **提示词**:`prompts/system.md` 强制约定"只根据检索到的商品作答,绝不编造价格/优惠券/SKU"。
- **评测**:`eval/golden.jsonl` 含 68 条经审计的回归用例,其中包括四条多轮约束状态用例;`python -m rag.eval.report` 输出 HTML/JSON 指标,含分场景切片。

## 单轮数据流

1. Docker/FastAPI 启动完成检索预热;`/ready` 变为 `200`。
2. iOS 向 `/chat/stream` 发送 `{messages: [...], filters?: {}}`。
3. 后端提取检索查询,并把用户各轮发言折叠为结构化
   约束状态。例如,后续的 `预算加到3500元`、`不要 Sony 了,
   改成 Bose` 或 `预算不限` 会替换/取消继承的约束;显式的
   请求过滤字段优先于推断状态。`RAG_HARD_FILTERS=0`
   可关闭文本/会话推断,用于 A/B 对比。
4. Chroma 稠密检索与 BM25 应用同一过滤器,随后混合融合
   与重排产出候选列表。
5. 对人民币区间约束,已索引的 CNY 价格会被提前过滤;外币标价的
   候选在响应阶段折算后再做严格校验,
   并保留原价及带日期的汇率元数据。
6. 后端组装 prompt:`system_prompt` + `retrieved_context_block` + `conversation_history`。
7. 后端流式转发 Doubao 回复。两种事件类型:
   - `data: {"type":"delta","text":"..."}`
   - `data: {"type":"product_card","product":{...}}` —— 每个被引用的商品发送一次,数据取自已索引的 JSON(不含任何臆造字段)。
8. iOS 把 delta 渲染进流式消息气泡;每收到一个 `product_card` 事件,追加一张以人民币为主价、可追溯原价的商品卡片。

## 多模态(拍照找货)路径

1. iOS 选择/拍摄图片,POST 到 `/chat/multimodal`。
2. 后端用 OpenCLIP 处理图片(模式取决于部署:A100 走 RPC,或本地 CPU 兜底)。
3. 图片向量 → Chroma `products_image` 集合 → top-k。
4. 之后的 prompt 组装与流式输出与文本路径相同,检索到的商品作为上下文。

## 防幻觉保证

- 模型永远看不到完整商品库,只看到检索上下文。若检索未命中,system_prompt 会要求"告知用户找不到匹配商品",而不是瞎猜。
- 客户端渲染的商品卡片来自已索引的 JSON,而非模型文本 —— 模型负责*推理文字*,卡片是*程序化生成*的。
- 原始价格、SKU、图片 URL 一律来自已索引数据;
  面向用户展示的人民币金额由带日期、可溯源的汇率折算得出,
  且不会覆盖目录中的原始证据。

## 部署

- Chroma 本地持久化在 `data/.chroma/` 下;Docker 提供可复现的
  Windows 后端与评测环境。Docker 后端只有在其 `/ready` 健康检查
  确认检索预热完成后才对外暴露。
- 元数据变更后需重跑文本入库,例如新加入索引的
  `currency` 字段(人民币感知的检索过滤所必需)。
- A100 **仅**用于索引构建(CLIP)和批量评测 —— 不在请求路径上。
- iOS 客户端指向运行 `uvicorn` 的笔记本(局域网开发)或后端部署的任意主机。

## 图示参考

- 工作区根目录的 `sam's sample.png` 展示了 Sam 手绘的 5 步流程。
- 演示视频所需的精修版图,待 UI 落地后再重新绘制。
