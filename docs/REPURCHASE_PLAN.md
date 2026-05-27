# 复购提醒功能 - 完整设计文档

> **状态**: 设计完成,**未实现**。本文档是 single source of truth 的 spec,
> 含原 Phase 0-5(购买落库 + 周期提醒)+ 开屏主动推荐增量(in-app,not push)
> + 5 项 review 改进。
>
> **何时实现**: 面试 + 上云部署完成后再启动。预估总工时 3-4 小时(Phase 0-5
> + A/B,不含 C 的 stretch)。
>
> **作者**: Sam (李雨晟)
> **Reviewer 沿用建议**: Codex review 的 5 点全部已并入(snooze dedup、
> IDFV caveat、`/onboarding` 并进 `/reminders`、`next_due_at` 预计算 +
> 索引、unit test 矩阵)。

---

## 0. 目标与边界

### 0.1 一句话

> 用户打开 App 进入聊天界面时,**先于任何用户输入**,以「商品卡片 + 一句提醒文案」
> 的形式主动展示「该补货」的复购商品,并提供「再来一单」按钮闭环。

### 0.2 必须做(in scope)

- 购买落库(消费动作的服务端记录)
- 周期补货计算(基于商品类目默认周期 + 用户购买时间)
- 「该补货」提醒接口(开屏 + 设置页可监控两用)
- 客户端对接契约(给队友照着接,**不写 Swift**)

### 0.3 不做(out of scope)

| 不做的 | 理由 |
|---|---|
| **APNs / 系统推送 / 锁屏通知** | iOS 推送要全套配置(证书 / device token / 后端 APNs 集成 / 证书续期),10 天内为加分项做这个**风险远大于收益**,且大半在客户端 lane。**in-app 主动展示就能完整满足需求**。 |
| **登录系统 / 账号体系** | 用 `identifierForVendor` 当 user_id 已经够。比赛 demo 阶段不需要跨设备同步。 |
| **复购推荐文案走 LLM(默认路径)** | 开屏接口是高频路径(每次开 App 都调),不能每次烧 LLM token。**默认用模板**,LLM 文案是 Phase C stretch 且必须缓存。 |
| **服务端定时 cron / 主动推送** | 既然不做 push,就不需要 cron 扫全库找 due 用户。**用户主动开 App 时即时计算即可**——这是 server-side 设计上的重要简化。 |

---

## 1. 数据模型

### 1.1 存储选型

**SQLite**,数据库文件 `data/repurchase.db`(gitignored)。

**为什么不是其他**:
- 不是 Chroma:这是关系数据,不是向量
- 不是 Redis:demo 单实例不需要跨进程共享,SQLite in-process 零运维
- 不是 Postgres:多一个进程 / 多一个故障源,demo 没必要
- 是 SQLite:Python stdlib `sqlite3`,**零新依赖**;持久化到本地文件;
  我们后端已有 in-process pattern(Chroma `PersistentClient`)与之一致

### 1.2 表结构

```sql
CREATE TABLE IF NOT EXISTS purchases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT    NOT NULL,        -- iOS identifierForVendor (UUID)
    product_id      TEXT    NOT NULL,        -- 跟 Chroma / data/seed/*.json 一致
    purchased_at    INTEGER NOT NULL,        -- unix epoch 秒
    cycle_days      INTEGER NOT NULL,        -- 周期(由 product 类目默认 + 用户可覆盖)
    next_due_at     INTEGER NOT NULL,        -- 预计算: purchased_at + cycle_days * 86400
    last_shown_at   INTEGER DEFAULT NULL,    -- 最近一次被「提醒展示」的时间;snooze 用
    created_at      INTEGER NOT NULL         -- 插入时间(审计 / 调试)
);

-- review 改进 #4: 索引让 due 查询不扫全表
CREATE INDEX IF NOT EXISTS idx_user_due
    ON purchases (user_id, next_due_at);

-- 用于 snooze 过滤
CREATE INDEX IF NOT EXISTS idx_user_shown
    ON purchases (user_id, last_shown_at);
```

