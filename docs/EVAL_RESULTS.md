# RAG 检索质量评测 - 当前结果

> 评测脚本: `rag/eval/run.py` (CLI) + `rag/eval/report.py` (HTML 看板) + `rag/eval/image_eval.py` (多模态)
> 评测代码: `rag/eval/core.py`
> Golden 集: `rag/eval/golden.jsonl` - 68 cases / 58 positive / 10 no-match / 11 带 `forbidden_product_ids`
> 图像索引: `data/.chroma/products_image` collection - 145 vectors (OpenCLIP ViT-B/32)

## 怎么跑

在装好 `server/requirements.txt` 的 Python 环境中:

```bash
# 文字检索 eval (主)
python -m rag.ingest.run
python -m rag.eval.report

# 多模态图像检索 eval (拍照找货 / bonus 4.2)
pip install open_clip_torch              # 一次性,~200MB
python -m rag.ingest.run_image           # 145 张图 → 512-d 向量 (MPS 约 30s)
python -m rag.eval.image_eval
```

Windows 上可用项目已有 Docker 镜像复现:

```powershell
docker build -f Dockerfile.rag -t aaalion-rag:latest .
docker run --rm -e ANONYMIZED_TELEMETRY=False -e CHROMA_TELEMETRY=False `
  -e RAG_FAST_PATH=1 -e RAG_HARD_FILTERS=1 -e RAG_PREWARM=1 `
  --mount "type=bind,source=$((Get-Location).Path),target=/app" `
  aaalion-rag:latest bash -lc "python -m rag.ingest.run && python -m rag.eval.report"
