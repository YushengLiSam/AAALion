# 向量存储层:Chroma / Milvus 可插拔(R15)

> 作者:李雨晟 · 2026-09-26 · 代码:`rag/store/` · 测试:`server/tests/test_vector_store.py`
> · 一致性比对:`rag/eval/store_parity.py`

## 一句话

把向量库抽象成一层接口,Chroma(默认)和 Milvus 按配置切换。两个后端用**同一批向量**
逐条比对,并以 numpy 暴力算出的**精确解**为准:生产路径评测指标逐字相同,
Milvus 的召回在每条 query 上都不低于 Chroma。借 Milvus 必须显式声明 schema 的机会,
把"不要日系"这类国别反选从检索后删改成检索前过滤。

**145 件商品用不上 Milvus 的性能。** 这一轮做的是可插拔的架构、迁移无损的证据,
以及规模化之前必须先踩平的坑(见 §4)。性能数据要等百万级压测(§7)。

---

## 1. 结构

```
rag/store/
  base.py          Doc / Hit / VectorStore 协议 / SCHEMA_VERSION / 可过滤字段清单
  chroma_store.py  Chroma 后端(行为与 R14 之前的 rag/store.py 一致)
  milvus_store.py  Milvus 后端(显式 schema、HNSW + 标量倒排、带过滤搜索分档策略)
  filters.py       过滤条件中立写法 → Milvus 布尔表达式(不支持的写法直接报错)
  migrate.py       跨后端复制索引,不重算向量
  load.py          向量文件(.npz)→ 当前后端;刻意不 import torch
  __init__.py      get_store() 工厂 + 保留旧的模块级 API
```

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `RAG_STORE` | `chroma` | `chroma` / `milvus`;其他取值直接报错,不会悄悄退回某个后端 |
| `RAG_MILVUS_URI` | `data/.milvus/lionpick.db` | 文件路径 = 嵌入式 Lite;`http://host:19530` = 服务模式 |
| `RAG_MILVUS_INDEX_TYPE` | `HNSW` | 也可 `FLAT`(纯 numpy) |
| `RAG_FILTER_PUSHDOWN` | `1` | 国别反选下推 + Python 侧同一规则 |

**不要用 `MILVUS_URI` 这个名字。** pymilvus 在 import 时自己会读它,而且要求必须是 http 地址;
设成文件路径会让 `import pymilvus` 直接失败。所以本项目统一用 `RAG_MILVUS_*` 前缀。

`pymilvus` 是**可选依赖**(`server/requirements-milvus.txt`)。云端 VM 的自动部署只做
`git reset --hard` + 重启,不装新包;默认的 Chroma 路径在没有 pymilvus 的机器上照常工作,
这一点单独验证过。

## 2. Schema v2 与过滤下推

v2 起每条文档都带 `brand_country` 和 `currency`。

- **`brand_country` 必须用反选逻辑同一个函数算**:`brand_origin.product_origin()`。
  不能直接用商品数据里的 `provenance.origin_country`,因为演示数据这一项一律默认填 "CN",
  欧莱雅也是 CN。用错字段的话反选会静默出错。实测结果:欧莱雅→FR,资生堂→JP,
  苹果→US,没有一个品牌的国别是未知的。
- 入库时把版本号写进索引(Chroma 写集合 metadata;Milvus 的 schema 是显式的,直接 describe 就知道)。
  查询侧用 `filterable_fields()` 判断能不能下推:**旧索引会自动退回只在 Python 侧过滤**。
- **索引里的 `brand_country` 是入库那一刻的快照。** 以后修正了产地表,自动部署会让 Python 侧
  立刻用上新值,数据库里却还是旧值。这时再下推,就会误删 Python 侧本该保留的商品。
  所以入库时再写一个**产地解析指纹**(`brand_origin.origin_fingerprint`,对每件商品的解析结果
  取 sha1),查询侧**只有指纹一致才下推**;不一致就只走 Python 侧,等下一次 `--rebuild`。
  指纹只在建集合时写入,因为 Chroma 修改已有集合的 metadata 会丢掉 `hnsw:space`(实测)。
