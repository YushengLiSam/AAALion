<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# > "List public or semi-public APIs that return real product data for Chinese e-commerce (Taobao Open Platform, JD Union, Pinduoduo Open Platform, Xiaohongshu Notes API, Douyin commerce API). For each: auth requirement, rate limit, whether sandbox/test data is real or synthetic, and whether a college student team can register without a business license. Include any unofficial mirrors or aggregated datasets on GitHub (e.g., `xxx-spider`) that have current real data dumps."

Here's a comprehensive breakdown of all five platforms' official APIs, their constraints, and the GitHub ecosystem around them.

***

## Official Platform APIs

### 1. Taobao Open Platform (`open.taobao.com`)

**Auth:** OAuth 2.0 with App Key + App Secret + user Access Token. All three credentials are required per call, obtained after application approval. The sandbox entry is `http://gw.api.tbsandbox.com/router/rest`, and production is `http://gw.api.taobao.com/router/rest`.[^1]

**Rate Limits (2025):**


| Account Type | Rate Limit | Core Access |
| :-- | :-- | :-- |
| Personal developer | ≤ 10 req/min [^2] | Basic product query, store info |
| Enterprise developer | ≤ 100 req/min [^2] | Orders, payments, AI product selection |
| ISV (service provider) | Up to 500 req/min [^2] | Multi-store, bulk order management |

**Sandbox data:** The sandbox (`tbsandbox.com`) is **fully synthetic** — it's an independent test environment with fake accounts (e.g., `sandbox_c_1`, password `taobao1234`) that are fully isolated from real online data. The production formal environment shares **live data** with the online system with a 5,000 calls/day cap for testing.[^1]

**College team registration:** ❌ **Effectively blocked.** Enterprise accounts require a business license (营业执照), organization code certificate, and tax registration certificate. Personal developer accounts exist and only need ID + face recognition, but are restricted to basic, non-commerce-sensitive APIs. Since 2025, personal accounts can no longer call `taobao.trade.fullinfo.get` (order details). Foreign developers face additional friction — reports indicate that without a Chinese entity or Alipay real-name verification, access is practically unavailable.[^3][^2][^4]

***

### 2. JD Union / JD Open Platform (`jos.jd.com` / `media.jd.com`)

**Auth:** Two separate paths:

- **JD Open Platform (JOS):** OAuth 2.0, App Key + App Secret → Access Token per seller authorization.[^5][^6]
- **JD Union (CPS affiliate):** Simpler static API key (120-day validity), retrieved from the Union backend under "我的工具 → 我的API".[^7]

**Rate Limits:** Not officially published in a single table. JD Union high-privilege interfaces (order data, full product feeds) require enterprise account with >30,000 orders/month to unlock. Standard Union API keys give access to product promotions and link generation with no published hard ceiling for basic calls.[^8]

**Sandbox data:** JOS provides a test environment, but like Taobao's sandbox, it uses **synthetic/mock data** isolated from production.[^6]

**College team registration:** ⚠️ **Partially possible.** JD distinguishes clearly between individual and enterprise developer tiers. Individual developer registration requires only national ID (personal real-name auth) and grants access to learning/testing interfaces. JD Union registration similarly allows individuals to register with an ID number. However, full e-commerce product catalog and order-level APIs require enterprise certification with business license. The basic affiliate link/product info endpoints are accessible to individuals without a business license.[^9][^6][^7]

***

### 3. Pinduoduo Open Platform (`open.pinduoduo.com`)

**Auth:** OAuth 2.0 with Client ID + Client Secret + merchant authorization. Callback URLs must be HTTPS and ICP-registered. Signature generation follows a platform-specific HMAC-SHA256 scheme.[^10]

**Rate Limits:** Officially ~100 calls/minute for standard accounts; exceeding this triggers temporary bans. Specific per-endpoint quotas are unlocked per-application after audit.[^11]

**Sandbox data:** Sandbox exists but is **synthetic**. The platform provides test keys for local debugging, but real product search (`pdd.goods.search`) only returns live data in production mode.[^10]

