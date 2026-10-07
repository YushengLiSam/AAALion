<!-- 头部使用表格布局,让图标与标题/副标题各占一格、互不重叠。 -->
<table border="0">
  <tr>
    <td width="150" valign="middle" align="center">
      <img width="130" src="client/AAALionApp/AAALionApp/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png" alt="狮选 LionPick app icon"/>
    </td>
    <td valign="middle">
      <h1>狮选 LionPick</h1>
      <b>基于 RAG 的多模态电商智能导购(iOS 原生 + FastAPI + Milvus)</b>
      <br/><br/>
      团队 <b>AAALion</b> · ByteDance 2026 AI 全栈挑战赛
    </td>
  </tr>
</table>

[![CI](https://github.com/YushengLiSam/AAALion/actions/workflows/ci.yml/badge.svg)](https://github.com/YushengLiSam/AAALion/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11-blue?logo=python)
![iOS](https://img.shields.io/badge/iOS-17%2B-black?logo=apple)
![Milvus](https://img.shields.io/badge/Vector%20DB-Milvus%20Standalone-00A1EA)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

> **本 README 里的每一个数字都能在仓库里找到出处**(评测报告、压测 JSON、运行手册)。没有跑过的东西写"未验证",不写预估的业务指标。

---

## 📖 目录

1. [这是什么](#-这是什么)
2. [系统架构(看图就懂)](#-系统架构看图就懂)
3. [一次请求是怎么走的](#-一次请求是怎么走的)
4. [拍照找货:图片和文字怎么配合](#-拍照找货图片和文字怎么配合)
5. [效果(全部是实测)](#-效果全部是实测)
6. [工程与运维](#-工程与运维)
7. [快速上手](#-快速上手)
8. [API 一览](#-api-一览)
9. [项目结构](#-项目结构)
10. [设计取舍(常被问到的问题)](#-设计取舍常被问到的问题)
11. [已知局限与下一步](#-已知局限与下一步)
12. [团队与致谢](#-团队与致谢)

---

## 🤔 这是什么

### 一句话

> 用户在 iPhone 上用**文字、语音或照片**说出需求,狮选只从**真实商品库**里找货,按用户的**硬条件**(预算、品牌、"不要日系"……)严格筛选,再让大模型写推荐理由——商品卡片先到,文字随后逐字流出。

### 它解决什么问题

| 痛点 | 常见做法 | 狮选的做法 |
|---|---|---|
| 大模型编造商品、价格 | 直接让大模型回答 | 只把检索到的商品交给大模型;**商品卡由服务端按检索结果生成**,不经过大模型;每句事实带来源标签 |
| "两千以内""不要日系"被无视 | 把条件塞进提示词,靠模型自觉 | 规则解析成**硬约束**,检索前预过滤、检索后按人民币(实时汇率)严格复核 |
| 拍照找货只看"长得像" | CLIP 取前几名就返回 | 图片召回 + 文字条件过滤 + 文字排序;离线给商品图写外观描述,存进文本索引 |
| 用户说不清要什么 | 随便推几个 | 首轮太模糊时**先反问**,给可点选的快捷选项 |
| 多轮对话里条件丢失 | 每轮从头理解 | 约束状态在多轮之间**继承 / 替换 / 取消**("再便宜点""品牌不限") |

### 能力一览

多轮对话 · 否定/排除 · 商品对比表 · "比 X 便宜的 Y"(多跳) · 拍照找货 + 文字条件 · 主动反问 · 对话式购物车(加购 / 改数量 / 删除 / 结算) · 降价提醒 · 复购提醒 · 拼单 · 👍/👎 偏好 · 语音输入 + 朗读 · 海外商品人民币换算

---

## 🏗 系统架构(看图就懂)

```
 iPhone(SwiftUI)  文字 / 语音 / 照片
        │  HTTPS(Cloudflare 隧道)
        ▼
 FastAPI(只监听 127.0.0.1)── 限流 · 越权校验 · /ready 就绪门控
        │
        ├─ 意图分流(规则):购物车指令 / 太模糊先反问 / 越界拒答 / 场景词 / 多跳
        │
        ├─ ① 快路(默认,确定性流水线)
        │     约束解析 → 同义词扩展 → 混合召回(向量 + BM25,RRF 融合)
        │     → 否定过滤 → 交叉编码器重排 → 相关性门槛 → 人民币硬约束复核 → 价格意图 → 👍/👎 微调
        │
        ├─ ② 拍照路径:视觉路(CLIP)+ 文字路两路召回,按名次合并;两路都严格执行文字里的硬条件
        │
        └─ ③ 智能体路径(LangGraph,默认关闭;实测不如快路,见下文)
        │
        ▼
 Milvus Standalone(版本化集合 + 别名)  ·  BM25(jieba)  ·  SQLite(用户 / 偏好 / 提醒)
        │
        ▼
 大模型(TokenRouter → claude-haiku-4-5,支持视觉)── 只能引用本轮检索到的商品
        │
        ▼
 SSE 流式返回:先发商品卡 → 再逐字发文字 → 来源统计 → 完成
```

**为什么默认走确定性流水线,而不是智能体?** 我们用 LangGraph 实现了智能体路径,在线上用真模型对 34 个复杂问题做了对比:**快路 31/34,智能体 21/34(要求两次都对),p50 延迟是快路的 4 倍多**。所以默认走快路,智能体留在开关后面。详见 [`docs/AGENT.md`](docs/AGENT.md) §12.1。

---

## 🔍 一次请求是怎么走的

以"**推荐防晒霜,不要日系,两百以内**"为例:

| 步骤 | 做了什么 | 💡 通俗解释 |
|---|---|---|
| 1. 意图分流 | 不是购物车指令、不模糊、不越界 → 走检索 | 先判断"要不要找商品",全部用规则,快且确定 |
| 2. 约束解析 | 品类=防晒;排除产地=日本;预算≤¥200 | 把"条件"从话里拆出来,后面严格执行 |
| 3. 混合召回 | 向量检索(按意思)+ BM25(按字面),RRF 按名次合并 | 两路互补:一路懂"清爽不油腻",一路认准型号和品牌名 |
| 4. 否定过滤 | 按品牌→产地对照表(155 条品牌名,含中英文别名)去掉日本品牌 | "不要日系"由代码保证,不靠模型自觉 |
| 5. 重排 + 门槛 | 交叉编码器打分;分数太低且字面无重叠 → 判定"目录里没有" | 宁可说没有,也不硬推不相关的 |
| 6. 硬约束复核 | 海外商品按实时汇率换成人民币,再查一次 ≤¥200 | 美元标价的商品不会因为"数字小"混进来 |
| 7. 生成 | 先推商品卡,再流式输出推荐理由,每句带 `[目录✓]` / `[推断?]` | 卡片由服务端生成,文字只能引用这些商品 |

---

## 📸 拍照找货:图片和文字怎么配合

```
 离线:145 张商品图 ─ CLIP ─→ 图片向量(Milvus)
                   └ 本地 Qwen3-VL-2B 写外观描述 ─→ 文本索引(和商品描述、SKU 规格一起)

 在线:照片 + "有没有便宜点的,不要日系"
   视觉路:CLIP 对全部商品排序 → 低于视觉下限的不出卡 → 文字里的硬条件严格过滤
   文字路:去掉套话后的文字(只发照片时用照片商品的离线外观描述),在同品类里检索一次,同样过硬条件
   两路按名次合并(RRF);照片和某件商品非常像,或在问"这是什么",就把它放在第 1 位

   特殊问法:"有黑色的吗 / 有 XL 码吗"     → 查商品的 SKU 规格数据,有就是有,没列就说没列
            "同品牌的 / 同价位的 / 配个什么" → 以照片里的商品为锚点,走多跳检索
```

- 检索阶段**不调用大模型**;生成回答时大模型能同时看到照片和文字。
- 离线图片描述只写看得见的外观(形状、颜色、材质、风格);图中文字容易被模型编造,**不进索引**。
- 两路模式出任何异常都会退回只走视觉路(`IMAGE_TWO_PATH=0` 时的行为)。
- 详细设计、开关和评测见 [`docs/IMAGE_SEARCH.md`](docs/IMAGE_SEARCH.md)。

---

## 📊 效果(全部是实测)

**文字检索**(CI 门禁同一设置:线上重排参数、不调用大模型;来源 [`docs/bench/eval_baseline.json`](docs/bench/eval_baseline.json))

| 评测集 | 用例数 | recall@5 | MRR | 否定准确率 |
|---|---:|---:|---:|---:|
| 标准集 golden | 92 | 0.929 | 0.863 | 1.000 |
| 组合难题 compositional(多轮 / 对比 / 组合条件) | 61 | 0.830 | 0.886 | 1.000 |

> 用例是团队自己写的,规模小;它的主要用途是**回归门禁**:每次改代码逐条比对,任何一条从命中变成没命中都会让 CI 变红。方法论见 [`docs/EVAL_RESULTS.md`](docs/EVAL_RESULTS.md)。

**拍照找货**(本机离线评测,不调用大模型:145 张商品图每张生成 3 个随机变体——裁剪、旋转、调色、缩小、JPEG 压缩——当作"用户照片";来源 [`docs/bench/image_two_path_eval_local.json`](docs/bench/image_two_path_eval_local.json)、[`docs/bench/image_text_eval_local.json`](docs/bench/image_text_eval_local.json))

| 场景 | 结果 |
|---|---|
| 只发照片(435 张):这件商品排第 1 / 在前 3 | 0.956 / 0.991 |
| 照片 + "有黑色的吗 / 有 XL 码吗"(142 问):有没有这个规格答对 | 0.901 |
| 照片 + "有没有同品牌的 / 同价位的 / 配个什么":出的卡全部满足关系 | 0.913 / 0.938 / 0.932 |
| 照片 + "不要这个牌子" / "不要这个产地的" / "预算是原价的八成":出现违反条件的卡片 | 0 / 0 / 0 |
| 照片 + "有没有便宜点的":出现不比拍的商品便宜的卡片 | 2.1%(3/145,都是 CLIP 把照片认成了别的商品) |
| 明显无关的合成图片(噪声、纯色、渐变、几何图案):被拒绝出卡 | 50/50(注意:没有用真实的"目录外"照片校准) |

> 两路比只走视觉路多一次文字检索:本机融合环节 p50 从 0.2 ms 变成约 60 ms;**线上 VM 的延迟还在补测**。

**快路 vs 智能体**(VM 上真模型,34 个复杂问题;来源 [`docs/AGENT.md`](docs/AGENT.md))

| | 全对 | p50 / p95 延迟 | 每次请求调模型 |
|---|---:|---:|---:|
| 快路 | **31/34** | 1.2 / 7.6 s | 0 次(检索阶段) |
| 智能体(要求 2 次都对) | 21/34 | 5.3 / 8.0 s | 2.18 次 |

**向量库**(来源 [`docs/RUNBOOK_MILVUS.md`](docs/RUNBOOK_MILVUS.md)、[`docs/VECTOR_STORE.md`](docs/VECTOR_STORE.md))

- 线上 Milvus Standalone vs 原 Chroma,在 VM 上回放 1410 次查询:**错误 0**;两边结果不一致的查询里,Milvus 对精确解的召回都不低于 Chroma;p50 / p95 **11.8 / 30.5 ms**(Chroma 21.5 / 76.5 ms)。来源 [`docs/bench/replay_gate_vm-20261007-captions.json`](docs/bench/replay_gate_vm-20261007-captions.json)。
- 规模压测(M4 笔记本,KuaiSearch 真实商品标题 100 万条):Chroma 21.7 s / recall .760,Milvus 1.0 s / .985。

---

## 🛠 工程与运维

| 环节 | 做法 | 文档 |
|---|---|---|
| CI | 每次 PR / 推送 main:pytest(Python 3.10 + 3.11)+ 检索评测门禁(逐条比对基线) | [`.github/workflows/ci.yml`](.github/workflows/ci.yml) |
| 部署 | VM 每 2 分钟检查 main;**只部署 CI 全绿的提交**;同步 systemd 配置 → 重启 → `/ready` 检查,失败自动回滚 | [`docs/RUNBOOK_OPS.md`](docs/RUNBOOK_OPS.md) |
| 向量库 | Milvus Standalone(Docker,端口只开本机,账号鉴权,最小权限);版本化集合 + 别名,重建索引不停服,一条命令回滚;Chroma 兜底保留到 2026-10-21 | [`docs/RUNBOOK_MILVUS.md`](docs/RUNBOOK_MILVUS.md) |
| 就绪门控 | `/ready` 直接查一次向量库,查不了就返回 503,部署会回滚 | [`docs/RUNBOOK_MILVUS.md`](docs/RUNBOOK_MILVUS.md) §6 |
| 安全 | 生产关闭验证码回显;账号类接口越权校验(灰度:先记录后拦截);登录 / 短信 / 聊天限流;默认密钥保护 | [`docs/SECURITY.md`](docs/SECURITY.md) |
| 数据 | 用户 SQLite 每日备份(保留 14 天)+ 恢复演练;向量是派生数据,从 npz 文件 10 秒重建 | [`docs/RUNBOOK_OPS.md`](docs/RUNBOOK_OPS.md) |
| 配置 | systemd 配置放在 `deploy/systemd/`,改配置 = 一次提交;密钥不进仓库 | [`deploy/README.md`](deploy/README.md) |

实测记录:每次重启到 `/ready` 恢复 21–40 秒;停掉 Milvus 约 18 秒后 `/ready` 变 503、用户请求由兜底接住,重新启动后约 27 秒自动恢复。

---

## 🚀 快速上手

本地开发默认用进程内 Chroma,不需要 Milvus。

```bash
# 1. 安装辅助命令(任意目录可用)
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"

# 2. 配置大模型密钥(只写进被 gitignore 的 server/.env)
cp .env.example server/.env
$EDITOR server/.env            # TOKENROUTER_API_KEY=...;不想调付费接口就设 LLM_PROVIDER=echo

# 3. 后端
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
aaalion ingest                 # 从 data/seed 建文本索引
python -m rag.ingest.run_image # 建图片索引(拍照找货需要)
aaalion backend                # 看到 /ready 返回 ready 即可

# 4. iOS 模拟器
aaalion ios-sim

# 5. 检索评测(不调用大模型)
python -m rag.eval.run
```

<details>
<summary>Windows / Docker 部署(无需 API key 的本地冒烟测试)</summary>

在仓库根目录运行下面这段 PowerShell。检索、过滤、汇率换算与商品卡片都是真实功能,只有回答生成使用确定性的 `echo` provider。

```powershell
if (-not (Test-Path server/.env)) { Copy-Item .env.example server/.env }
(Get-Content server/.env -Raw) -replace '(?m)^LLM_PROVIDER=.*$', 'LLM_PROVIDER=echo' |
  Set-Content server/.env -Encoding UTF8

docker compose -f server/docker-compose.yml down
docker compose -f server/docker-compose.yml build backend
docker compose -f server/docker-compose.yml run --rm --no-deps backend python -m rag.ingest.run
docker compose -f server/docker-compose.yml up -d

do {
  Start-Sleep -Seconds 1
  try { $ready = Invoke-RestMethod http://127.0.0.1:8000/ready } catch { $ready = $null }
} until ($ready.status -eq "ready")
$ready
```

打开 `http://127.0.0.1:8000/docs` 即可测试 API。要切换成 TokenRouter 生成真实回答:把 `server/.env` 里的 `LLM_PROVIDER` 改成 `tokenrouter` 并填入 `TOKENROUTER_API_KEY`,然后 `docker compose -f server/docker-compose.yml up -d --force-recreate backend`。

</details>

**App 连哪个后端**:`Config.swift` 默认指向线上隧道地址,装好就能用;开发时在 App 的 ⚙ 设置里改成 `http://localhost:8000`(模拟器)或 `http://<Mac 局域网 IP>:8000`(真机)。iPhone 实机部署见 [`docs/DEPLOY_GUIDE.md`](docs/DEPLOY_GUIDE.md)。

---

## 🔌 API 一览

完整说明见 [`docs/API.md`](docs/API.md),运行后可在 `/docs` 查看 Swagger。

| 接口 | 说明 |
|---|---|
| `POST /chat/stream` | 对话主入口(SSE)。事件:`product_card`(先发)、`delta`(文字片段)、`cart_intent`、`clarify`、`hop_trace`、`claim_summary`、`error`、`done` |
| `GET /ready` · `GET /health` | 就绪(含向量库门控、生效的大模型)· 存活 |
| `GET /products/{id}` · `GET /currency/rate` | 商品详情 · 参考汇率 |
| `/auth/*` | 注册、密码登录、Apple 登录、手机号验证码(生产关闭回显)、注销 |
| `/preferences` · `/price_watch` · `/repurchase` · `/groupbuy` | 👍/👎 偏好 · 降价提醒 · 复购提醒 · 拼单 |
| `GET /cache/stats` | 两层缓存的命中率 |

---

## 📁 项目结构

```
client/            iOS 客户端(SwiftUI、语音、相机、SSE 解析)
server/app/
  routes/          HTTP 接口(chat.py 是对话主流程)
  services/        检索编排 rag_client、大模型 provider、汇率、缓存、限流、SQLite
  agent/           LangGraph 智能体路径(默认关闭)
rag/
  ingest/          切块、文本 / 图片向量、离线入库
  retrieve/        约束解析、混合检索、否定、重排、多跳、拍照融合、SKU 属性
  store/           向量库抽象(Chroma / Milvus)、迁移、别名
  eval/            评测集与评测脚本(含 CI 门禁 gate.py)
data/seed/         145 件商品(JSON + 图片);data/derived/ 离线图片描述
deploy/            Milvus compose、systemd 配置
tools/             自动部署、备份、图片描述生成、辅助命令
docs/              设计、评测、运行手册(入口 docs/README.md)
```

---

## ❓ 设计取舍(常被问到的问题)

**Q1:为什么意图识别用规则,不用大模型?**
购物车指令要在回复之前就让界面动起来,规则快且确定;交给大模型可能出现"下单面板弹出来了,文字却说没货"。代价是词表要人工维护。

**Q2:为什么两路召回用 RRF 合并,不按分数加权?**
向量相似度和 BM25 分数不是一个尺度,直接相加基本只看 BM25。RRF 只看名次,不用调权重。

**Q3:145 件商品为什么要上 Milvus?**
不是为了快(这个规模 Chroma 也够)。价值在两点:一是工程闭环(独立服务、鉴权、版本化集合 + 别名回滚、就绪门控),二是规模(100 万条压测里 Chroma 21.7 s、recall .760,Milvus 1.0 s、.985)。

**Q4:怎么防止大模型编造?**
商品卡由服务端按检索结果生成;大模型只能看到本轮检索到的商品;每句事实带 `[目录✓]` / `[推断?]` 标签。老实说,标签目前是模型自己打的,还没有逐句核验(见下一节)。

**Q5:离线图片描述为什么不收录"图中文字"?**
抽检原图发现 2B 视觉模型会编造图里没有的字(帐篷图写出"Tent"和一串"1000");品牌和型号本来就在商品数据里,这一项收益小、风险大。

---

## 🧭 已知局限与下一步

- 评测集是团队自己写的,规模小;外部基准(Multi-CPR 电商检索)还没接入评测流程。
- `[目录✓]` 来源标签是模型自报,尚未做逐句核验。
- 越权校验还在"只记录"阶段,切成拦截需要新版 iOS 装到用户手机上。
- 公网入口是 Cloudflare 临时隧道,重启会换地址;外部探活和告警、整机重启演练都在换固定域名之后做。
- 单台 VM、单进程;限流和缓存都在进程内。
- 拍照路径:视觉下限只用合成图片校准过;上一轮发过的照片不会被记住。
- "预算凑一整套"这类请求还没有专门的规划 + 并行检索。

完整计划与进度见 [`PLAN.md`](PLAN.md)。

---

## 👥 团队与致谢

| 成员 | 主要负责 |
|---|---|
| 陈澍枫 Shufeng Chen | 项目负责人;iOS 客户端(`client/`) |
| 李雨晟 Yusheng Li | 后端(`server/`)、向量库与云端部署运维、CI 评测门禁 |
| 管图杰 Tujie Guan | 检索 / RAG(`rag/`)、评测集、汇率换算与多轮约束 |

开发过程与各轮交付记录见 [`docs/DEV_LOG.md`](docs/DEV_LOG.md),文档索引见 [`docs/README.md`](docs/README.md)。

用到的开源项目与模型:[FastAPI](https://github.com/fastapi/fastapi) · [Milvus](https://github.com/milvus-io/milvus) · [Chroma](https://github.com/chroma-core/chroma) · [BAAI bge-small-zh / bge-reranker](https://github.com/FlagOpen/FlagEmbedding) · [OpenCLIP](https://github.com/mlfoundations/open_clip) · [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) · [LangGraph](https://github.com/langchain-ai/langgraph) · [jieba](https://github.com/fxsjy/jieba) · [rank_bm25](https://github.com/dorianbrown/rank_bm25) · [Frankfurter 汇率](https://www.frankfurter.app/)

## 📄 许可证

MIT —— 见 [LICENSE](LICENSE)。
