<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# > "Find image-text product datasets useful for training or evaluating a multimodal e-commerce search system, with real product photos and Chinese text. Examples I'm looking to confirm or beat: M5Product, RPC, Products10K, Fashion-Gen. For each, give license, size, image count, whether Chinese text is present, and download URL. Add any newer (2024-2026) alternatives you can verify."

I now have comprehensive information to build the full dataset reference table. Let me compile the response.

Here is a thorough dataset reference for multimodal e-commerce search with Chinese text, covering your four anchor datasets plus verified newer alternatives.

***

## Your Four Anchor Datasets

### M5Product

M5Product is a large-scale multimodal e-commerce pretraining benchmark released by SYSU, BJTU, PengCheng Lab, and Alibaba Group. It covers **5 modalities** (image, text, table, video, audio), making it the most modality-diverse dataset in this space.[^1][^2]


| Field | Detail |
| :-- | :-- |
| **Image count** | ~6 million image samples (part of 6M+ multimodal records) [^1] |
| **Text language** | Simplified Chinese — sourced from 1M Alibaba/Taobao merchants [^2] |
| **Size** | 6M+ multimodal pairs, 6,000+ categories, 5,000+ attributes [^2] |
| **License** | Custom non-commercial research license (must be university/research institution; no distribution; signed commitment letter required) [^3] |
| **Download** | `https://xiaodongsuper.github.io/M5Product_dataset/` — requires application via Alibaba's dataset portal [^1] |

**Notes:** The terms explicitly state only university researchers/faculty qualify. No commercial use, no redistribution.[^3]

***

### RPC (Retail Product Checkout)

RPC is a retail checkout-oriented object detection dataset from Megvii/Nanjing University, primarily meant for automated checkout scenarios.[^4]


| Field | Detail |
| :-- | :-- |
| **Image count** | 83,739 total (53,739 training exemplars + 30,000 validation/test checkout images) [^5] |
| **Text language** | **No Chinese text** — annotations are category IDs/bounding boxes only; product names in dataset are transliterated, not Chinese-language descriptions [^6] |
| **Size** | ~15 GB on Kaggle; 200 SKU categories [^5] |
| **License** | **CC BY-NC-SA 4.0** [^5] |
| **Download** | `https://rpc-dataset.github.io` → Kaggle (primary) or Baidu Drive (backup) [^5] |

**Notes:** Strong for product recognition benchmarking, but lacks Chinese-language text metadata — unsuitable as a Chinese text retrieval dataset without augmentation.

***

### Products-10K (Products10K)

Products-10K is a SKU-level product recognition dataset from JD AI Research (JD.com), focused on fine-grained product identification.[^7]


| Field | Detail |
| :-- | :-- |
| **Image count** | ~190,000 images (150K–190K depending on split) [^8] |
| **Text language** | Products are from JD.com (Chinese e-commerce), but published annotations are category IDs/graph labels — **title text not directly included** in the released CSVs [^9] |
| **Size** | ~10,000 SKUs across fashion, food, 3C, healthcare, household [^10] |
| **License** | Non-commercial research/educational; requires agreement via JD [^7][^9] |
| **Download** | OneDrive or JD Pan (passcode: `kecp4a`) — `https://products-10k.github.io` [^8] |

**Notes:** Images are real JD.com product photos and Chinese titles can be scraped from the underlying JD product pages, but the official dataset release focuses on image classification, not image-text retrieval.

***

### Fashion-Gen

Fashion-Gen is a high-resolution image-text fashion dataset from Element AI/Mila (Montreal).[^11]


| Field | Detail |
| :-- | :-- |
| **Image count** | 325,536 total (260,480 train / 32,528 val / 32,528 test); 293,008 paired with stylist captions [^12] |
| **Text language** | **English only** — professional stylist-written English captions [^11][^12] |
| **Size** | 1360×1360 px images; 48 main categories, 121 sub-categories [^12] |
| **License** | Academic/research use; download requires registration at `fashion-gen.com` (form-based, no explicit CC license) [^13][^14] |
| **Download** | `https://fashion-gen.com` (form registration); H5 files also mirrored informally [^13] |

**Notes:** Fashion-Gen is **not a Chinese-text dataset** — it cannot substitute for Chinese e-commerce text benchmarking.