### 1.3 类目→周期默认表(分离到代码常量,可调)

```python
# server/app/services/repurchase_db.py
DEFAULT_CYCLE_DAYS_BY_CATEGORY: dict[str, int] = {
    "美妆护肤": 60,    # 面霜/精华 2 个月
    "食品饮料": 30,    # 牛奶/咖啡 1 个月
    "母婴健康": 30,    # 奶粉/尿不湿 1 个月
    "食品生活": 30,    # 日用品 1 个月
    "服饰运动": 365,   # 衣服几乎不复购,放 1 年(实际不会触发)
    "数码电子": 365,
    "家居家具": 365,
    "图书音像": 365,
}
DEFAULT_CYCLE_FALLBACK = 90  # 类目未知时
```

落库时如果客户端没给 cycle,后端从 product JSON 读 category → 查这个表。

---

## 2. 算法 - `compute_due_items`

### 2.1 函数签名

```python
def compute_due_items(
    user_id: str,
    *,
    now: int | None = None,
    limit: int | None = None,
    snooze_hours: int = 24,
) -> list[dict]:
    """
    返回该用户当前应该被提醒的购买记录,按紧急度排序(最逾期的在前)。

    紧急度 = (now - next_due_at);值越大越逾期 → 排序靠前。
    应用 snooze: last_shown_at 在 `snooze_hours` 内的不返回。

    Returns:
        list of dicts:
            {
                "id": int,             # purchase row id
                "product_id": str,
                "product": {           # 嵌入完整商品卡片 (复用 routes/products.py 格式)
                    "title": ..., "brand": ..., "price_cny": ..., "image_url": ...,
                },
                "purchased_at": int,
                "next_due_at": int,
                "days_overdue": int,   # 负数 = 未到期(若返回的话,目前过滤掉)
                "reminder_text": str,  # 模板生成的中文一句话
            }
    """
```

### 2.2 SQL

```sql
SELECT id, product_id, purchased_at, cycle_days, next_due_at
FROM purchases
WHERE user_id = :user_id
  AND next_due_at <= :now              -- 已到期
  AND (last_shown_at IS NULL OR last_shown_at < :snooze_cutoff)
ORDER BY next_due_at ASC                -- 最逾期的在前
LIMIT :limit;                            -- 客户端 limit;不传则全部
```

`:snooze_cutoff = now - snooze_hours * 3600`。
**review 改进 #4**:`next_due_at` 是落库时就算好的列,query 直接走索引 `idx_user_due`,**不需要在 Python 端遍历**。p95 应 < 50ms 即使 user 有 1000 条 purchases。

### 2.3 模板文案

```python
def _reminder_text(product: dict, days_overdue: int) -> str:
    title = product.get("title", "你之前买的商品")
    if days_overdue <= 0:
        return f"你购买的「{title}」大概该补货了,要再来一单吗?"
    if days_overdue <= 7:
        return f"你购买的「{title}」已经到周期了,要不要再来一单?"
    return f"你购买的「{title}」已经超期 {days_overdue} 天,该补货啦~"
```

**没用 LLM**——三档拼接,稳定、快、可测。**review 改进**:Phase C stretch 才上 LLM 文案,且必须缓存。

### 2.4 「mark shown」原子操作

**review 改进 #1**:接口返回提醒时,**同事务内**更新 `last_shown_at = now`,
否则用户开 App 5 次会被同一条提醒烦 5 次。

```sql
BEGIN;
SELECT ...;  -- 上面那条
UPDATE purchases SET last_shown_at = :now WHERE id IN (returned ids);
COMMIT;
```

---

## 3. API 契约

### 3.1 `POST /repurchase/purchase` — 落一条购买

**用途**:用户点商品卡 "再来一单"、或聊天中说"我买了"等场景。