**College team registration:** ⚠️ **Individual developer registration is technically allowed** — the platform lets you choose between "personal developer" and "enterprise developer" at signup. Individual registration requires phone + real-name verification without a business license. However, the "多多客联盟" (Duo Duo Ke affiliate) path — which is the most useful for product data — has tutorials mostly targeting enterprise flow and requires filling in administrator/legal representative/company information in practice. Pure individual path gives access to basic goods search and link generation, but commerce-grade interfaces need merchant authorization.[^12][^13][^10]

***

### 4. Xiaohongshu (RED/小红书) Notes API

**Auth (Official):** OAuth 2.0 with App Key + App Secret + Access Token (2-hour expiry, refresh via OAuth 2.0). Available only through the "Ark" developer platform at `school.xiaohongshu.com`. Sandbox entry requires a sandbox Ark login at `flssandbox.xiaohongshu.com`.[^14][^15]

**Rate Limits:** Not publicly documented. The official API is described as a **heavily invitation-only partnership program** — you must contact RED's tech support team to even enter a sandbox test cycle, then coordinate a production test cycle. Public documentation primarily covers the "brand partner" integration workflow for e-commerce sellers.[^15]

**Sandbox data:** The sandbox is **invitation-only** and uses **synthetic data** for integration testing. Moving to production requires a coordinated test with RED's support team.[^15]

**College team registration:** ❌ **Functionally impossible without business credentials.** Official Xiaohongshu developer access is gated behind enterprise certification (business license + company materials) and approval from RED's business team. Developer account creation requires submitting营业执照 (business license) and waiting 1–5 business days for audit. There is no public individual developer tier for the notes/commerce API — this is a B2B-only program.[^16][^17]

**Unofficial third-party API (JustOneAPI):** A semi-public workaround exists at `justoneapi.com`, which exposes endpoints like `/api/xiaohongshu/get-user-note-list/v4` with token-based auth. Pricing runs from free (50 requests/month) to \$399.99/month for ~120,000 requests. This is a scraping proxy, not an official integration — data is real but Terms of Service compliance is unclear.[^18][^19]

***

### 5. Douyin Commerce API (`op.jinritemai.com` / `open.douyin.com`)

**Auth:** OAuth 2.0 with Client ID + Client Secret + seller OAuth authorization. Commerce-specific endpoints (product/order management) live on the e-commerce open platform `op.jinritemai.com`, while general content APIs (video, user) are at `open.douyin.com`.[^20][^21]

**Rate Limits:** Not centrally published. The Douyin Open Platform does not list a global cap, but individual interfaces specify their own quotas during application. Individual developers have no deposit requirement; ISVs (third-party software vendors) must pay a 10,000–50,000 RMB guarantee deposit.[^22]

**Sandbox data:** A sandbox environment exists for integration testing; test data is **synthetic**. Real product data requires a live merchant store that has granted OAuth authorization to your application.[^20]

**College team registration:** ❌ **Requires active merchant store + business license.** To access commerce APIs (product listings, orders, live-stream commerce), you must have an operating Douyin shop, which requires 营业执照 (business license), legal representative ID, and a software copyright certificate for self-developed apps. A personal developer account on `open.douyin.com` can access general content APIs (video metadata, user info) without a business license, but any e-commerce-related interface requires the merchant authorization chain.[^23][^21]

***

## Quick Comparison Table

| Platform | Auth Mechanism | Personal Dev (No Biz License)? | Sandbox Data | Rate Limit (Personal) |
| :-- | :-- | :-- | :-- | :-- |
| Taobao | OAuth 2.0, 3-credential chain | ⚠️ Limited (no order APIs since 2025) [^2] | Synthetic (isolated sandbox) [^1] | 10 req/min [^2] |
| JD Union | Static API key (120-day) | ✅ For product/affiliate links [^7] | Synthetic | Not published (basic) |
| JD Open (JOS) | OAuth 2.0 | ⚠️ Learning/test only [^6] | Synthetic | Not published |
| Pinduoduo | OAuth 2.0 + HMAC sig | ⚠️ Basic goods search only [^12] | Synthetic | ~100 req/min [^11] |
| Xiaohongshu | OAuth 2.0 (invitation-only) | ❌ Enterprise only [^16] | Synthetic (invite-only) [^15] | Undisclosed |
| Douyin Commerce | OAuth 2.0 + merchant auth | ❌ Biz license + active store [^23] | Synthetic | Undisclosed |