***

## Verified Chinese Multimodal E-Commerce Datasets

These are datasets you should strongly consider adding to your matrix, all with verified Chinese text:

***

### MEP-3M (Multi-modal E-Commerce Products)

A large-scale Chinese e-commerce classification dataset from Hohai University, published in *Pattern Recognition* (2023).[^15]


| Field | Detail |
| :-- | :-- |
| **Image count** | 3M+ product images (stored in 599 `.rar` files, 67 GB total) [^16] |
| **Text language** | **Simplified Chinese** — product titles + OCR text extracted from product images [^17] |
| **Size** | 3M+ products, 599 fine-grained categories, hierarchical labels [^17] |
| **License** | Research use; contact `fanliu@hhu.edu.cn` to request access (no formal CC) [^16] |
| **Download** | `https://github.com/ChenDelong1999/MEP-3M` (images on request; annotation JSON publicly hosted) [^16] |


***

### MUGE (Multimodal Understanding and Generation Evaluation)

The **de facto standard Chinese multimodal retrieval benchmark**, released by Alibaba DAMO Academy + Zhejiang University via Alibaba's Tianchi platform (2021).[^18]


| Field | Detail |
| :-- | :-- |
| **Image count** | Retrieval task: ~100K image corpus (30,588 images in validation split); caption task: 50K train + 5K val + 10K test [^19][^18] |
| **Text language** | **Simplified Chinese** — real Taobao product titles and merchant descriptions [^20] |
| **Size** | Multiple sub-tasks: image-text retrieval (I2T and T2I), image captioning, text-to-image generation [^20] |
| **License** | Competition-style access through Tianchi; retrieval data accessible via ModelScope API (research use) [^19] |
| **Download** | `https://tianchi.aliyun.com/muge`; also via `MsDataset.load("muge")` on ModelScope [^19][^21] |


***

### Product1M

A weakly supervised multimodal instance-level cosmetics retrieval dataset presented at ICCV 2021.[^22]


| Field | Detail |
| :-- | :-- |
| **Image count** | 1M+ image-caption pairs (cosmetics, single-product and multi-product samples) [^22] |
| **Text language** | **Chinese/mixed** — crawled from Chinese e-commerce sites (captions are in Chinese) [^23] |
| **Size** | 1M+ pairs; fine-grained cosmetic categories [^22] |
| **License** | Research use; release agreement required (email authors) [^23] |
| **Download** | `https://github.com/zhanxlin/Product1M` — Google Drive link for train set [^23] |


***

## Newer Datasets (2024–2026)

### DanQing (2026) ✅ Best for pretraining

A 100M Chinese image-text pretraining corpus from Common Crawl (2024–2025 data), released Jan 2026 by DeepGlint.[^24]


| Field | Detail |
| :-- | :-- |
| **Image count** | ~100 million image-text pairs [^24] |
| **Text language** | **Simplified Chinese** — filtered/standardized from web crawl [^25] |
| **Size** | 100M pairs; multi-stage quality filtering including Chinese-CLIP cross-modal alignment [^26] |
| **License** | **CC BY 4.0** (commercial-friendly) [^24] |
| **Download** | `https://github.com/deepglint/DanQing`; HuggingFace paper page: `arxiv:2601.10305` [^24] |

**Notes:** Not purely e-commerce but covers product-adjacent web imagery; ideal for pretraining a Chinese CLIP backbone before fine-tuning on domain-specific data.

***

### LGS – Let's Go Shopping (2024)

A 15M English-language e-commerce image-caption dataset from NYU/Scale AI.[^27]


| Field | Detail |
| :-- | :-- |
| **Image count** | 15 million image-caption pairs [^27] |
| **Text language** | **English only** — scraped from English e-commerce sites [^27] |
| **Size** | 15M pairs; product images from clean retail/e-commerce pages [^28] |
| **License** | **BSD 3-Clause** (permissive, commercial-friendly) [^29] |
| **Download** | Filtered URL list shared under BSD 3-Clause; `arxiv:2401.04575` [^27] |

**Notes:** No Chinese text, but the most permissive license in this list and directly comparable to CLIP pretraining corpora.

***

