# Multi-hop RAG 设计方案 — 狮选 LionPick

> 状态:方案(未实现)。定位:赛后增强,面向作品集/面试叙事 + 真实产品价值。
> 设计原则与现有系统一脉相承:**控制流走确定性规则,语言理解走 LLM;每一跳都复用生产级检索;跳间信息传播可解释、可断言。**

---

## 1. 什么是 Multi-hop、为什么我们需要

现有系统是 **single-hop**:一句话 → 一次检索 → 生成。一步到位,答不了"需要**先查到 A,再拿 A 的信息去查 B**"的问题:

| 用户问 | 为什么单跳答不了 | 多跳怎么答 |
|---|---|---|
| "有没有比 AirPods Pro 便宜的降噪耳机" | 检索器不知道 AirPods Pro 多少钱 | hop1 查到 AirPods Pro(¥1899)→ hop2 检索"降噪耳机 且 价格<1899" |
| "跟特步那双跑鞋同价位的其他跑鞋" | "同价位"是相对于锚点的 | hop1 锚定特步 160X(¥999)→ hop2 检索跑鞋 ∈ [800,1200],排除锚点自身 |
| "买了 iPhone,配个耳机" | "配"需要知道锚点的品牌/生态 | hop1 锚定 iPhone(Apple)→ hop2 检索耳机,同品牌加权(AirPods 靠前) |
| "比刚才第二款便宜的有吗"(多轮) | 锚点在**上一轮的卡片**里 | hop1 从会话历史解析"第二款"→ hop2 派生价格约束检索 |

本质:**跳间依赖**——第二次检索的约束,来自第一次检索的**结果**。

## 2. 总体架构:锚定式两跳(规则)+ LLM 规划兜底

```
用户 query
   │
   ├─ ① 多跳意图检测(正则模式,~0ms)────────── 未命中 → 走现有单跳流程(完全不变)
   │        命中
   ▼
   ② 解析出 HopPlan { 锚点词, 关系, 目标品类 }
   ▼
   ③ hop-1:top_k(锚点词, k=3)          ← 复用现有 混合检索+重排 全流水线
   │     取 top-1 为锚点;rerank 分数过低 → 触发已有的"反问澄清"("你是指X吗?")
   ▼
   ④ 属性提取(纯结构化,不走 LLM):
   │     anchor.price_cny / brand / category / sub_category / product_id
   ▼
   ⑤ 约束派生(按关系查表,确定性):
   │     cheaper      → Filter(sub_cat=锚点品类, price_max=锚点价×0.95, 排除锚点id)
   │     same_price   → Filter(price ∈ [0.8×, 1.2×], 排除锚点id)
   │     same_brand   → Filter(brand=锚点品牌, category=目标品类)
   │     pair(搭配)   → Filter(category=互补品类表[锚点品类]) + 同品牌加权
   ▼
   ⑥ hop-2:top_k(目标品类词, filters=派生约束)   ← 再次复用全流水线(含否定/预算/兜底)
   ▼
   ⑦ 合并输出:锚点卡(1张) + hop2 结果(4张)
        + 新 SSE 事件 hop_trace(检索链,给 UI/评委看"它是怎么想的")
        + prompt 附则:"先确认锚点,再推荐,并解释关系(比它便宜¥N 等)[目录✓]"
```

**为什么规则优先、LLM 兜底**(和"否定过滤不靠 LLM 自觉"同一哲学):
- 四种关系(更便宜/同价位/同品牌/搭配)覆盖电商多跳的绝大多数问法,正则可确定性解析;
- 属性提取从商品 JSON 直接拿字段,**零幻觉、可断言**(hop2 每个结果都能程序化验证"确实比锚点便宜");
- LLM 规划器(Phase 2)只兜"规则没接住、但像多跳"的长尾,失败静默回退单跳(复用 extract_negation 的兜底模式)。

## 3. 模块落点(全部增量,不改现有行为)

