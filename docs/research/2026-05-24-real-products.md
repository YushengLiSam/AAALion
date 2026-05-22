# Real-Product Research — 2026-05-24 (Round 6)

> Round 6's pivot: stop generating fake data. Hand-curate real products from
> public e-commerce pages, mark provenance (origin country + currency +
> source platform) clearly in the UI, and let the catalog tell judges
> exactly where each item came from.

## What we did

Two parallel research agents searched the web for verifiable products:

1. **CN agent** (`a53990ab79921362d`) — focused on JD.com, Tmall, Pinduoduo.
   Returned **25 products** across `1_美妆护肤`, `5_母婴健康`, `6_家居家具`,
   `7_图书音像`, `8_户外运动`.
2. **Intl agent** (`a0f813702c1a2a68f`) — focused on Amazon.com / Amazon.co.jp.
   Returned **20 products** across `1_美妆护肤`, `2_数码电子`, `3_服饰运动`,
   `4_食品生活`.

Combined: **45 real products** added to the catalog. Each carries:

- a real Amazon / JD / Tmall product detail URL (`external_url`)
- a real product image URL from the platform's CDN
- price in the source currency (USD / CNY)
- `provenance.origin_country` (US / JP / DE / FR / CN)
- `provenance.source_platform` (Amazon US, Amazon JP, JD, Tmall)
- `provenance.shipping_note` (海外直邮 / 国内现货)
- a Chinese-language marketing description (200-400 chars)
- a paraphrased user-review summary (100-200 chars)

## Cataloging artifacts

- **Raw agent outputs** (committed, reproducible source):
  `docs/research/raw/2026-05-24-intl-products.json` (20 entries)
  and `docs/research/raw/2026-05-24-cn-products.json` (25 entries).
- **Converter script**: `tools/import_real_products.py`. Takes the raw
  agent output and emits one JSON per product under
  `data/seed/<cat>/data/<product_id>.json` matching the existing seed
  schema, so the rest of the pipeline (ingest → BM25 → Chroma → SSE → iOS)
  needs no further change.
- **Generated seed files**: 45 new `p_*_intl_*.json` + `p_*_real_*.json`
  files under the eight category dirs.

## Catalog growth

| Before R6 | After R6 |
|---|---|
| 100 AI-gen products, 4 categories | 145 products (100 AI-gen + 45 real), 8 categories |
| All `provenance.source_platform = AI-gen (demo)` | Mix of AI-gen (`演示` badge) + Amazon US/JP, JD |
| Single-currency (CNY only) | Multi-currency (CNY + USD), iOS shows `(美元)` hint and per-currency cart totals |
| `external_url = null` everywhere | 45 real product detail URLs surfaced via "去原页" button |

## Caveats and honest limits

- **Amazon images** use the `m.media-amazon.com/images/I/<HASH>._SLnnnn_.jpg`
  pattern. Amazon's CDN doesn't restrict hotlinking from arbitrary clients,
  but image hashes rotate over time. If a card thumbnail 404s in the demo,
  the iOS `AsyncImage` falls back to the photo-placeholder; nothing breaks.
- **JD.com pages** block direct fetches from US-originated IPs with a 302
  to a risk handler. The agent surfaced these URLs from search-result
  snippets (which Google indexes from live pages); the SKU IDs were
  cross-referenced via repeated appearances in search results. A few SKUs
  flagged in the CN agent's caveats may need a once-over before final
  demo: 华为 GT4, 凯乐石 MT5-3, 牧高笛 冷山2.
- **Image URLs for some JD items** are placeholder paths on the same CDN
  pattern; the agent couldn't reach the live page to read the exact image
  hash. The iOS placeholder handles that gracefully.
- **License**: this is academic-research / private-demo usage. Real
  product images are linked, not republished. Source URLs are stored in
  `provenance.external_url` of each JSON for attribution.
- **No image embeddings** for the 45 new products yet — they reference
  remote CDN images, not local `data/seed/.../images/<id>.jpg`. CLIP
  visual retrieval (Round 3's path) still works for the 100 AI-gen
  products. Text retrieval (BM25 + dense) covers all 145. If we need
  visual retrieval over real-products, scrape and embed in a Round 7.

## Why this beats "expand catalog with more AI-gen"

- **Credibility for the defense**: judges can click "去原页" on a real
  product and verify the price + image + brand themselves.
- **PDF rubric §4.2 multimodal depth**: real images from real platforms
  feels meaningfully different from AI-generated visuals.
- **PDF rubric §工程质量**: surfacing the provenance + multi-currency cart
  is a clear engineering decision, documented honestly.
- **Round 5 weakness fix**: the "推荐一本书" → cosmetics regression goes
  away once `7_图书音像` actually has books.

## Catalog snapshot after R6

| Category | AI-gen seed | Real-added | Total |
|---|---:|---:|---:|
| `1_美妆护肤` | 25 | 5 (intl) + 5 (CN) = 10 | 35 |
| `2_数码电子` | 25 | 5 (intl) | 30 |
| `3_服饰运动` | 25 | 5 (intl) | 30 |
| `4_食品生活` | 25 | 5 (intl) | 30 |
| `5_母婴健康` | 0 | 5 (CN) | 5 |
| `6_家居家具` | 0 | 5 (CN) | 5 |
| `7_图书音像` | 0 | 5 (CN) | 5 |
| `8_户外运动` | 0 | 5 (CN) | 5 |
| **Total** | **100** | **45** | **145** |

Round 7+ direction: grow each new category (5/6/7/8) to 15-20 real
products so the "推荐一本书" / "助孕用品" / "登山徒步鞋" queries have
3-5 candidates to choose from (currently they'll surface all 5 in that
category since the catalog is too small to filter). For the demo we get
correct-category routing, which is the rubric-defending point.