### Qilin (2025) ✅ Best for search/retrieval evaluation

A multimodal search-and-recommendation dataset from Xiaohongshu (RedNote) + Tsinghua University, presented at SIGIR 2025.[^30]


| Field | Detail |
| :-- | :-- |
| **Image count** | 5,006,181 images across 1,983,938 notes [^30] |
| **Text language** | **Simplified Chinese** — real Xiaohongshu user queries, titles, post content [^30] |
| **Size** | 15,482 users; 57,188 queries; 2.5M actions; image-text + video notes [^30] |
| **License** | Research use (data filtered for safety/privacy; license not explicit CC — contact authors) [^30] |
| **Download** | `https://github.com/RED-Search/Qilin`; HuggingFace: `THUIR/Qilin` [^31][^32] |

**Notes:** Uniquely captures real user search sessions with behavioral signals (click, like, collect) — excellent for training or evaluating a live retrieval system. Not purely product-SKU focused, but covers commercial notes including product ads.

***

### EVADE-Bench (2025) ✅ Evaluation-only

A Chinese multimodal benchmark for evaluating evasive/misleading product content detection — useful for adversarial robustness evaluation.[^33]


| Field | Detail |
| :-- | :-- |
| **Image count** | 13,961 annotated product images + 2,833 text samples [^33] |
| **Text language** | **Simplified Chinese** — health/supplement/body-shaping product claims [^34] |
| **License** | **CC BY 4.0** [^35] |
| **Download** | `https://huggingface.co/datasets/koenshen/EVADE-Bench` [^34] |


***

## Quick Comparison Matrix

| Dataset | Images | Chinese Text | License | Best Use |
| :-- | :-- | :-- | :-- | :-- |
| **M5Product** | ~6M | ✅ Yes (Taobao) | Non-commercial, apply | Multimodal pretraining |
| **RPC** | 83,739 | ❌ No | CC BY-NC-SA 4.0 | Detection benchmarking |
| **Products-10K** | ~190K | ⚠️ Indirect (JD.com) | Non-commercial, apply | SKU recognition |
| **Fashion-Gen** | 325,536 | ❌ English only | Registration required | Fashion text-to-image |
| **MEP-3M** | 3M+ | ✅ Yes (+ OCR) | Research, on request | Classification/retrieval |
| **MUGE** | ~100K (retrieval) | ✅ Yes (Taobao) | Tianchi/ModelScope | T2I/I2T retrieval eval |
| **Product1M** | 1M+ | ✅ Yes (cosmetics) | Research, on request | Instance retrieval |
| **DanQing** | 100M | ✅ Yes (web) | **CC BY 4.0** ✅ | CLIP pretraining |
| **LGS** | 15M | ❌ English only | **BSD 3-Clause** ✅ | Pretraining (EN) |
| **Qilin** | 5M+ | ✅ Yes (Xiaohongshu) | Research | Search/rec evaluation |
| **EVADE-Bench** | 13,961 | ✅ Yes | **CC BY 4.0** ✅ | Safety/adversarial eval |