| 新增/修改 | 内容 |
|---|---|
| `rag/retrieve/multihop.py`(新) | `detect_multihop(text, history) -> HopPlan\|None`、`derive_filter(anchor, relation)`、互补品类表 `PAIR_MAP` |
| `server/app/services/rag_client.py` | 新函数 `multi_hop_retrieve(plan) -> (anchor, products, trace)`,内部两次调 `top_k` |
| `server/app/routes/chat.py` | 意图层加一个分支:`plan = detect_multihop(...)`;命中则走 `multi_hop_retrieve`,发 `hop_trace` 事件,prompt 加附则 §8 |
| `rag/eval/golden_multihop.jsonl`(新) | ~15 例:`{query, anchor_expect, final_expect[], relation_assert}` |
| `rag/eval/core.py` | 新指标:anchor_accuracy、final_recall@5、**relation_correctness**(程序化断言:hop2 结果全部满足派生约束) |
| iOS `ChatView`(可选 Phase 3) | 渲染 hop_trace 成"检索链"面包屑:`锚点: 理肤泉防晒 ¥268 → 找更便宜的防晒` |

**多轮锚点解析**(Phase 1.5,高价值):"比刚才第二款便宜的" → 锚点不检索,直接从会话历史里上一轮的 product_card 列表取第 2 个(复用现有 `_parse_ordinal` 序数解析 + 历史遍历)。

## 4. 触发模式(初版四类正则)

```
比较锚定   比(X)(更|再)?(便宜|贵|高端|轻|大)          → cheaper / pricier
同类锚定   (跟|和)(X)(同|一样|差不多)(价位|品牌|风格)   → same_price / same_brand
搭配锚定   (买了|刚入|入了)?(X)[,,]?(配|搭)(个|双|条)?(Y) → pair
会话锚定   比(刚才|上面|第N款)(更)?(便宜|好)            → cheaper + 历史锚点
```
锚点词 X 必须能在目录里对上(与 `except_brands` 相同的"目录词典交集"校验),对不上→不触发,零误伤现有流程。

## 5. 可演示的真实 query(全部基于现有 145 商品目录验证过可行)

1. `有没有比 AirPods Pro 便宜的降噪耳机` → 锚 ¥1899 → 华为 FreeBuds Pro 5 ¥1699 ✓
2. `比理肤泉那款防晒便宜的防晒霜` → 锚 ¥268 → 巴黎欧莱雅 ¥170 ✓
3. `跟特步跑鞋同价位的其他跑鞋` → 锚 ¥999 → Pegasus 41 ¥899 / HOKA ¥1099 ✓
4. `买了小米手机,配个耳机` → pair + 品牌加权
5. (多轮)`推荐防晒` → `比第二款便宜的有吗` → 会话锚点 ✓

## 6. 性能与风险

| 项 | 预估 |
|---|---|
| 延迟 | 规则路径 +1 次检索 ≈ +150ms(热);两跳均走现有检索缓存。LLM 规划路径 +1~2s(仅长尾) |
| 回归风险 | 低:未命中模式 = 现有流程零改动;92 例回归集作门禁 |
| 已知限制 | 145 商品目录下 pair 互补表要人工精选;锚点歧义靠"反问澄清"兜;>2 跳暂不支持(电商场景两跳够用,上限硬编码) |

## 7. 分期与工作量

| 阶段 | 内容 | 工作量 |
|---|---|---|
| P1 | 规则两跳(4 关系)+ hop_trace 事件 + 15 例评测 | ~1 天 |
| P1.5 | 会话锚点("比第二款便宜的") | ~0.5 天 |
| P2 | LLM 规划器兜底(JSON schema 分解,静默回退) | ~0.5 天 |
| P3 | iOS 检索链面包屑 UI | ~0.5 天 |

## 8. 面试/答辩叙事(一句话)

> "我们把 single-hop RAG 升级为**锚定式 multi-hop**:每一跳都复用生产级混合检索,跳间通过**结构化属性传播约束**——不经过 LLM、可解释、可程序化断言(hop2 结果 100% 满足派生约束),并把检索链暴露成 hop-trace 让回答可审计。规则接住高频关系,LLM 规划器兜长尾,失败静默降级单跳。"