- 下推分两层,判断规则完全相同:
  1. 数据库层:`{"brand_country": {"$nin": ["JP"]}}`,只在索引确实有这个字段时加;
  2. Python 层:`product_matches_filter` 里同样的判断,不依赖索引,稠密和 BM25 两路都会用到。

在截取 top-k **之前**就把要排除的国别去掉,候选名额不再浪费。实测 13 条带国别反选的
query:不下推时,稠密检索的 60 个候选里平均有 **8.8 个**是注定会被删掉的
(最多 16/60);下推后是 0。评测指标不变,因为 145 件的目录名额本来就够用。
这项收益要到数据量大的时候才会体现。

## 3. Milvus 的三种运行方式

| 方式 | URI | 适用 | 注意 |
|---|---|---|---|
| 嵌入式 Lite | 文件路径 | 不加载 torch 的离线工具(迁移、灌数据) | 一个库文件只能被一个进程打开;macOS 上不能和 torch 同进程(§4.5) |
| **Lite 服务进程** | `http://127.0.0.1:19530` | **macOS 本地开发推荐** | `tools/milvus-lite-server.sh`;多个进程可以同时连 |
| Standalone / 集群 | `http://host:19530` | 生产 | etcd + MinIO + milvus;推荐 16G 内存 |

同一份适配器代码,换的只是 URI。

## 4. 这一轮抓到的问题(按发现顺序)

### 4.1 本地索引和线上索引悄悄不一致了
- **现象**:本地 Chroma 索引 1082 条文档里,**0 条**带 `currency`;线上 VM 1082/1082 都带。
- **根因**:`currency` 是 5 月 25 日加进入库代码的。本地索引那天早些时候建的,之后一直没重建;
  VM 的索引是 5 月 29 日建的。索引没有版本号,代码变了也没有任何东西会报警。
  所以本地的价格过滤条件在数据库层等于没生效,只靠 Python 侧复核兜底。
  CI 每次都从种子数据重建索引,**报告里的指标不受影响**。
- **处理**:入库时打 schema 版本号;`/ready` 把后端、版本、可过滤字段、条数都报出来;
  过滤下推按索引能力决定开不开。

### 4.2 HNSW 加上窄的过滤条件会少召回
- **现象**:一致性比对里,带类目过滤的 query,Milvus 返回的条数比实际应有的少。
  "笔记本电脑"这个过滤,1082 条里合格的有 58 条(5.4%),`ef=128` 时只返回 24 条,
  **recall 0.414**。
- **根因**:过滤是在图遍历过程中跳过不合格节点。过滤越窄,在 `ef` 这个遍历预算内
  碰到的合格节点就越少。Milvus 服务端(Knowhere)遇到高选择性过滤会自动改走暴力搜索;
  **Milvus Lite 3.x 是 Python 重写版,没有这层逻辑。**
- **处理**:在适配层补上分档策略。先用标量倒排索引数出合格条数 m:m 为 0 直接返回空;
  m ≤ 4096 时把这 m 条向量取出来精确计算;否则走 HNSW,`ef` 按选择性的倒数放大(有上限)。
  处理后带过滤的 query 在 144 条上 **recall 全部是 1.000**。

### 4.3 Chroma 自己也会漏
- 不带过滤时,有几条 query 是 **Chroma 漏掉了精确 top-10**(Chroma 默认的搜索宽度很小)。
  所以一致性的判定标准定为"两个后端都和精确解比,Milvus 在任何一条上都不能比 Chroma 差",
  而不是"两个后端逐条相同"。基础 `ef` 设为 256 之后,Milvus 在全部 297 条上都等于精确解,
  其中 **47 条比 Chroma 更准**。Chroma 的参数没动,线上行为不变。