***

## Unofficial Mirrors, Scraper APIs \& GitHub Datasets

### Active/Semi-Active Scrapers

**`NanmiCoder/MediaCrawler`** (GitHub, ~40k+ stars) — Python async framework using Playwright to crawl Xiaohongshu notes+comments, Douyin videos+comments, Weibo, Bilibili, Kuaishou, Tieba, and Zhihu. Data is real and live (scraped from logged-in sessions). Supports MySQL, CSV, and JSON output. Uses IP proxy pool integration. Licensed as non-commercial/learning only. This is the most actively maintained multi-platform crawler for Chinese social platforms.[^24][^25][^26]

**`Northxw/Pinduoduo`** (GitHub) — Scrapy-based spider for Pinduoduo product data via the PDD product API (unofficial mobile API calls). Last updated 2019–2022; may be broken against current anti-bot measures.[^27][^28]

**`SZFsir/pddSpider`** (GitHub, ~311 stars) — Pinduoduo spider for all products + comments using Selenium. Updated as of 2022.[^29]

**`wfgsss/pinduoduo-scraper-example`** (GitHub) — Scrapes product price, sales volume, and images from Pinduoduo.[^30]

**`davide97l/ecommerce_price_scraper`** (GitHub) — Multi-platform price scraper targeting Taobao, Tmall, and JD.com.[^31]

**`lorenzowne/xiaohongshu-scraper`** (GitHub) — Pulls structured content data (categories, posts, metadata) directly from XHS pages.[^32]

**`KaitoHH/xiaohongshu-spider-visualizer`** (GitHub, 2018) — Distributed XHS crawler with Celery + Docker + visualization. Older but architecturally clean for reference.[^33]

### Third-Party API Aggregators (Semi-Public, Paid)

**TikHub (`tikhub.io`)** — Paid API (\$0.001–\$0.01/request, ~50 free requests on signup) covering Douyin product details, video data, live-stream commerce, Xiaohongshu notes, and 14 other platforms. Rate limit: 10 RPS default. Data is real and scraped from live platforms. No business license required — just email signup. The Python SDK is open source at `TikHub/TikHub-API-Python-SDK` on GitHub.[^34][^35][^36][^37]

**JustOneAPI (`justoneapi.com`)** — Covers Xiaohongshu note lists, user profiles, note details, and comment threads with token auth. Free tier: 50 requests/month at 1,000 req/hr; Pro is \$39.99/month for 8,000 requests. Data is real.[^38][^19][^18]

**TMAPI (`tmapi.top`)** — Unofficial proxy providing Pinduoduo product detail by ID (title, price, SKUs, inventory, images) via `apiToken`. Requires account registration but no business license.[^39]

### Academic Datasets with Real Data

**`rucliujn/JDsearch`** (GitHub) — Released via SIGIR 2023, contains 173,831 users, 12.8M products, and 26.6M interactions from JD.com's real search system. Available via JD Cloud Disk (password: `zz4qza`) or by emailing the author. Data is real but anonymized.[^40][^41]

**`jdcomsearch/jd-pretrain-data`** (GitHub, JD.com official) — Query/item/category data from JD's search system for NLP pretraining. IDs are encoded for business confidentiality but query-item pairs are real.[^42]

**`Taobao-live/Product-Seeking`** (GitHub) — 474k image-title pairs and 101k frame-description pairs from Taobao Mall and Taobao Live. Real product data.[^43]

**`TAOBAO-MM`** (GitHub Pages) — 8.79M user interaction logs, 35.4M items, 99M labeled samples from Taobao. All IDs anonymized; raw content not released due to copyright.[^44]

**`products-10k`** (Kaggle/JD) — 10,000 real JD.com products with human-labeled categories covering fashion, 3C, food, healthcare, etc..[^45]

