# Demo 02 — Conditional Filter / Anti-Hallucination

**Verdict**: ✅ PASS (excellent no-hallucination behavior demonstrated)

## User query

> 200元以下的蓝牙耳机有哪些

## Screenshot

![Conditional filter](02-conditional-filter.png)

## Assistant reply (verbatim)

> 根据我们的商品目录，**抱歉，目前没有200元以下的蓝牙耳机**。
>
> 我们现有的蓝牙耳机产品为：
> - 华为 FreeBuds Pro 5：¥1699.0
> - Apple AirPods Pro 3心率监测版：¥1899.0
>
> 这两款都是高端旗舰级产品。如果您对降噪、音质、续航等方面有需求，我可以为您详细介绍这两款产品的特点，或者您可以告诉我您预算范围，我帮您找最合适的选择。

## Product cards shown

- `p_digital_*` 华为 FreeBuds Pro 5 ¥1699
- `p_digital_*` Apple AirPods Pro 3 ¥1899

## Pipeline confirmed

- ✓ The catalog only contains expensive earphones (>¥1500). The LLM **did not invent** a cheaper product to satisfy the user.
- ✓ Honesty + alternative offer instead of hallucination. Matches PDF rubric "AI 编造不存在的商品... 扣分" criterion in reverse.
- ✓ Retrieval correctly limited results to bluetooth earphones.

## Notes

This is the demo that most clearly shows anti-hallucination. Surfaced explicitly in the WeChat update + defense slides — judges care about this.