### 4.4 一个库文件被两个进程同时打开,静默降级 202 次
- **现象**:打开下推后跑 Milvus 评测,dense 的 recall 从 0.762 掉到 0.617。
- **根因**:评测和一致性比对**同时打开了同一个嵌入式 Lite 库文件**。后打开的进程每次查询都失败,
  检索代码里原本就有"出错时退回关键词检索"的兜底,于是悄悄降级了 202 次。
- **处理**:降级路径现在一定会往 stderr 打日志(这次就是靠这行日志抓到的,
  否则看起来只是指标略差);打开失败时报错会说明 Lite 是单进程的,并指向服务模式。

### 4.5 macOS 上 faiss 和 torch 不能在同一个进程里
- **现象**:全量单元测试跑到 Milvus 那部分时,整个 Python 进程 abort。
- **根因**:`OMP: Error #15`。torch 和 faiss-cpu(milvus-lite 的依赖)在 macOS 上**各自打包了一份
  libomp**。同一个进程里两份都被初始化,不管谁先谁后,第二份一初始化进程就 abort。
  不只是写入(建索引),**搜索也会**。评测那几次没崩,是因为在这台 Mac 上
  embedding 和 rerank 都跑在 mps(GPU)上,torch 没用过 CPU 的 OpenMP。
  这是一个潜伏的风险,并不是"没问题"。
- **试过但放弃的方案**:报错信息里建议的 `KMP_DUPLICATE_LIB_OK=TRUE`,实测进程会在建索引时
  **静默崩溃**,不可用。
- **处理**:
  1. 向量库放进**独立的进程**:`milvus-lite server`,或者生产上的 Standalone。
     faiss 只在那个进程里跑。实测:客户端进程先在 CPU 上初始化 torch 的 OpenMP,
     再通过 gRPC 建 HNSW、做搜索,没有崩,recall 对精确解是 1.000,
     客户端进程里也没有加载 faiss;
  2. 嵌入式模式加了写入防护:在危险组合下**抛出可捕获、讲清原因的异常**,
     而不是让进程 abort;
  3. 入库时"算向量"和"写库"拆开:向量先存成 `.npz`,再由不加载 torch 的子进程
     `python -m rag.store.load` 写入。这也是百万级灌库本来就需要的:向量只算一次,
     换后端不用重算。

### 4.6 写完立刻 release/load 会读到半截文件
- 原计划写完之后 release → load,强制嵌入式 Lite 当场把索引建完。实测 milvus-lite 3.2.1
  会读到后台线程还没写完的段文件(报 `Not an Arrow file` / `File is too small`),
  FLAT 和 HNSW 都会出现。现在封口只做 flush;服务模式下索引本来就由服务进程自己建。

### 4.7 对抗式代码审查补上的(提交前)
4 个维度各一个审查员,每条发现再交给一个独立的反驳者去证伪。确认成立 8 条,驳回 9 条。
修掉的几条:
- **Milvus 服务重启后,查询会一直降级**:milvus-lite 服务进程重启后,带索引的集合都以
  "未加载"状态打开,而 API 进程里的"已加载"缓存不会失效,之后每次查询都失败并悄悄退回
  关键词检索,直到 API 进程重启。现在服务端回"未加载"时,会清缓存、重新加载一次再重试。
- **`--rebuild` 先删索引再算向量**:算向量那一步一旦失败,索引就空了。现在改成向量算好、
  存好之后,写入前才删。
- **产地表修正后 `brand_country` 过期**:加了上面 §2 说的指纹门控。
- `/ready` 是公开接口,不再返回服务器的绝对路径;`.dockerignore` 补上几个 GB 级的新数据目录;
  `.gitignore` 补上 `.env.bak*`。这一条不是本轮引入的,但仓库里确实躺着一个含真实 key、
  却没被忽略的 `server/.env.bak.*`。