***

## Practical Recommendations for a College Team

For a student team **without a business license**, your realistic options ranked by accessibility:

1. **JD Union API** — Most accessible official path; individual registration with just an ID card gets you product info and affiliate link generation.[^7]
2. **Pinduoduo Open Platform (individual track)** — Basic goods search (`pdd.goods.search`) is reachable after personal real-name verification, though deep commerce APIs are blocked.[^12]
3. **TikHub** — No business license needed, real Douyin and XHS data, generous free tier, straightforward signup.[^35]
4. **NanmiCoder/MediaCrawler** — For XHS + Douyin raw content scraping; requires a logged-in account (Chinese phone number needed), not a business entity.[^24]
5. **Academic datasets** — JDsearch and jd-pretrain-data are the cleanest no-friction paths to bulk real product data for ML/research.[^41][^42]

Note that all scraper-based approaches (MediaCrawler, pddSpider, etc.) operate in a legal gray area — they violate most platforms' ToS even if technically functional. For production or published research, the official API paths or officially released academic datasets are significantly safer.[^46][^24]
<span style="display:none">[^100][^101][^102][^103][^104][^105][^106][^107][^108][^109][^110][^111][^112][^113][^114][^115][^116][^117][^118][^119][^120][^121][^122][^47][^48][^49][^50][^51][^52][^53][^54][^55][^56][^57][^58][^59][^60][^61][^62][^63][^64][^65][^66][^67][^68][^69][^70][^71][^72][^73][^74][^75][^76][^77][^78][^79][^80][^81][^82][^83][^84][^85][^86][^87][^88][^89][^90][^91][^92][^93][^94][^95][^96][^97][^98][^99]</span>

<div align="center">⁂</div>

[^1]: https://open.alitrip.com/docs/doc.htm?treeId=19\&articleId=102834\&docType=1

[^2]: https://cloud.tencent.com/developer/article/2566727

[^3]: https://www.yunzhanxinxi.com/detail/2155/0.html

[^4]: https://www.reddit.com/r/taobao/comments/yeqnuq/i_want_to_know_the_criteria_for_using_tmall_or/

[^5]: https://blog.csdn.net/api_open/article/details/149418746

[^6]: https://blog.csdn.net/api_open/article/details/147054497

[^7]: https://blog.csdn.net/Andyfu2019/article/details/104074798

[^8]: https://news.jd.com/153_1.html

[^9]: https://www.yunzhanxinxi.com/detail/1839/0.html

[^10]: https://blog.csdn.net/api_open/article/details/149246327

[^11]: https://developer.aliyun.com/article/1686691

[^12]: https://developer.aliyun.com/article/1632049

[^13]: https://www.kancloud.cn/bycc/xsczl/2333446

[^14]: https://school.xiaohongshu.com/en/open/quick-start/how-to-get-app-key.html

[^15]: https://school.xiaohongshu.com/en/open/quick-start/workflow.html

[^16]: https://blog.csdn.net/2503_92478457/article/details/154178750

[^17]: https://www.youtube.com/watch?v=E10u_AFo88o

[^18]: https://docs.justoneapi.com/en/api/xiaohongshu-rednote/user-published-notes-v4

[^19]: https://rapidapi.com/dataapiman/api/xiaohongshu-all-api/pricing

[^20]: https://www.kancloud.cn/zwgj/zwcms/2727599

[^21]: https://blog.csdn.net/api_open/article/details/149418660

[^22]: https://opc.csdn.net/698463497bbde9200b985306.html

[^23]: https://blog.51cto.com/u_16482102/13287049

[^24]: https://zread.ai/NanmiCoder/MediaCrawler/package.json

[^25]: https://publicrepo.dev/repo/NanmiCoder/MediaCrawler

[^26]: https://nanmicoder.github.io/MediaCrawler/项目架构文档.html

[^27]: https://github.com/Northxw/Pinduoduo

[^28]: https://github.com/Northxw/Pinduoduo/blob/master/pinduoduo/spiders/pdd.py