For a Chinese multimodal e-commerce search system specifically, the recommended stack is: **MUGE** as your primary text-to-image retrieval eval benchmark (it's the standard Chinese CLIP eval), **MEP-3M** or **M5Product** for supervised fine-tuning with Chinese product image-text pairs, and **DanQing** for any pretraining compute you can afford. **Qilin** is the strongest 2025 addition for evaluating a *search system* (rather than just image-text matching) since it includes real query logs and behavioral feedback.[^2][^36][^15][^30][^24]
<span style="display:none">[^100][^101][^102][^103][^104][^105][^106][^107][^108][^109][^110][^111][^112][^113][^114][^115][^116][^117][^118][^119][^120][^121][^122][^123][^124][^125][^126][^127][^128][^129][^130][^131][^132][^133][^134][^135][^136][^137][^138][^139][^140][^141][^142][^143][^37][^38][^39][^40][^41][^42][^43][^44][^45][^46][^47][^48][^49][^50][^51][^52][^53][^54][^55][^56][^57][^58][^59][^60][^61][^62][^63][^64][^65][^66][^67][^68][^69][^70][^71][^72][^73][^74][^75][^76][^77][^78][^79][^80][^81][^82][^83][^84][^85][^86][^87][^88][^89][^90][^91][^92][^93][^94][^95][^96][^97][^98][^99]</span>

<div align="center">⁂</div>

[^1]: https://xiaodongsuper.github.io/M5Product_dataset/

[^2]: https://arxiv.org/abs/2109.04275v1

[^3]: https://xiaodongsuper.github.io/M5Product_dataset/terms_of_use.html

[^4]: https://arxiv.org/abs/1901.07249

[^5]: https://rpc-dataset.github.io

[^6]: https://ar5iv.labs.arxiv.org/html/1901.07249

[^7]: https://neurohive.io/en/news/product-10k-new-large-scale-dataset-of-product-images/

[^8]: https://products-10k.github.io/challenge.html

[^9]: https://www.kaggle.com/competitions/products-10k/data

[^10]: https://products-10k.github.io

[^11]: https://arxiv.org/abs/1806.08317

[^12]: https://www.emergentmind.com/topics/fashion-gen-dataset

[^13]: https://github.com/menardai/FashionGenAttnGAN

[^14]: https://blog.csdn.net/fighting_Kitty/article/details/120005988

[^15]: https://www.sciencedirect.com/science/article/abs/pii/S0031320323002194

[^16]: https://github.com/ChenDelong1999/MEP-3M

[^17]: https://chendelong.world/publication/pr2023mep/

[^18]: http://lib.ia.ac.cn/news/newsdetail/68263

[^19]: https://www.alibabacloud.com/help/en/vrs/latest/dashvector-modelscope-play-with-multimodal-retrieval

[^20]: https://www.jiqizhixin.com/articles/2021-12-21-11

[^21]: https://tianchi.aliyun.com/muge

[^22]: https://arxiv.org/abs/2107.14572

[^23]: https://github.com/zhanxlin/Product1M

[^24]: https://arxiv.org/abs/2601.10305

[^25]: https://huggingface.co/papers/2601.10305

[^26]: https://www.youtube.com/watch?v=HH-X0YUTue4

[^27]: https://arxiv.org/abs/2401.04575

[^28]: https://ai-scholar.tech/en/articles/large-language-models/lets-go-shopping-LGS-dataset

[^29]: https://arxiv.org/html/2401.04575v2

[^30]: https://arxiv.org/abs/2503.00501

[^31]: https://huggingface.co/papers/2503.00501

[^32]: https://huggingface.co/posts/AdinaY/245501501333845

[^33]: https://arxiv.org/abs/2505.17654

[^34]: https://arxiv.org/abs/2505.17654v3

[^35]: https://openreview.net/forum?id=1IAgMkztsC

[^36]: https://github.com/OFA-Sys/Chinese-CLIP/blob/master/Results.md

[^37]: https://www.futurebeeai.com/dataset/ocr-dataset/chinese-product-image-ocr-dataset

[^38]: https://ui.adsabs.harvard.edu/abs/2019arXiv190107249W/abstract

[^39]: https://arxiv.org/abs/2109.04275

[^40]: https://www.semanticscholar.org/paper/RPC:-A-Large-Scale-Retail-Product-Checkout-Dataset-Wei-Cui/8b640874dcfc3fe62fd52f043b9c9ca358426371

[^41]: https://openaccess.thecvf.com/content/CVPR2022/papers/Dong_M5Product_Self-Harmonized_Contrastive_Learning_for_E-Commercial_Multi-Modal_Pretraining_CVPR_2022_paper.pdf

[^42]: https://github.com/DIYer22/retail_product_checkout_tools

[^43]: https://www.scribd.com/document/925760944/Products-10K-A-Large-scale-Product-Recognition-Dataset

[^44]: https://mmlab.ie.cuhk.edu.hk/projects/DeepFashion.html

[^45]: https://www.marketresearch.com/Expert-Market-Research-v4220/China-Commerce-Forecast-41222188/

[^46]: https://multimodality.group/post/mep-3m/

[^47]: https://data-starcloud.pcl.ac.cn/iearthdata/7

[^48]: https://stackoverflow.com/questions/67739240/from-where-to-download-fashion-gen-dataset

[^49]: https://arxiv.org/html/2502.20196v1

[^50]: https://www.youtube.com/watch?v=YjUe-v8EEV4

[^51]: http://yongfeng.me/dataset/

[^52]: https://dl.acm.org/doi/10.1145/3773966.3777958

[^53]: https://github.com/ChenDelong1999/MEP-3M/blob/main/dataset_info.xlsx

[^54]: https://openreview.net/pdf/fd0b72994ed77ad43428fe783447e56f335febad.pdf

[^55]: https://arxiv.org/html/2604.00513v2

[^56]: https://arxiv.org/html/2505.17654v1

[^57]: https://arxiv.org/html/2402.13587v2

[^58]: https://www.facebook.com/chinascio/posts/chinas-high-quality-dataset-push-is-showing-real-momentum-100000-high-quality-da/1377243071096303/

[^59]: https://www.semanticscholar.org/paper/Fashion-Gen:-The-Generative-Fashion-Dataset-and-Rostamzadeh-Hosseini/318c4c25d86511690cc5df7b041a6392e8cc4ea8

[^60]: https://arxiv.org/html/2502.15979v1

[^61]: https://kdd2025.kdd.org/datasets-and-benchmarks-track-papers-2/

[^62]: http://arxiv.org/abs/1806.08317

[^63]: https://arxiv.org/html/2605.17366v1

[^64]: https://pmc.ncbi.nlm.nih.gov/articles/PMC12218582/

[^65]: https://dl.acm.org/doi/abs/10.1145/3773966.3777958

[^66]: https://eval.ai/web/challenges/challenge-page/2099/overview

[^67]: https://proceedings.mlr.press/v235/peng24c.html

[^68]: https://gts.ai/dataset-download/e-commerce-product-image-classification-dataset/

[^69]: https://ninglab.github.io/eCeLLM/

[^70]: https://www.kaggle.com/datasets/warcoder/visual-product-recognition

[^71]: https://github.com/ninglab/eCeLLM

[^72]: https://www.cnblogs.com/xuehuiping/p/15983541.html

[^73]: http://x.com/alibaba_cloud/status/1247745770427576320

[^74]: https://raw.githubusercontent.com/mlresearch/v235/main/assets/peng24c/peng24c.pdf

[^75]: https://ar5iv.labs.arxiv.org/html/2008.10545

[^76]: https://github.com/MUGE-2021/image-retrieval-baseline

[^77]: https://huggingface.co/JUNJIE99/MMRet-MLLM-S2

[^78]: https://huggingface.co/JUNJIE99/MMRet-base

[^79]: https://dl.acm.org/doi/abs/10.1145/3581783.3612408

[^80]: https://huggingface.co/JUNJIE99/MMRet-base/blob/60074383f487034cbd926f0baeb9d875f9a27e1a/README.md

[^81]: https://github.com/OFA-Sys/Chinese-CLIP/issues/17

[^82]: https://github.com/LiJiaBei-7/Awesome-Cross-Lingual-Cross-Modal-Retrieval

[^83]: https://arxiv.org/html/2503.00501v1

[^84]: https://github.com/OFA-Sys/Chinese-CLIP/blob/master/README_En.md

[^85]: https://pmc.ncbi.nlm.nih.gov/articles/PMC9824814/

[^86]: https://aclanthology.org/2025.acl-long.935.pdf

[^87]: https://www.semanticscholar.org/paper/68ae1669cef10d0a5e7a6a5e356ebd3e7e4d9e39

[^88]: https://github.com/ChenAnno/Real20M_ACMMM2023

[^89]: https://sigir2025.dei.unipd.it/detailed-program/paper?paper=a981f2b708044d6fb4a71a1463242520

[^90]: https://mmasia2025.org/multimodal_multiethnic

[^91]: https://github.com/RED-Search/Qilin

[^92]: https://github.com/koenshen/EVADE-Bench

[^93]: https://developer.aliyun.com/ask/601095

[^94]: https://www.kaggle.com/datasets/platesmania/license-plates-datasets-download-images-byself/code

[^95]: https://www.kaggle.com/datasets/nurimammasri/rockpaperscissorsdicoding

[^96]: https://voxel51.com/blog/exploring-google-research-kaggle-image-matching-challenge-2023-dataset

[^97]: https://wenku.csdn.net/answer/3zft2eqb4x

[^98]: https://huggingface.co/modelscope

[^99]: https://arxiv.org/html/2309.17164v2

[^100]: https://arxiv.org/html/2402.12193v2

[^101]: https://zenodo.org/records/14783016

[^102]: https://github.com/rawatpranjal/industry-datasets

[^103]: https://aclanthology.org/2024.findings-acl.184.pdf

[^104]: https://servicedesk.surf.nl/wiki/plugins/viewsource/viewpagesrc.action?pageId=158040294

[^105]: https://pmc.ncbi.nlm.nih.gov/articles/PMC12157099/

[^106]: https://stackoverflow.com/questions/61042900/train-validation-data-split-labels-available-but-no-classes

[^107]: https://medium.com/@glami-engineering/glami-1m-multilingual-image-text-fashion-dataset-a72691b6fedb

[^108]: https://github.com/google-research-datasets/wit-retrieval

[^109]: https://github.com/glami/glami-1m

[^110]: https://arxiv.org/html/2506.02291v1

[^111]: https://arxiv.org/abs/2211.14451

[^112]: https://blender.cs.illinois.edu/course/fall22/Assignment1.docx

[^113]: https://ar5iv.labs.arxiv.org/html/1806.08317

[^114]: https://pmc.ncbi.nlm.nih.gov/articles/PMC11950592/

[^115]: https://papers.neurips.cc/paper_files/paper/2022/file/a90b9a09a6ee43d6631cf42e225d73b4-Paper-Datasets_and_Benchmarks.pdf

[^116]: https://www.datasetlist.com

[^117]: https://bmvc2022.mpi-inf.mpg.de/0607.pdf

[^118]: https://www.microsoft.com/en-us/research/wp-content/uploads/2019/01/1803.09010.pdf

[^119]: https://blog.gitcode.com/93620fa533f1bbf41ec82dfde170f7aa.html

[^120]: https://www.emergentmind.com/topics/danqing-dataset

[^121]: https://huggingface.co/datasets/OpenStellarTeam/Chinese-EcomQA/raw/main/README.md

[^122]: https://tianchi.aliyun.com/specials/promotion/muge_mews

[^123]: https://huggingface.co/papers/2502.20196

[^124]: https://www.iieta.org/journals/ts/paper/10.18280/ts.380119

[^125]: https://www.arxiv.org/abs/2502.20196

[^126]: https://github.com/OpenStellarTeam/ChineseEcomQA

[^127]: https://www.kaggle.com/competitions/google-universal-image-embedding/discussion/337384

[^128]: https://www.anyscale.com/blog/cross-modal-search-for-e-commerce-building-and-scaling-a-cross-modal-image-retrieval-app

[^129]: https://huggingface.co/datasets/OpenStellarTeam/Chinese-EcomQA

[^130]: https://www.ijcai.org/proceedings/2024/197

[^131]: https://pmc.ncbi.nlm.nih.gov/articles/PMC12868852/table/Tab2/

[^132]: https://pmc.ncbi.nlm.nih.gov/articles/PMC13039621/table/table1-20552076261438923/

[^133]: https://huggingface.co/papers/2401.04575

[^134]: https://www.facebook.com/100091859925844/posts/in-january-2026-alibaba-reached-a-significant-milestone-as-its-qwen-family-of-ai/830366396702017/

[^135]: https://huggingface.co/datasets/forza61/e-commerce-image-captioning

[^136]: https://openaccess.thecvf.com/content/CVPR2025/supplemental/Kim_GENIUS_A_Generative_CVPR_2025_supplemental.pdf

[^137]: https://github.com/Taobao-live/Product-Seeking

[^138]: https://segmentfault.com/a/1190000040509106/en

[^139]: https://www.emergentmind.com/papers/2401.04575

[^140]: https://github.com/greenblue96/Taobao-Serendipity-Dataset

[^141]: https://www.linkedin.com/posts/bakulesh-rane-82205517a_lets-go-shopping-lgs-dataset-a-large-scale-activity-7152286164459122688-Vpl3

[^142]: https://huggingface.co/collections/baobuihf/datasets

[^143]: https://arxiv.org/pdf/2304.03669.pdf

