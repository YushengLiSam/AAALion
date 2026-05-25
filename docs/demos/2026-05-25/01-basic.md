# 01 — basic recommendation

**Query**: `推荐一款适合油皮的洗面奶`

**Verdict**: ✅ PASS

## Assistant reply (excerpt)

> 根据目录，我暂时没有找到专门标注"油皮"的洗面奶。
> 目录中有一款 **珊珂洗颜专科绵润泡沫洁面乳（¥52.0）** 适合中性、混合性皮肤…
> **建议：** 如果你是混油皮（T区油腻），珊珂这款能很好地清洁油脂污垢；如果你是纯油皮，
> 我建议你告诉我更多需求（如是否容易长痘、敏感度等）…

Honest "no exact match" while still surfacing the closest fit. No hallucination.

## Products returned (top 5)

| ID | Title | Brand | Origin | Price |
|---|---|---|---|---|
| `p_beauty_011` | 珊珂洗颜专科绵润泡沫洁面乳 | 珊珂 (Senka) | JP (via brand-origin lookup) | ¥52 |
| `p_beauty_023` | 理肤泉特护清盈防晒乳 | 理肤泉 | FR | ¥268 |
| `p_beauty_020` | 雅诗兰黛持妆粉底液 | 雅诗兰黛 | US | ¥440 |
| `p_beauty_014` | 花西子空气蜜粉 | 花西子 | CN | ¥169 |
| `p_beauty_013` | 方里柔焦蜜粉饼 | 方里 | CN | ¥99 |

## What this verifies

- Synonym expansion firing (Tujie R6.5): "油皮" → "控油 / 油性肌肤 / 清爽 / 抑油".
- Provenance markers rendered on the cards (flag emoji per brand origin).
- Honest reply (LLM cited a small gap rather than fabricating "perfect油皮 product").
