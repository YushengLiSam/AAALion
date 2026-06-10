# API 参考

后端只暴露一个很小的 HTTP 接口面。iOS 端只感知这些端点。请保持本文件与 `server/app/main.py` 同步。

## Base URL(基础地址)

开发环境:`http://localhost:8000`。在 `client/AAALionApp/AAALionApp/Config.swift` 中配置(`PUBLIC_BACKEND_URL`)。

## 端点列表

### `GET /health`

存活检查(liveness check)。

**响应 200**:
```json
{ "status": "ok", "version": "0.1.0" }
```

---

### `GET /ready`

面向用户流量的就绪检查(readiness check)。后端在启动时预热 embedding、BM25、
cross-encoder 重排器(reranker)以及一次完整的检索调用。

预热完成后 **响应 200**:
```json
{"status":"ready","retrieval":{"status":"ready","prewarm":"completed","embedding":"ready","bm25":"ready","reranker":"ready","query_path":"ready"}}
```

若启动预热失败则 **响应 503**。在该端点就绪之前,`/chat/stream` 也会返回 `503`,
因此用户请求永远不会承担模型加载的开销。

---

### `POST /chat/stream`

多轮对话,流式响应。

**请求**:
```json
{
  "messages": [
    {"role": "user", "content": "推荐一款适合油皮的洗面奶"},
    {"role": "assistant", "content": "..."},
    {"role": "user", "content": "再便宜点的呢"}
  ],
  "filters": {
    "category": "美妆护肤",
    "sub_category": "洁面",
    "price_min": 0,
    "price_max": 200,
    "include_brands": ["珂润"],
    "exclude_brands": []
  }
}
```

`filters` 为可选字段。支持的字段有 `category`、`sub_category`、
`price_min`、`price_max`、`include_brands` 和 `exclude_brands`。后端
也会从文本中推断正向约束,例如 `3500元以下的 Sony 降噪耳机`;
请求中显式给出的字段会覆盖推断值。

对于多轮请求,推断出的约束会跨用户消息持续存在,并在检索前更新。
诸如 `预算加到3500元`、`不要 Sony
了,改成 Bose`、`品牌不限` 或 `预算不限` 这样的追问会替换或取消先前状态,
而无需客户端自行构造 `filters`。显式发送
`filters` 仍然是当前请求的最高优先级覆盖。

**响应**:`text/event-stream`。每个事件是一个 JSON 对象,每行一个 `data:`:

```
data: {"type":"delta","text":"为你推荐"}

data: {"type":"delta","text":"这款洁面"}

data: {"type":"product_card","product":{"product_id":"p_2_intl_01","title":"索尼 WH-1000XM5 头戴降噪耳机 黑色","brand":"Sony","base_price":398.0,"price_cny":2703.06,"exchange_rate":{"source_currency":"USD","target_currency":"CNY","rate":6.7916,"rate_date":"2026-05-25","provider":"Frankfurter latest reference rate","stale":false},"provenance":{"currency":"USD","source_platform":"Amazon US"},"image_url":"..."}}

data: {"type":"done"}
```

事件类型:

| `type` | 载荷 | 含义 |
|---|---|---|
| `product_card` | `{"product": {...}}` | 展示一张商品卡片。**R10**:在 `delta` 文本*之前*发出(首屏极速 — 卡片先于 LLM 文本) |
| `delta` | `{"text": "..."}` | 追加到流式消息中的 token |
| `cart_intent` | `{"action": "...", "index": Int?, "quantity": Int?}` | 对话式购物车操作。`action` ∈ `add` · `checkout` · `remove` · **`set_quantity`**(R10)。`index` 是从 1 开始的序号(`-1` = 最后一个),用于 remove/set_quantity;`quantity` 是 set_quantity 的目标数量(如"把数量改成2") |
| `clarify` | `{"chips": ["...", ...]}` | **R10 #5** — 请求过于模糊;回复是一个澄清问题,这些是可点选的快捷回复选项(本轮不出商品卡片) |
| `claim_summary` | `{"verified": Int, "inferred": Int}` | 按消息统计的来源计数,用于 `[目录✓]`/`[推断?]` 标记 |
| `error` | `{"message": "...","code":"..."}` | 展示错误提示;流到此结束 |
| `done` | `{}` | 流完成,可以最终定稿气泡 |

连接以一个 `data: {"type":"done"}` 事件结束,随后是 EOF。

价格字段:

| 字段 | 含义 |
|---|---|
| `base_price` | 以 `provenance.currency` 计价的原始目录金额;永不被覆盖 |
| `price_cny` | 面向用户的人民币金额。CNY 商品等于 `base_price`;外币商品在汇率可用时为换算值 |
| `exchange_rate` | 已换算的外币商品才有:源/目标币种、汇率、参考日期、提供方,以及 `stale` 回退标志 |

外币换算使用来自
[Frankfurter v2](https://frankfurter.dev/) 的最新可用参考汇率,并在服务端缓存一
小时。它用于可比展示和人民币预算过滤;并非支付结算报价。
若既无新鲜报价也无缓存报价可用,
客户端会展示原始币种,而不是伪造一个 CNY 数值。
在检索方面,CNY 目录商品在候选召回阶段即按预算过滤。
外币来源的商品则保留到换算之后,再按同一人民币区间严格校验。

---

### `POST /chat/multimodal`

与 `/chat/stream` 形态相同,但接受一张图片。用于拍照找货。

**请求**:`multipart/form-data`
- `image`:文件(JPEG/PNG,最大 5 MB)
- `messages`:JSON 字符串(与 `/chat/stream` 相同)
- `filters`:JSON 字符串,可选

**响应**:与 `/chat/stream` 相同的 SSE 流。

---

### `GET /products/{product_id}`

按 id 获取商品详情。iOS 端在点击卡片后由 `ProductDetailView` 使用。

**响应 200**:
```json
{
  "product_id": "p_2_intl_01",
  "title": "索尼 WH-1000XM5 头戴降噪耳机 黑色",
  "brand": "Sony",
  "category": "数码电子",
  "base_price": 398.0,
  "price_cny": 2703.06,
  "provenance": {"currency": "USD", "source_platform": "Amazon US"},
  "exchange_rate": {
    "source_currency": "USD",
    "target_currency": "CNY",
    "rate": 6.7916,
    "rate_date": "2026-05-25",
    "provider": "Frankfurter latest reference rate",
    "stale": false
  },
  "skus": [{"price": 398.0, "price_cny": 2703.06}],
  "rag_knowledge": {
    "marketing_description": "...",
    "official_faq": [...],
    "user_reviews": [...]
  }
}
```

上面的响应是一份示例性的抓取报价。`price_cny` 和
`exchange_rate.rate` 取决于提供方的最新可用报价,即使在同一汇率日期内也
可能被刷新;它们不是固定的目录数据。

**响应 404**:
```json
{ "detail": "product not found" }
```

---

### `GET /static/images/<filename>`

静态商品图片。演示用途下,服务器将 `data/seed/<category>/images/` 挂载到此处。

## 错误

使用标准 HTTP 状态码。对于 SSE,错误以 `data: {"type":"error","message":"...","code":"..."}` 事件形式发出,而非 HTTP 状态码(此时连接已经是 200 OK)。

常见错误码:
- `RATE_LIMITED` — 触发 Doubao 限流;客户端应退避后重试。
- `RETRIEVAL_EMPTY` — RAG 未返回任何商品;模型会向用户如实说明。
- `UPSTREAM_TIMEOUT` — Doubao 耗时超过 30 秒。

## 版本管理

1.0 之前。破坏性变更要求在同一个 PR 中同时更新客户端和后端。