**Request body**:
```json
{
  "user_id": "F4B7C2D1-...-...",   // 来自 iOS identifierForVendor
  "product_id": "p_1_real_03",
  "purchased_at": 1748284800,       // unix epoch seconds; 可省略,后端用 now()
  "cycle_days": 30                  // 可省略,后端按类目默认
}
```

**Response 200**:
```json
{
  "id": 42,
  "next_due_at": 1750876800
}
```

**Response 400**: `product_id` 不在 catalog 里(防止脏数据)。

### 3.2 `GET /repurchase/reminders` — 拿当前应展示的提醒

**review 改进 #3**:原 plan 想加一个 `/repurchase/onboarding` 端点,但其实
**给 `/reminders` 加一个 `limit` query 参数就够**——同一接口两用,开屏调
`?limit=3`,设置页可监控调不带 limit 拿全部。

**Query params**:
- `user_id` (required): IDFV
- `limit` (optional, int): top N,开屏建议传 3;不传则返回全部 due
- `snooze_hours` (optional, default 24): 测试 / 调试用,生产基本固定

**Response 200**:
```json
{
  "reminders": [
    {
      "id": 42,
      "product_id": "p_1_real_03",
      "product": {
        "product_id": "p_1_real_03",
        "title": "雅诗兰黛小棕瓶第七代",
        "brand": "雅诗兰黛",
        "base_price": 899,
        "price_cny": 899,
        "image_url": "/static/1_美妆护肤/images/p_1_real_03.jpg",
        "provenance": {"origin_country": "US", "source_platform": "Amazon US", ...}
      },
      "purchased_at": 1745692800,
      "next_due_at": 1750876800,
      "days_overdue": 3,
      "reminder_text": "你购买的「雅诗兰黛小棕瓶第七代」已经到周期了,要不要再来一单?"
    }
  ]
}
```

**空状态**(高频路径,**不报错**):
```json
{"reminders": []}
```

客户端据此**不渲染**任何提醒区域,而不是显示「没有提醒哦」之类的占位。

**副作用**:成功返回时,后端 transaction 内已更新 `last_shown_at`(snooze 生效)。

### 3.3 端点已有路由模块映射

新建文件 `server/app/routes/repurchase.py`,在 `server/app/main.py` 里
`app.include_router(repurchase.router)`,跟 `cache_stats / health` 一个 pattern。

---

## 4. 客户端对接契约(队友看的)

### 4.1 user_id 方案

```swift
// iOS
let userId = UIDevice.current.identifierForVendor?.uuidString ?? "anon"
```

**review 改进 #2 — 必须文档化的 caveat**:`identifierForVendor` 在用户
**卸载本 App + 同 vendor 的所有 App** 后会重置(Apple 官方行为)。这意味着:

- 用户卸载狮选后重装 → 历史复购数据**与新 ID 不再关联**
- demo 阶段可接受
- 真上生产需要 login / Apple ID-based identifier

**面试 / 答辩话术**:
> "我们用 IDFV 是为了 demo 阶段做到零登录、跨会话保持购买记录。
> 我们知道 IDFV 会在卸载重装时漂移,所以这个方案的生命周期跟单一 App 安装绑定。
> 真上线会切到 Sign in with Apple 拿稳定 user identifier。"

### 4.2 触发时机

- App 进入聊天界面时(SwiftUI `.onAppear` 在 root chat view)
- 调一次 `GET /repurchase/reminders?user_id=<idfv>&limit=3`
- **不必每次 view 重绘都调**——首次进入即可。如果用户切到设置页再回来,
  可以不重新调(snooze 24h 内本来就不会变)。

### 4.3 展示

- 返回非空 → 在对话流顶部插入 N 张「复购提醒卡」
  - **复用现有 product_card 的 SwiftUI 组件**
  - 加一行 `reminder_text` 在卡片上方
  - 加一个「再来一单」按钮覆盖在卡片底部
