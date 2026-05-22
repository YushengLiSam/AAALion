# Research — Data Availability for 狮选 LionPick

> Snapshot of what's actually available for real Chinese e-commerce product data, sourced via Perplexity research (2026-05-22). Three files in this folder, each ~21 KB. The verdict below is the TL;DR.

## Files in this folder

| File | Topic | Key finding |
|---|---|---|
| [`2026-05-22-cn-ecommerce-datasets.md`](2026-05-22-cn-ecommerce-datasets.md) | Real Chinese e-commerce product datasets released 2022+ | None usable — anonymized, missing fields, or behind academic ToU |
| [`2026-05-22-multimodal-ecommerce-benchmarks.md`](2026-05-22-multimodal-ecommerce-benchmarks.md) | Multimodal image-text benchmarks (M5Product, RPC, Products-10K, Fashion-Gen, …) | Some have Chinese text but all gate access to university researchers |
| [`2026-05-22-cn-ecommerce-apis.md`](2026-05-22-cn-ecommerce-apis.md) | Official APIs (Taobao, JD, Pinduoduo, Xiaohongshu, Douyin) + unofficial mirrors | Every commerce-grade endpoint requires 营业执照 |

## Verdict / 结论

**There is no free, usable, real Chinese e-commerce dataset with our full schema** (title + brand + price + image_url + marketing_description + reviews).

### What's out there, and why none of them fit

- **MEP-3M** (3M products, 76 GB) — title + image only, **no price/brand/reviews**, university-only ToU.
- **JDsearch** (12M products) — fully **anonymized** tokens. Not human-readable.
- **Products-10K** (10K SKUs, ~190K images) — images + category IDs only, **no price or brand text** in the released CSVs.
- **OpenBG-IMG** / **TAOBAO-MM** / **TMPS/TLPS** — anonymized or knowledge-graph form; not a product catalog.
- **M5Product** (Chinese, 6M) — gated to academic researchers with signed commitment letter (no commercial use).
- **Fashion-Gen** — **English only**; not relevant for Chinese e-commerce.

### Why we can't just hit the official APIs

| Platform | Individual register? | Useful endpoints accessible? | Notes |
|---|---|---|---|
| Taobao Open Platform | ✓ (ID + face) | ✗ basic only | Order-detail API blocked for personal accounts since 2025 |
| JD Union / JD Open | ✓ (national ID) | partial — affiliate-link tier only | Full catalog needs enterprise + business license |
| Pinduoduo Open | ✓ | partial | "Duo Duo Ke" path is enterprise-flow |
| Xiaohongshu (RED) Notes API | ✗ | ✗ | Invitation-only B2B; business license required |
| Douyin Commerce | ✗ | ✗ | Requires active merchant store + business license |

Unofficial third-party mirrors (e.g. JustOneAPI for Xiaohongshu) exist but ToS-grey and scraped — not defensible for a competition.

## What we're doing about it

Documented in [`docs/PLAN_2026-05-22.md`](../PLAN_2026-05-22.md) Round 2 §"Honest read of the Perplexity research outputs":

1. **Keep the AI-generated seed (100 products, 4 categories) as the pipeline demo set.** It's comparable in field coverage to anything publicly available, and it's permission-clean.
2. **Hand-curate 10-15 real products** from Tmall / JD by manual copy: real title + brand + price + image URL + ~3 reviews each. Drop them into `data/extra/` (gitignored — license-grey) as a separate "real-data" smoke set to prove the pipeline isn't tied to AI-gen artifacts.
3. **Surface this constraint in the defense slides** as a positive: "We did the research, found that the dataset landscape forces this trade-off, and chose a path that ships a working demo + a small real-data validation set."

## Why this matters for the rubric

Per `课题说明会：基于 RAG 的多模态电商智能导购 AI Agent.pdf`:

- **基础功能完整性 (35%)**: the AI-gen seed is sufficient — pipeline goes end-to-end.
- **工程质量 (25%)**: this research, ingested + documented, is itself an engineering-quality signal.
- **效果与可靠性 (20%)**: hand-curated real products give a clean A/B against the AI-gen seed for prompt-grounding checks.
- **加分项 (20%)**: not affected — bonus tracks are about the agent's behavior, not data origin.

## How to extend this folder

If you (Sam / Tujie / future-Shufeng) discover a usable real dataset, add a file here named `YYYY-MM-DD-<topic>.md` with:
- Source (where you found it)
- Schema (what fields are real)
- License + how to download
- Quick verdict: is it usable for our schema?

Also update the table above so the index stays accurate.

## See also

- [`docs/DATA.md`](../DATA.md) — the original prompts that produced these search results; how to extend.
- [`data/README.md`](../../data/README.md) — what's in `data/seed/` vs `data/extra/`.