```

生成文件:

- `docs/eval_report.html`: 总表、按场景拆解和逐 case 明细。
- `docs/eval_report.json`: 可复用的原始结果。

Chroma/PostHog 的 `capture() takes 1 positional argument but 3 were given`
属于遥测兼容噪声，不影响检索结果；设置 `ANONYMIZED_TELEMETRY=False`
可避免该输出。

## 当前数据 (2026-05-25, stateful multi-turn retrieval + Docker prewarm)

商品库包含 145 件商品、8 个类目。本次结果基于队友刚合入 `main` 的
反选/品牌产地修复，以及 `Tujie` 的 golden 审计、外币人民币标准化与
检索阶段硬约束、多轮对话的结构化约束状态，以及 Docker 启动预热。本轮
在此前 5 条 `constraint-filter` 样例之外新增 4 条
`constraint-state` 回归样例。

### 三策略整体对比

| 策略 | recall@5 | recall@10 | MRR | precision@5 | 反选准确率 | 无匹配正确率 | 平均延迟 |
|---|---:|---:|---:|---:|---:|---:|---:|
| Dense | 0.766 | 0.890 | 0.705 | 0.293 | 0.782 | 0.902 | 217 ms |
| Hybrid (Dense + BM25) | 0.751 | 0.879 | 0.704 | 0.297 | 0.745 | 0.897 | 96 ms |
| **Hybrid + Rerank (生产路径)** | **0.982** | **0.994** | **0.856** | **0.390** | **1.000** | **0.942** | **610 ms** |

生产路径为 `server/app/services/rag_client.top_k`。Docker 重建索引后运行
`python -m rag.eval.report`，三种策略均为 `0` errors。

评测在计时 case 之前调用与后端一致的检索预热。未预热的同一版本曾测得
生产路径平均 `6156 ms`，其中一次首次模型载入异常达到 `379341 ms`；
启用预热后平均降至 `610 ms`，中位数为 `68 ms`。宽泛请求仍可能在 CPU
rerank 中耗时数秒，这是正常推理成本，不是用户触发的模型下载。

### 按场景拆解 (Hybrid + Rerank)

| 场景 | n cases | recall@5 | MRR | precision@5 | 反选准确率 | 备注 |
|---|---:|---:|---:|---:|---:|---|
| 基础推荐 `basic` | 13 | **1.000** | 0.917 | 0.300 | - | 稳定 |
| 条件筛选 `filter` | 16 | 0.978 | 0.933 | 0.427 | - | 检索阶段硬约束生效 |
| 新增约束回归 `constraint-filter` | 5 | **1.000** | **1.000** | **0.560** | - | 类别/品牌/人民币预算 |
| 多轮状态 `constraint-state` | 4 | **1.000** | **1.000** | 0.250 | **1.000** | 覆盖/排除/取消旧约束 |
| 反选/排除 `negation` | 12 | 0.936 | 0.803 | 0.455 | **1.000** | 队友反选修复与产地审计生效 |
| 多轮追问 `multiturn` | 9 | **1.000** | 0.889 | 0.267 | **1.000** | 文本上下文 + 结构化状态 |
| 多商品对比 `compare` | 6 | **1.000** | 0.917 | 0.433 | - | 稳定 |
| 无匹配标签 `no-match` | 6 | - | - | - | - | correctness = 0.964 |
| 品牌产地 `brand-origin` | 3 | **1.000** | 0.611 | 0.400 | **1.000** | 约束后的合规替代召回提升 |

共有 10 条 `expected_product_ids=[]` 的案例；其中 6 条显式标记
`no-match`，另外 4 条为 vague/rewrite 或排除后无替代商品的测试。

## 多模态图像检索(bonus track 4.2 「拍照找货」)

新增 `rag/eval/image_eval.py`。在此之前「拍照找货」只有演示截图、零数字；
本轮把它变成可复现的 metric:

### 索引

OpenCLIP ViT-B/32 编码全部 **145 张商品图**(含 Round 6 加入的 45 张真品图,
之前因 `_product_id` 正则只匹配 `_live` 后缀被静默跳过,本轮一并修复)。
存进 `data/.chroma/products_image` collection,与文字索引同库不同 collection。
本机 Apple M-series MPS 上构建 30 秒;A100 约 5 秒。

### 整体数字(leave-one-in, k=5, queries=145)

| 指标 | 数值 | 释义 |
|---|---:|---|
| `self_recall@1` | **1.000** | top-1 命中查询商品自身;路径正确性 sanity |
| `self_recall@5` | **1.000** | top-5 命中自身 |
| `category_precision@5_excl_self` | **0.793** | 排除自身后 top-5 中同类目占比 — 真实「相似品类召回」信号 |
| `brand_recall@5_excl_self` | **0.124** | 同品牌占比;低是预期(多数品牌目录里只有 1-2 件) |
| 延迟 | mean=59ms · p50=32ms · p95=45ms | OpenCLIP 编码 + Chroma cosine on MPS |

### 按类目拆解

| 类目 | n | self_recall@1 | category_precision@5 |
|---|---:|---:|---:|
| 美妆护肤 | 35 | 1.000 | **0.864** |
| 数码电子 | 30 | 1.000 | **0.933** |
| 服饰运动 | 30 | 1.000 | **0.917** |
| 食品饮料 | 25 | 1.000 | **0.830** |
| 母婴健康 | 5 | 1.000 | 0.750 |
| 图书音像 | 5 | 1.000 | 0.500 |
| 家居家具 | 5 | 1.000 | 0.250 |
| 食品生活 | 5 | 1.000 | 0.150 |
| 户外运动 | 5 | 1.000 | 0.050 |

大样本类目(美妆/数码/服饰/食品饮料,n≥25)的 `category_precision@5` 都在
**0.83 - 0.93**,说明 CLIP 在密集类目里能稳定召回视觉相近商品。小样本类目
(n=5)的低分有两个真实原因:**(a)** 5 件商品视觉本身异质(户外类:帐篷 +
登山鞋 + 冲锋衣 + 头灯 + 睡袋,互相不像);**(b)** 排除自身后类目里最多只剩
4 件可被召回,数学上限就低。这是 catalog 设计问题、不是模型问题。

### 方法学说明 / 已知局限

这是 *leave-one-in* 评测 — 查询图就在索引里,所以 `self_recall@1 = 1.000`
本就是结构性预期。它检验的是 CLIP 编码 + Chroma 检索的路径正确性,不能等同
真实场景。真实「拍照找货」用户是用**新拍的、不在索引里的**照片做 query;
那个数字一定低于本表。**`category_precision@5_excl_self`** 是更接近真实
场景的代理指标 — 它问的是:「排除自身这一条作弊答案后,top-K 仍然落在合理
品类里吗?」 答案是 79.3%(大样本类目最高 93%)。

更严格的 held-out user-photo 评测会在 06-10 前再补;现阶段先把
*bonus track 4.2* 从「只有演示截图」升级到「有可复现的 metric + 按类目拆解
+ 已知局限说清楚」。

## 本轮合并的三层变化

### Golden 标签审计 (Tujie)

对照实际目录修正了 19 条错误或不完整标签，包括类型错配、目录已有却
标成 no-match、跨品类的反选正负样本，以及不完整的候选范围。审计前后
的数字不可作为纯算法 A/B；`59 cases / 49 positive` 是后续比较口径。

### 反选与品牌产地审计 (merged from main)

队友在 `rag/retrieve/negation.py` 与 `rag/retrieve/brand_origin.py`
补充了品牌明示反选、跨语言别名和产地修正，并更正相关真实商品来源数据。
在当前 11 条 forbidden 案例上，合并结果的生产路径
`negation_accuracy=1.000`；`brand-origin` 三例的召回为 `1.000`。
该切片仍较小，后续应继续加入经过复核的产地反选与替代商品样例。

### CNY 价格标准化 (Tujie)

- `server/app/services/currency.py` 为外币商品查询最新可用 Frankfurter
  参考汇率，返回 `price_cny` 与 `exchange_rate`，不覆盖原始价格。
- `price_intent.py` 按人民币价格执行“200元以下”“便宜点”等条件；
  汇率不可用的外币不会被错误视为满足人民币预算。
- 在仅加入 CNY 排序的 Tujie 分支上，MRR 从 `0.771` 提升到 `0.778`，
  `filter` MRR 从 `0.469` 提升到 `0.576`。
- 汇率仅用于展示与检索比较，有一小时缓存，不是支付结算报价。

### 检索阶段硬约束 (Tujie)

- `rag/retrieve/constraints.py` 从查询和 API filters 解析类目、子类目、
  指定品牌、排除品牌与人民币价格上下限。
- 同一个 `Filter` 进入 Chroma dense 与 BM25，再进行 hybrid fusion；
  不再依赖召回后才尝试筛掉明显不满足条件的商品。
- `currency` 写入 Chroma 元数据。人民币商品可提前按预算收窄；美元等
  外币商品保留到实时换算后，再做严格人民币判断，避免将 `$20` 当成
  `¥20`，也避免过早漏召回。
- 目录复核发现 `300元以下的面霜` 还应包含折算后约 `¥156` 的
  `p_1_intl_05`，已加入 golden 预期结果；本节数字基于修正后的标签。
- 在同一 64-case 集上切换 `RAG_HARD_FILTERS`：`filter` MRR
  `0.635 → 0.917`，`filter` recall@5 `0.889 → 0.972`；
  整体 recall@5 `0.891 → 0.981`。两组均为 `0` errors。

### 多轮结构化约束状态 (Tujie)

- `server/app/services/constraint_state.py` 逐轮归并类目、子类目、品牌和
  人民币预算；客户端 API `filters` 始终覆盖对话推断结果。
- 用户可继续沿用条件，也可用 `预算加到3500元`、`不要 Sony 了，改成
  Bose`、`品牌不限`、`预算不限` 覆盖或取消旧条件。
- 状态对象直接进入生产检索，且价格偏好只读取当前追问；因此上下文改写
  中保留的旧锚点不会把已经取消的预算或品牌重新加回来。
- `golden.jsonl` 新增 4 条三轮回归；`constraint-state` 切片
  `recall@5=1.000 / MRR=1.000`，`multiturn` 九例
  `recall@5=1.000 / MRR=0.889`。

### Docker 启动预热与就绪门控 (Tujie)

- `Dockerfile.rag` 在镜像构建时下载 `bge-small-zh-v1.5` 和
  `bge-reranker-base`，避免容器第一次用户请求才访问模型仓库。
- `server/app/services/retrieval_readiness.py` 在 FastAPI 启动阶段预热
  embedding、BM25、cross-encoder 以及一次完整 `top_k` 检索路径。
- `/ready` 仅在预热完成后返回 `200`；预热失败或未完成时
  `/chat/stream` 返回 `503`。`server/docker-compose.yml` 用该端点做
  healthcheck。
- Docker 接口实测：容器启动后约 `14.8 s` ready；首条实际宽泛检索
  `推荐一款日常面霜` 的 retrieval 为 `1264 ms`，而不完整预热时同一
  首条请求为 `6519 ms`。

### 多图请求延迟优化 + 图像索引覆盖修复 (Sam, R8.F)

- `server/app/routes/chat.py` 在把用户消息交给视觉 LLM 之前，先把所有
  `data:image/...` base64 内嵌图缩到 **1024px 长边**(`_downscale_image_data_url`,
  PIL LANCZOS + JPEG 85)。Anthropic / Doubao 视觉模型按像素 tile 计费,
  iPhone 12MP 原图(4032×3024)≈ 12 tiles ≈ 13k tokens,缩后 ≈ 1 tile ≈
  1.1k tokens。3 张图请求预计从 **>30s → ~8s**。CLIP 检索仍用原图字节,
  视觉精度不变。
- `rag/ingest/embed_image.py:_product_id` 正则仅匹配 `*_live.jpg`(AI-gen
  seed 命名),导致 Round 6 新增的 45 张真品图(`p_1_intl_05.jpg`、
  `p_5_real_05.jpg` 等)全部被静默跳过,「拍照找货」对所有真品都不可用。
  正则放宽到接受任一 `p_*` stem;重建后 `products_image` collection 从
  100 vectors → **145 vectors**(全覆盖)。

## 指标定义

| 指标 | 含义 | 何时适用 |
|---|---|---|
| `recall@k` | 命中的 expected IDs 占 expected 的比例 | expected 非空 |
| `MRR` | 第一个 expected 出现位置的倒数 | expected 非空 |
| `precision@k` | top-k 中 expected 的占比 | expected 非空 |
| 反选准确率 | top-5 中未落入 `forbidden_product_ids` 的槽位比例 | 带 forbidden IDs |
| 无匹配正确率 | 基于 query/title 字符重叠的启发式诚实度指标 | expected 为空 |
| 延迟 | 单次 retrieve 的 wall-clock 毫秒 | 每个 case |

指标仅依赖返回的 `product_id` 与 golden 标签，不调用生成模型。

## 历史对比

| 时间 | Golden 口径 | recall@5 | MRR | 反选准确率 | 备注 |
|---|---|---:|---:|---:|---|
| Round 5 | 31 / 19 positive | 0.711 | 0.695 | - | 初版 hybrid + rerank |
| Round 6.5 同义词扩展 | 31 / 19 positive | 0.816 | 0.705 | - | 同一小集算法对照 |
| Round 7 扩集 (审计前) | 标签含错配 | 0.746 | 0.674 | - | 仅保留为发现标签问题的记录 |
| Round 7.1 标签审计后 | 59 / 49 positive | 0.830 | 0.771 | 0.780 | 标签对齐后的行为基线 |
| Round 7.2 CNY 标准化 (`Tujie`) | 59 / 49 positive | 0.830 | 0.778 | 0.780 | 人民币预算排序落地 |
| Merged main + Tujie | 59 / 49 positive | 0.880 | 0.828 | 1.000 | 反选审计与 CNY 标准化共同生效 |
| R7.4 约束过滤 | 64 / 54 positive | 0.981 | 0.846 | 1.000 | dense/BM25 同步按条件召回；新增 5 条回归 |
| R7.5 多轮约束状态 | 68 / 58 positive | 0.982 | 0.856 | 1.000 | 新增 4 条继承/覆盖/取消回归 |
| **R7.6 Docker 预热 (当前)** | **68 / 58 positive** | **0.982** | **0.856** | **1.000** | **模型构建期缓存；平均延迟 6156 ms → 610 ms** |

队友原分支记录的 `59 / 44 valid` 评测早于本次 golden 标签审计，因口径
不同不与当前表直接计算增益。

## 性能与限制

- 预热后的 Docker 全量评测生产路径平均延迟为 `0.61 s`，中位数
  `68 ms`；少数需全量 rerank 的宽泛问题仍可达到 `4.3 s`。第一次遇到
  外币商品还可能请求 FX 服务。
- 队友已有 20 并发 SSE 压测记录，成功率为 `100%`；缓存命中路径应与
  冷跑评测分开解释。
- 约束词典目前聚焦目录已有、含义清晰的类别/子类别和品牌别名；扩展规则
  前应同时补充 golden 样例，以防把模糊需求过早硬过滤。
- `no_match_correctness` 是启发式指标，不等同于生成端反幻觉保证。
- Golden 集仍建议由第二位标注者复核，特别是泛需求的可接受候选范围。