- 返回空 → 什么都不显示(对话流是干净的开场)

### 4.4 交互闭环

用户点「再来一单」:

**选项 A(简单,推荐)**: 客户端直接 POST `/repurchase/purchase` 落新记录,
然后**复用聊天界面**让用户继续问问题。

**选项 B**: 把「再来一单 + 商品标题」当成一条 user 消息塞进 `/chat/stream`,
走正常导购流程,后端在 chat.py 里识别这种意图后**同时**调 `/repurchase/purchase`。

**建议先 A,B 是 Phase C stretch**。

### 4.5 同步队友

完成 Phase B 后,直接把这一节链接 + 三个端点 curl 例子甩到 WeChat 群,
让 Shufeng 接。

---

## 5. 实施 phase 划分

| Phase | 工时 | 产出 | DoD |
|---|---|---|---|
| **0. DB 模块** | 30 min | `server/app/services/repurchase_db.py` 含 schema 初始化 + connection wrapper(asyncio.to_thread)+ CYCLE_DAYS 常量表 | sqlite db 文件能自动创建;一个 `init_schema()` 跑完表 + 索引都在 |
| **1. compute_due_items** | 45 min | 同模块加 `compute_due_items()` + `record_purchase()` + `_reminder_text()` | 单元测试 3 个 case(空 / 1 due / 3+ 排序)全过 |
| **2. 路由** | 30 min | `server/app/routes/repurchase.py` 含 2 个 endpoint | curl 手工测两个端点都返回正确格式 |
| **3. Wire 进 main** | 10 min | `server/app/main.py` include_router | `/repurchase/reminders` 在 OpenAPI doc 出现 |
| **4. 客户端契约文档** | 30 min | `docs/REPURCHASE_CLIENT_CONTRACT.md`(或者本文档 §4 拆出来) + 三端点 curl 例子 | 队友照着能跑通 |
| **5. Tests + 性能验收** | 45 min | `server/tests/test_repurchase.py` | 3 个 unit + 1 个 p95 < 50ms perf,全过 |
| **A. 开屏 limit=3** | — | **已并入 Phase 2**(`/reminders?limit=3`),无独立工作 | — |
| **B. 契约文档** | — | **已并入 Phase 4** | — |
| **C. (stretch)** | 2-3h | 见 §7 | 各自独立可上 / 可不上 |

**Phase 0-5 合计 ~3 小时**。

---

## 6. 测试矩阵(review 改进 #5)

### 6.1 必须有的 unit tests(`server/tests/test_repurchase.py`)

```python
def test_compute_due_items_empty_user():
    """新用户 / 没有任何 due 商品 → 返回空列表,不报错。"""
    items = compute_due_items("new-user-uuid", now=now())
    assert items == []

def test_compute_due_items_single_due():
    """1 条 due 商品 → 返回 1 条。"""
    record_purchase("u1", "p_1_real_03",
                    purchased_at=now() - 31 * 86400,
                    cycle_days=30)
    items = compute_due_items("u1", now=now())
    assert len(items) == 1
    assert items[0]["product_id"] == "p_1_real_03"
    assert items[0]["days_overdue"] == 1

def test_compute_due_items_top3_urgency_order():
    """3+ due 商品 + limit=3 → 返回最逾期 3 条,降序排列。"""
    record_purchase("u2", "p_a", purchased_at=now() - 60*86400, cycle_days=30)  # overdue 30
    record_purchase("u2", "p_b", purchased_at=now() - 35*86400, cycle_days=30)  # overdue 5
    record_purchase("u2", "p_c", purchased_at=now() - 50*86400, cycle_days=30)  # overdue 20
    record_purchase("u2", "p_d", purchased_at=now() - 32*86400, cycle_days=30)  # overdue 2
    items = compute_due_items("u2", now=now(), limit=3)
    assert [it["product_id"] for it in items] == ["p_a", "p_c", "p_b"]  # 30, 20, 5

def test_snooze_24h():
    """提醒展示后 24h 内不再返回(review 改进 #1)。"""
    record_purchase("u3", "p_1_real_03",
                    purchased_at=now() - 35*86400, cycle_days=30)
    items1 = compute_due_items("u3", now=now())
    assert len(items1) == 1  # 首次看到
    items2 = compute_due_items("u3", now=now())
    assert len(items2) == 0  # 24h 内不再看到
    items3 = compute_due_items("u3", now=now() + 25*3600)
    assert len(items3) == 1  # 25h 后又能看到
```