[^29]: https://github.com/topics/pdd

[^30]: https://github.com/wfgsss/pinduoduo-scraper-example/releases

[^31]: https://github.com/davide97l/ecommerce_price_scraper

[^32]: https://github.com/lorenzowne/xiaohongshu-scraper

[^33]: https://github.com/KaitoHH/xiaohongshu-spider-visualizer

[^34]: https://github.com/TikHub/TikHub-API-Python-SDK

[^35]: https://tikhub.io/pricing

[^36]: https://tikhub.io

[^37]: https://tikhub.io/api-reference

[^38]: https://docs.justoneapi.com/en/api/xiaohongshu-rednote/

[^39]: https://tmapi.top/docs/pinduoduo/item-detail/get-item-detail-by-id

[^40]: https://www.kaggle.com/datasets/duuuscha/jd-search-dataset

[^41]: https://github.com/rucliujn/JDsearch

[^42]: https://github.com/jdcomsearch/jd-pretrain-data

[^43]: https://github.com/Taobao-live/Product-Seeking

[^44]: https://taobao-mm.github.io

[^45]: https://products-10k.github.io

[^46]: https://blog.csdn.net/ecommerceAPI/article/details/135533612

[^47]: https://developers.coupangcorp.com/hc/en-us/articles/20414599556889-Introduction-of-Open-API-rate-limit-policy

[^48]: https://www.coppin.edu/iss-registration-requirements

[^49]: https://otcommerce.com/pinduoduo-api-dropshipping/

[^50]: https://community.openai.com/t/getting-openais-api-rate-limit-reached-while-i-never-used-the-api-before/706448

[^51]: https://law.illinois.edu/admissions/jd-admissions/jd-application/

[^52]: https://www.howtotao.com/taobao-official-dropshipping/

[^53]: https://www.american.edu/wcl/impact/initiatives-programs/health/institute/requirement.cfm

[^54]: https://juejin.cn/post/7507840946038571018

[^55]: https://open.alitrip.com/docs/doc.htm?treeId=735\&articleId=121120\&docType=1

[^56]: https://www.lsac.org

[^57]: https://rapidapi.com/ShowApi/api/pinduoduo-data-service/playground/apiendpoint_cfca076d-07bb-4fde-99e6-4edebd67b0eb

[^58]: https://open.taobao.global/doc/api.htm

[^59]: https://www.instagram.com/p/DVOPUv4EjdA/

[^60]: https://www.businessoffashion.com/news/news-analysis/pinduoduo-wants-its-data-back-from-alibaba/

[^61]: https://blog.csdn.net/api_open/article/details/149245815

[^62]: https://skywork.ai/skillhub/xiaohongshu-rednote-user-published-notes-api/

[^63]: https://open.fliggy.com/docs/doc.htm?treeId=23\&articleId=120801\&docType=1

[^64]: https://www.oceanengine.io/resource/register-douyin-ads-account-outside-of-china

[^65]: https://www.domatters.com/e-commerce-entry-guide/

[^66]: https://marketingtochina.com/douyin-ecommerce/

[^67]: https://marketplace.webkul.com/pinduoduo-social-commerce-app/

[^68]: https://www.yicaiglobal.com/news/tiktoks-sister-app-douyin-hasnt-opened-registration-for-intl-users-source-says/

[^69]: https://appinchina.co/blog/the-complete-guide-to-e-commerce-on-douyin-tiktok/

[^70]: https://www.youtube.com/watch?v=2szJkU05jsE

[^71]: https://chinadvisory.com/how-to-open-douyin-store-in-2024/

[^72]: https://blog.csdn.net/yong823_api/article/details/141135020

[^73]: https://www.21cloudbox.com/china-technology/what-is-pinduoduo-and-how-to-get-started.html

[^74]: https://mcpmarket.com/server/taobao-scraper

[^75]: https://www.retailgators.com/taobao.php

[^76]: https://github.com/KaitoHH/xiaohongshu-spider-visualizer/blob/master/xiaohongshu.py

[^77]: https://github.com/topics/little-red-book