- 一致性比对报告的表头原来写"下推=关",实际是开着的,已改成与生产开关一致。数字本身没问题:
  开和关两种情况都显式跑过。

## 5. 验证结果

**一致性**(`python -m rag.eval.store_parity`,153 条 query × {不过滤, 生产过滤} + 145 张图,
以 numpy 精确解为准):

| 切片 | 查询数 | Chroma recall@10 / @60 | Milvus recall@10 / @60 |
|---|---:|---|---|
| 文本 · 不过滤 | 153 | 0.999 / 0.991 | **1.000 / 1.000** |
| 文本 · 生产过滤 | 144 | 1.000 / 1.000 | **1.000 / 1.000** |
| 图片 | 145 | 0.999 | **1.000** |

共同命中的分数最大相差 1.0e-6(浮点舍入);Milvus 更好 47 条,更差 0 条。Chroma 的近似索引每次重建会略有不同,
这个数在 47–48 之间浮动,"更差 0 条"每次都成立。

**端到端评测**(生产路径 hybrid + rerank,`python -m rag.eval.run`):

| 集子 | recall@5 | recall@10 | MRR | 反选 | 无匹配 |
|---|---|---|---|---|---|
| golden 92 例 | 0.947 | 0.971 | 0.860 | 1.000 | 0.952 |
| compositional 61 例 | 0.841 | 0.884 | 0.878 | 1.000 | 1.000 |

以下组合全部**逐字相同**:Chroma、嵌入式 Milvus、Milvus 服务模式;下推开 / 关;
新代码跑在旧索引上(模拟线上 VM 自动部署之后的状态)。多跳评测 34/34 不变。
单元测试 174 个通过(原有 144 个 + 新增 30 个)。

## 6. 操作手册

```bash
# 重建 Chroma 索引(打上 schema v2)
python -m rag.ingest.run --rebuild && python -m rag.ingest.run_image --rebuild

# 启动 Milvus Lite 服务进程(macOS 推荐)
pip install -r server/requirements-milvus.txt
tools/milvus-lite-server.sh &
export RAG_MILVUS_URI=http://127.0.0.1:19530

# Chroma → Milvus,不重算向量
python -m rag.store.migrate --from chroma --to milvus --rebuild

# 一致性比对 + 评测
python -m rag.eval.store_parity
RAG_STORE=milvus python -m rag.eval.run

# 用 Milvus 跑后端
RAG_STORE=milvus RAG_MILVUS_URI=http://127.0.0.1:19530 aaalion backend
```

线上 VM 目前**没有改动**:还是 Chroma 加上那份旧索引。新代码推上去后会自动识别出它是旧索引
(没有 `brand_country`,也没有产地指纹),数据库层下推会跳过,只走 Python 侧。想让线上也用上
数据库下推,需要在 VM 上执行一次 `python -m rag.ingest.run --rebuild`。

## 7. 下一步

- **规模压测**:KuaiSearch(快手,2026,Lite 版 660 万中文商品,带品牌和三级类目)截 10 万和 100 万条,
  比较 Chroma 和 Milvus 的写入耗时、带过滤查询的 p50/p99、内存;再对比 Milvus 库内 BM25
  和现在的进程内 BM25。数据已经下载到 `data/external/`(已 gitignore)。
- **外部评测**:Multi-CPR 电商(阿里,100 万条淘宝标题,1000 条人工标注 query),
  和论文基线并排比较(百万级语料上 BM25 的 MRR@10 是 0.225,最好的领域内 DPR 是 0.289)。
  这套标注每条 query 只有 1 个正例,所以分数会远低于我们自己那套的 0.947,两者不是一个量纲。
- **生产部署**:VM(15G 内存、4 核)上用 docker-compose 起 Milvus Standalone,
  `/ready` 加上 Milvus 健康检查。回滚就是把 `RAG_STORE` 切回 chroma。