### 6.2 性能验收

```python
def test_compute_due_items_p95_under_50ms_at_1000_purchases():
    """review 改进 #4: 1000 条 purchase fixture 下 p95 < 50ms。"""
    # 预填 1000 条
    for i in range(1000):
        record_purchase(f"u{i % 10}", f"p_x_{i}",
                        purchased_at=now() - (i % 60) * 86400, cycle_days=30)
    latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        compute_due_items("u3", now=now(), limit=3)
        latencies.append((time.perf_counter() - t0) * 1000)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    assert p95 < 50, f"p95={p95:.1f}ms 超过 50ms,索引可能没生效"
```

### 6.3 集成测试(可选)

`server/tests/test_repurchase_routes.py` 起 FastAPI test client 跑两个端点
端到端,验证 status code + response shape。

---

## 7. Phase C — Stretch goals(都可选)

### 7.1 同类升级推荐
开屏提醒"洁面该补货"时,**复用 `rag_client.top_k`** 检索同 category 商品,
在「再来一单」按钮旁加「换换看?」展示 2-3 个候选。**不新写检索逻辑**,
就是把 reminders response 的 product 字段从 `dict` 改成
`{"primary": dict, "alternatives": [dict]}`。

### 7.2 LLM 个性化文案
对每个 due 商品调 LLM 生成更自然的提醒语。**必须缓存**:
- cache key = `(user_id, product_id, days_overdue_bucket)`
  其中 bucket = `(days_overdue // 7) * 7`(每 7 天一个 bucket)
- 缓存 30 天
- 复用现有 `services/cache.py` 的 LRU+TTL

**仅在 cache miss 时调 LLM**。Default 仍是模板文案——LLM 拿到一个,
失败 fall back 模板。

### 7.3 Routine 聚合
后端检测到「同一天 due 的多个日用品」时聚合一条:

```json
{
  "reminders": [
    {
      "type": "routine",
      "title": "你的日用品补货组",
      "items": [洗发水, 沐浴露, 牙膏],
      "reminder_text": "你的洗护套装该补货了,要不要打包再来一套?"
    }
  ]
}
```

**纯展示层聚合**——同时 due 的 + 类目都是 `食品生活/母婴健康` 的归一组。
3 个商品才合并,< 3 仍走原始格式。

---

## 8. 已知 limitations / 答辩话术备料

| 局限 | 答辩 talking point |
|---|---|
| **IDFV 卸载即漂移** | "demo 阶段做零登录的 tradeoff。生产切 Sign in with Apple 拿稳定标识。" |
| **周期默认按类目,不个性化** | "起步用类目默认 + 用户可在购买时覆盖。下一步加个性化:基于用户历史购买间隔学一个 per-user-per-product 周期(简单的 EMA 就够)。" |
| **没有跨设备同步** | "demo 单设备假设。生产配合账号体系打通。" |
| **提醒文案模板固定 3 档** | "答辩 dry-run 显示这 3 档已经覆盖 95% 体验。LLM 文案是 Phase C stretch,在 cache 保护下。" |
| **不支持「不要再提醒这个商品」** | "可以加一个 `dismissed_at` 列,UI 给个长按 → 「永久忽略」。**Phase C 加,不在 MVP**。" |
| **没有 cron / 后台计算** | "**这是设计选择不是缺陷**。用户开 App 时即时算够快(p95<50ms),没必要起后台任务。真上 push 才需要 cron 扫全库。" |