[^78]: https://github.com/mosalen/Scrapy-E-commerce-Website-Crawler

[^79]: https://developers.tiktok.com/doc/tiktok-api-v2-rate-limit?enter_method=left_navigation

[^80]: https://opengateway.telefonica.com/en/news/article/sandbox-test-apis-open-gateway-in-applications

[^81]: https://dlthub.com/context/source/tiktok-business

[^82]: https://polyapi.com/Help/PlatDesc?query=47

[^83]: https://open.taobao.global/doc/doc.htm

[^84]: https://lbsyun.baidu.com/index.php?title=FAQ%2Fauthentication

[^85]: https://redocly.com/blog/sandbox-environments-reality-check

[^86]: https://juejin.cn/post/7400328872582955042

[^87]: https://github.com/zhangru5151/Scraper/blob/main/wiki/MiniPie Scraper: The Easiest Web Scraping and Monitoring Tool.md

[^88]: https://github.com/topics/product-scraper

[^89]: https://www.facebook.com/0xSojalSec/posts/chinese-companies-are-making-substantial-profits-by-illegally-reselling-api-acce/1520639059590492/

[^90]: https://github.com/NanmiCoder/MediaCrawler

[^91]: https://developer.fedex.com/api/en-us/guides/ratelimits.html

[^92]: https://dragontrail.com/resources/blog/how-to-register-for-a-xiaohongshu-account-from-overseas

[^93]: https://www.drifttle.com/douyin-enterprise-account-setup

[^94]: https://www.bls.gov/ooh/legal/lawyers.htm

[^95]: https://www.linkedin.com/posts/dan-yu-77a91789_socialmedia-rednote-activity-7356135401201258496-oQNX

[^96]: https://www.youtube.com/watch?v=8SbyUyO8tb0

[^97]: https://devcommunity.x.com/t/ive-a-sugestion-about-pay-per-use/260358

[^98]: https://community.developer.atlassian.com/t/2026-point-based-rate-limits/97828

[^99]: https://onlinelibrary.wiley.com/doi/full/10.1002/poi3.70026

[^100]: https://public.com/disclosures/individual-api-program

[^101]: https://fortune.com/2026/02/12/chinese-tech-xiaohongshu-tiktok-shop-southeast-asia/

[^102]: https://www.facebook.com/yicaiglobal/posts/douyin-tiktoks-sister-app-in-china-did-not-open-its-registration-to-internationa/1015083800662953/

[^103]: https://milvus.io/ai-quick-reference/what-is-the-openai-api-rate-limit-and-how-does-it-work

[^104]: https://spider.cloud/scrapers/github-scraper

[^105]: https://brandasia.com.au/news/how-much-do-rednote-influencers-charge-for-collaboration/

[^106]: https://help.yahooinc.com/dsp-api/docs/rate-limits

[^107]: https://www.youtube.com/watch?v=SyWhp_lRCU4

[^108]: https://platform.kimi.ai/docs/pricing/limits

[^109]: https://community.openai.com/t/rate-limit-reached-for-10ktpm-200rpm/381878

[^110]: https://skywork.ai/skillhub/xiaohongshu-rednote-user-profile-api/

[^111]: https://community.openai.com/t/rate-limits-for-preview-models/662894

[^112]: https://www.barchart.com/story/news/30499159/tiktok-refugees-are-pouring-to-xiaohongshu-heres-what-you-need-to-know-about-the-rednote-app

[^113]: https://github.com/topics/pingduoduo

[^114]: https://boxku.com.my/blog/how-to-register-pinduoduo.html

[^115]: https://www.youtube.com/watch?v=PazTd-NTQys

[^116]: https://blog.tikhub.io/en/article/9

[^117]: https://www.youtube.com/watch?v=VOtLdT-YUYw

[^118]: https://www.trustpilot.com/review/tikhub.io

[^119]: https://docs.tikhub.io

[^120]: https://www.kancloud.cn/worldidc/worldidc_sqtg/1598649

[^121]: https://api.tikhub.io

[^122]: https://www.kancloud.cn/feichuangwangluo/fcsqtg/2252916

