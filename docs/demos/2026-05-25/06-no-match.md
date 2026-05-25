# 06 — no-match (honest "we don't have this")

**Query**: `推荐一台量子计算机`

**Verdict**: ✅ PASS

## Assistant reply (excerpt)

> 很遗憾，我的商品目录中 **没有量子计算机**。
>
> 我目前只能推荐以下电子产品：
> - 💻 笔记本电脑（Apple MacBook/联想 ThinkBook/ThinkPad）
> - 📱 平板电脑（Apple iPad Pro）
>
> 如果您的需求可以用 **高性能笔记本或平板** 来满足（比如进行复杂计算、数据分析、专业创意工作），我很乐意帮您挑选！😊

Model admits no match (no hallucinated "QuantumLab Pro Z1 ¥99999"), pivots to closest-relevant category, opens a follow-up.

## Products returned (top 5 — what retrieval surfaced, ALL filtered by the LLM)

| ID | Title | Brand | Price |
|---|---|---|---|
| `p_digital_023` | 联想 ThinkBook 14+ 2026 | 联想 | ¥6999 |
| `p_digital_020` | Apple MacBook Air 13" M5 | Apple | ¥8499 |
| `p_digital_006` | Apple MacBook Pro 14" M5 | Apple | ¥13499 |
| `p_digital_013` | Apple iPad Pro 13" M5 | Apple | ¥8999 |
| `p_digital_022` | 联想 ThinkPad X1 Carbon | 联想 | ¥10999 |

## What this verifies

- Anti-hallucination guard rail intact: model says "没有量子计算机" rather than inventing one.
- Graceful pivot to "computers" closest neighbors (laptop / iPad).
- Sam's eval `no_match_correctness = 0.855` — this scenario is part of the 11 no-match cases that drive that number.