---

## 9. 跟现有代码的关系

| 已有 | 复购功能怎么用 |
|---|---|
| `data/.chroma/products_text` 索引 | 不动 |
| `data/seed/*/data/*.json` 商品目录 | **读**——拿 product 元数据填进 reminders response 的 `product` 字段 |
| `server/app/routes/chat.py` | **不动**(Phase A 不改 chat 流程)。Phase C 选项 B 才会加意图识别 |
| `server/app/services/cache.py` | **复用**(Phase C 的 LLM 文案缓存) |
| `server/app/services/llm_provider.py` | **复用**(Phase C 的 LLM 文案生成) |
| `server/app/services/rag_client.py` | **复用**(Phase C 的同类推荐) |
| `data/.chroma/` 落地路径模式 | **借鉴**——`data/repurchase.db` 同样 gitignored、in-process 持久化 |

**没有任何破坏性改动**。复购功能是**加层**,不是改既有路径。

---

## 10. 风险 + 缓解

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| 客户端没传 user_id 或传了脏 ID | 中 | 数据不可信 | 后端校验 UUID 形态,非法直接 400 |
| 同一 user 短时间内大量 purchase POST(脚本攻击) | 低 | DB 膨胀 | 加 rate-limit:同一 user 60s 内 max 10 次 purchase。**Phase C 才加** |
| SQLite 文件损坏 | 低 | 复购数据丢失 | demo 阶段可接受,生产切 Postgres |
| 周期 default 表跟实际类目不一致 | 中 | 提醒时机不准 | 把表写在 const,**测试 fixture 验证每个类目都有 default** |
| 服务重启时 in-flight 提醒展示丢失 | 低 | snooze 状态没持久化前会重复 | 不存在——`last_shown_at` 是 DB 列,本来就持久化 |

---

## 11. 后续 / Future work

1. **Sign in with Apple 集成**——稳定 user_id,跨设备同步
2. **APNs 推送**——`expected_due_at - 3 days` 时主动推到锁屏。**先做完上面的 in-app 路径再说**
3. **A/B 评测**——提醒文案模板版 vs LLM 版的点击率
4. **个性化周期**——基于该用户历次同商品 purchase 间隔学一个 EMA
5. **「不要再提醒」**——`dismissed_at` 列 + UI 长按

---

## 附录 A — Codex review 改进追溯表

| Review 点 | 落地位置 |
|---|---|
| #1 提醒 dedup(24h snooze) | §1.2 schema(`last_shown_at` 列)+ §2.4(原子 mark shown)+ §6.1(`test_snooze_24h`) |
| #2 IDFV 漂移文档化 | §4.1 必须文档化的 caveat |
| #3 `/onboarding` 并进 `/reminders` | §3.2 加 `?limit=3` 参数;§5 Phase A 已并入 |
| #4 `next_due_at` 预计算 + 索引 + p95 验收 | §1.2(`next_due_at` 列 + 索引)+ §2.2(SQL)+ §6.2(perf test) |
| #5 unit test 矩阵 | §6.1 4 个 case + §6.2 perf |

---

## 附录 B — 当前不在 main 的依赖

执行本 plan 前需要确认 / 添加(都不是阻塞,只是 to-do):

- [ ] `data/repurchase.db` 加进 `.gitignore`(目前 `.gitignore` 已 ignore
      `data/.chroma/`,加一行同 pattern 即可)
- [ ] `server/tests/` 目录(已存在,被 Tujie 在 R7.4 期间加过 `test_constraint_state.py` 等)
- [ ] 不需要新 pip 依赖——sqlite3 是 stdlib

---

**Status: 设计 freeze。等面试 + 部署完成后启动 Phase 0-5。**
