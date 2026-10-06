# RUNBOOK:Milvus Standalone 上生产(PLAN P1)

> 适用:生产 VM(Ubuntu 22.04,4 vCPU / 15 GB,无 swap,Python 3.10,单 worker uvicorn,`lionpick.service`)。
> 代码与部署资产都在仓库里;**本文档里的 VM 步骤一条都还没在 VM 上跑过**(写于 2026-10-06,本地只验证了下文
> "已验证"一节列出的内容)。每一步做完把输出存档,"能指向存档输出"才算完成。
>
> 原则:离线一致性 → 回放门槛 → 影子 → 一次只改一个变量的切换 → 三层回滚 → 故障 / reboot 演练。
> 线上 `data/.chroma`(旧索引,schema_version null)在整个迁移期间**一个字节都不动**,切换后至少保留 14 天。

相关文件:[`deploy/README.md`](../deploy/README.md) · [`docs/VECTOR_STORE.md`](VECTOR_STORE.md) §9 · `rag/store/composite.py` ·
`rag/store/alias.py` · `rag/eval/replay_gate.py` · `tools/milvus_bootstrap.py`。

---

## 0. 开关一览(全部默认关闭 = 行为与 P1 之前相同)

| 变量 | 默认 | 作用 |
|---|---|---|
| `RAG_READY_GATE` | `off` | `report`:`/ready` 展示向量库门控结果;`enforce`:门控不过返回 503 |
| `RAG_READY_GATE_TTL_S` / `_TIMEOUT_S` | 30 / 10 | 门控缓存秒数 / 单次超时 |
| `RAG_READY_GATE_REQUIRE_IMAGE`、`RAG_READY_MIN_TEXT`、`RAG_READY_MIN_IMAGE` | 1、1、1 | 门控是否要求图片集合、最少条数 |
| `RAG_STORE` | `chroma` | `milvus` 即切换 |
| `RAG_MILVUS_URI` / `RAG_MILVUS_TOKEN` / `RAG_MILVUS_DB` | Lite 文件 / 空 / 默认库 | Standalone:`http://127.0.0.1:19530`,`user:password` |
| `RAG_MILVUS_CONSISTENCY` | `Strong` | 建集合时的一致性级别(1082 条、无流式写入,差别可忽略) |
| `RAG_MILVUS_VERSIONED` / `RAG_MILVUS_RETAIN` | 0 / 2 | 版本化物理集合 + 别名;保留几个物理集合 |
| `RAG_STORE_SHADOW` | 空 | `milvus` / `chroma`:影子查询 |
| `RAG_SHADOW_LOG` / `_TIMEOUT_MS` / `_WORKERS` / `_QUEUE` / `_LOG_MAX_MB` | `data/.shadow/shadow.jsonl` / 2000 / 1 / 64 / 200 | 影子参数 |
| `RAG_STORE_FALLBACK` + `RAG_STORE_FALLBACK_UNTIL` | 空 | 主存储抛错时限期由兜底存储回答;没写日期不启用,过期自动失效 |
| `RAG_CHROMA_DIR` | `data/.chroma` | 离线工具用另一份 Chroma(如 `data/.chroma_v2`) |

`/ready` 的 JSON 里始终多一个 `fallbacks` 字段:`dense_to_keyword`(稠密检索失败退回关键词的次数)、
`image_query_failed`,以及影子 / 兜底包装的计数。

---

## 1. 资源准备(需 sudo;PLAN §7 第 2 项,机主确认后执行)

```bash
# 1.1 Docker(官方 apt 源,带 compose 插件;compose 需 >= 2.20 才认识 depends_on.required)
#     按 https://docs.docker.com/engine/install/ubuntu/ 安装 docker-ce + docker-compose-plugin
docker compose version

# 1.2 数据目录 + 密码
sudo mkdir -p /srv/milvus/{etcd,milvus,minio}
cd ~/AAALion-/deploy/milvus
cp .env.example .env && chmod 600 .env && $EDITOR .env   # 三个不同的 12–72 字节密码

# 1.3 起服务(默认:etcd + milvus,本地磁盘存储;docker.io/minio/minio 已拉不到,见 deploy/README.md)
sudo docker compose up -d
sudo docker compose ps                       # 两个容器都 healthy(milvus 首次启动 start_period 90 s)
sudo docker logs milvus-standalone 2>&1 | grep -iE 'simd|avx|cpu' | head   # 首次启动确认 SIMD 检测正常
sudo ss -ltnp | grep -E ':19530|:9091'       # 必须只有 127.0.0.1,不能有 0.0.0.0 / [::]
curl -sf http://127.0.0.1:9091/healthz && echo healthy

# 1.4 客户端依赖(只装 pymilvus,不装 milvus-lite)
.venv/bin/pip install --dry-run -r server/requirements.txt -r server/requirements-milvus-server.txt
#     看清楚会升级什么:pymilvus 3.0.2 要 protobuf>=5.27.2、python-dotenv>=1.0.1,<2;
#     2026-10-06 本地解析(Py3.10/3.11, linux x86_64)只多出 pymilvus/cachetools/pandas(+pytz/tzdata)
.venv/bin/pip install -r server/requirements-milvus-server.txt
```

## 2. 初始化账号(轮换 root + 最小权限)

```bash
.venv/bin/python tools/milvus_bootstrap.py --env-file deploy/milvus/.env
.venv/bin/python tools/milvus_bootstrap.py --env-file deploy/milvus/.env --check   # 再核对一遍,存档输出
```

- `lionpick_app`:`CollectionReadOnly` + `Load`(API 进程用);`lionpick_admin`:`CollectionAdmin` + `DatabaseAdmin`
  + `RenameCollection`(入库、迁移、切别名用)。权限组名按 Milvus 文档核对,**没有在真实 Standalone 上跑过**。
- 把 app 账号写进只给 systemd 读的文件(不进 git):
  ```bash
  sudo install -d -m 700 /etc/lionpick
  sudo sh -c 'umask 077; cat > /etc/lionpick/milvus.secret.env' <<'EOF'
  RAG_MILVUS_URI=http://127.0.0.1:19530
  RAG_MILVUS_TOKEN=lionpick_app:<LIONPICK_MILVUS_APP_PASSWORD>
  EOF
  ```
- **权限自检**(必须做,存档):
  ```bash
  export RAG_MILVUS_URI=http://127.0.0.1:19530
  RAG_MILVUS_TOKEN=lionpick_app:...   .venv/bin/python -m rag.store.alias --list          # 应成功(只读)
  RAG_MILVUS_TOKEN=lionpick_app:...   .venv/bin/python -m rag.store.alias --rollback      # 应被拒绝(permission denied)
  ```
  如果 app 账号连 `--list` 都失败,先看 `describe_role`(`--check` 输出),再按服务端报错补具体权限
  (例如 `DescribeCollection` / `ShowCollections`),不要直接给 admin 组。

## 3. 离线一致性(不动线上 Chroma,向量只算一次)

线上 Chroma 是旧索引,不能直接当参照。在旁边建一份 v2 的 Chroma,同时得到 npz 向量文件:

```bash
# 3.1 低峰时段:算一次向量(CPU,约几分钟),写进 data/.chroma_v2,并存 data/.embeddings/*.npz
RAG_CHROMA_DIR=data/.chroma_v2 .venv/bin/python -m rag.ingest.run --rebuild
RAG_CHROMA_DIR=data/.chroma_v2 .venv/bin/python -m rag.ingest.run_image --rebuild
sha256sum data/.embeddings/*.npz | tee docs/bench/embeddings_sha256_$(date +%Y%m%d).txt

# 3.2 搬进 Milvus,不重算向量;版本化 + 别名(用 admin 账号)
export RAG_MILVUS_URI=http://127.0.0.1:19530 RAG_MILVUS_TOKEN=lionpick_admin:... RAG_MILVUS_VERSIONED=1
RAG_CHROMA_DIR=data/.chroma_v2 .venv/bin/python -m rag.store.migrate --from chroma --to milvus --rebuild
.venv/bin/python -m rag.store.alias --list      # products_text -> products_text__v2_<时间戳>  rows=1082;image 145

# 3.3 一致性:exit 0 才继续
RAG_CHROMA_DIR=data/.chroma_v2 .venv/bin/python -m rag.eval.store_parity
# 两个后端上评测逐字相同(diff 为空),多跳 34/34
RAG_CHROMA_DIR=data/.chroma_v2 RAG_STORE=chroma .venv/bin/python -m rag.eval.run > /tmp/eval_chroma.txt
RAG_STORE=milvus .venv/bin/python -m rag.eval.run > /tmp/eval_milvus.txt && diff /tmp/eval_chroma.txt /tmp/eval_milvus.txt
```

另一条路(已有 npz 时,不需要 torch):`RAG_STORE=milvus RAG_MILVUS_VERSIONED=1 .venv/bin/python -m rag.store.load
data/.embeddings/products_text.npz --collection text --rebuild`(image 同理)。

版本化写入的规则:`--rebuild` 写进新物理集合,`seal()` 核对条数无误才切别名;条数不符**不切**并删掉半成品;
第一次切别名时,如果 Milvus 里已有直接叫 `products_text` 的老集合,会改名为 `products_text__legacy_<时间戳>` 保留。
非版本化模式(`RAG_MILVUS_VERSIONED=0`)对已经是别名的集合做 `--rebuild` 会直接报错——这是故意的。

## 4. 回放门槛

```bash
RAG_CHROMA_DIR=data/.chroma_v2 RAG_MILVUS_TOKEN=lionpick_app:... \
  .venv/bin/python -m rag.eval.replay_gate --label vm-$(date +%Y%m%d)
# 可选:--p95-max-ms 50(PLAN 的初值,按实测校准)  --min-calls 1500
```

通过标准:错误 0;(top-10 一致 + 被精确解解释)/ 总调用 ≥ 0.99。报告 `docs/bench/replay_gate_<label>.json` 提交进仓库。
本地参考(**milvus-lite 服务模式,M 系列笔记本,不是 Standalone**):`docs/bench/replay_gate_local-lite-20261006.json`,
1410 次调用、错误 0、一致率 0.9957、含解释 1.0000。

## 5. 线上影子(48 小时)

一次提交只改一个变量。正向影子(Chroma 继续回答,Milvus 陪跑):

```ini
# /etc/systemd/system/lionpick.service.d/20-vector-store.conf(或 P0.7 落地后的 deploy/prod.env)
[Service]
Environment=RAG_STORE_SHADOW=milvus
```

```bash
sudo install -D -m 644 deploy/systemd/lionpick.service.d/10-milvus.conf /etc/systemd/system/lionpick.service.d/
sudo systemctl daemon-reload && sudo systemctl restart lionpick
# 48 小时后
.venv/bin/python -m rag.store.composite --summarize data/.shadow/shadow.jsonl | tee docs/bench/shadow_$(date +%Y%m%d).json
```

读法:看 `products_text|unfiltered` 与 `products_image|unfiltered` 的 `overlap_mean` 和错误 / 超时;
`|filtered` 组在正向影子期间**两边语义本来就不同**(线上旧 Chroma 没有 `currency` / `brand_country`,
同一个 where 在两边筛出的集合不同),低 overlap 不是 Milvus 的问题——这一组以第 4 步的回放门槛为准。
影子在独立线程里跑、排队满了就丢,不影响用户请求;`/ready` 的 `fallbacks.store_wrappers.shadow` 有计数。

## 6. 门控、故障演练与 reboot 演练

```bash
# 6.1 先 report 看一眼,再 enforce
sudo systemctl edit lionpick   # 临时加 Environment=RAG_READY_GATE=report,restart 后:
curl -s http://127.0.0.1:8000/ready | python3 -m json.tool | sed -n '/vector_store_gate/,/}/p'   # ok: true
sudo install -D -m 644 deploy/systemd/lionpick.service.d/05-ready-gate.conf /etc/systemd/system/lionpick.service.d/
sudo systemctl daemon-reload && sudo systemctl restart lionpick

# 6.2 故障演练(切换后做;切换前做的话门控查的是 Chroma,演练无意义)
sudo docker stop milvus-standalone
sleep 35; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/ready    # 期望 503(告警应触发)
#   兜底开着时用户路径由 Chroma 回答:/ready 的 fallbacks.store_wrappers.fallback.served_by_fallback 增加
sudo docker start milvus-standalone
#   healthy 后最多 RAG_READY_GATE_TTL_S 秒,/ready 自动回到 200,不需要重启 API
#   (本地对 milvus-lite 服务做过同样的 kill / 重启,门控 503 → 200 自动恢复,见 §9)

# 6.3 reboot 演练
sudo reboot
# 回来后:
systemctl status docker lionpick --no-pager
sudo docker compose -f ~/AAALion-/deploy/milvus/docker-compose.yml ps   # restart: unless-stopped 自动拉起
journalctl -u lionpick -b | grep -i milvus    # 10-milvus.conf 的 ExecStartPre 最多等 80 s healthz
curl -sf http://127.0.0.1:8000/ready | grep -q ready && echo READY
```

## 7. 切换(一次只改一个变量)

1. 存档切换前基线:`curl -s localhost:8000/ready`、`stress_e2e` 延迟、`/ready` 门控结果。
2. 一次提交:`RAG_STORE=milvus` + 反向影子 `RAG_STORE_SHADOW=chroma` + 限期兜底
   `RAG_STORE_FALLBACK=chroma`、`RAG_STORE_FALLBACK_UNTIL=<今天+14 天>`。兜底与反向影子读的是线上旧 Chroma
   (它还能用,只是没有新字段,过滤语义与 Milvus 不完全一样——这正是兜底要限期的原因)。
3. autodeploy 的 `/ready` 检查(门控 enforce)不过会自动回滚这次部署(L1)。
4. 48 小时后再单独一次提交打开别的变量(例如过滤下推相关),不要和切换捆在一起。
5. 验收证据:`/ready` 显示 backend=milvus、mode=server、schema v2、1082/145、`physical` 为具体的
   `products_text__v2_<时间戳>`;回放 + 影子报告;演练日志;VM 上的延迟对比(1082 条时可能比进程内 Chroma 还慢,照实写)。

## 8. 回滚

| 层 | 触发 | 动作 | 恢复时间(预期) |
|---|---|---|---|
| L1 | 部署后门控不过 | autodeploy 自动 `reset --hard` 回上一个 SHA 并重启(配置在 git 里的话一起退回) | ~2–3 分钟 |
| L2 | 切换后发现问题 | `git revert <切换提交>` 推 main;`data/.chroma` 至少保留 14 天 | ~2–3 分钟 + 一次重启 |
| L3 | Milvus 运行中出错 | 不用动:`RAG_STORE_FALLBACK=chroma` 在截止日期前自动接住(计数可见);过期后退回关键词检索 | 即时(降级) |
| 别名 | 新一版索引有问题 | `RAG_MILVUS_TOKEN=lionpick_admin:... python -m rag.store.alias --rollback`(或 `--switch <物理集合>`) | 秒级,无需重启 API |

`FALLBACK_UNTIL` 到期后**删掉**兜底配置,而不是续期;要续期就意味着该做的验证没做完。

## 9. 向量是派生数据:npz 重建演练

主恢复路径是"npz + sha256 → 分钟级重建",不是备份 Milvus。演练(存档输出):

```bash
sha256sum -c docs/bench/embeddings_sha256_<日期>.txt
export RAG_MILVUS_TOKEN=lionpick_admin:... RAG_MILVUS_VERSIONED=1
time RAG_STORE=milvus .venv/bin/python -m rag.store.load data/.embeddings/products_text.npz  --collection text  --rebuild
time RAG_STORE=milvus .venv/bin/python -m rag.store.load data/.embeddings/products_image.npz --collection image --rebuild
.venv/bin/python -m rag.store.alias --list         # 新物理集合在服务,上一版保留可回滚
RAG_CHROMA_DIR=data/.chroma_v2 .venv/bin/python -m rag.eval.store_parity   # exit 0
```

milvus-backup(0.6.0)按 PLAN 只**演示一次**:备份 → 恢复到 `restore_drill` 库 → 用 `RAG_MILVUS_DB=restore_drill` 跑 parity;不设每晚任务。

## 10. 监控(PLAN P1 第 7 步,另行落地)

Milvus 的 `127.0.0.1:9091/metrics` 与应用 `/ready` 的 `fallbacks` 计数是告警的数据源:healthz 连续 2 分钟失败、
容器重启、`dense_to_keyword` / `served_by_fallback` 增加、p95 超阈值、磁盘 > 80%、Milvus 内存 > mem_limit 的 80%。
Attu 不常驻,需要时 `ssh -L` 临时开。

## 11. 不能说的话

- ❌ "零停机":单实例,切换就是一次重启(时长见 P0.10 实测)。
- ❌ "迁 Milvus 是为了降延迟":1082 条时 Standalone 走 gRPC,可能比进程内 Chroma 还慢;价值在扩展性和运维闭环。
- ❌ "高可用":单机,etcd / Milvus 都是单副本。
- ❌ "在生产上压测过百万级":百万级数据来自 M4 笔记本上的 Lite(`docs/VECTOR_STORE.md` §7),不是这台 VM。
- ❌ 把本地 milvus-lite 的回放延迟当 Standalone 的数字;任何不能指向存档输出的数字。
- ❌ "权限是最小的且验证过":在 §2 权限自检存档之前,只能说"按文档配置"。

---

## 已验证 / 未验证(2026-10-06)

**本地实跑过的**(macOS,共享 dev venv,milvus-lite 3.2.1 服务模式,端口 19633/19634,数据在 `/private/tmp`):

- 版本化迁移:`RAG_MILVUS_VERSIONED=1 rag.store.migrate --from chroma --to milvus --rebuild` → 别名指向
  `products_text__v2_<时间戳>`(1082)/ `products_image__v2_<时间戳>`(145);`rag.store.alias --list/--rollback/--switch`;
  非版本化 reset 经由别名被拒绝;建与别名同名的集合被拒绝;条数不符不切别名。
- `store_parity` exit 0;`replay_gate` 1410 次调用 PASS(报告已提交);指向错误的库时 FAIL(exit 1)。
- 后端以 `RAG_STORE=milvus RAG_READY_GATE=enforce` 跑在 127.0.0.1:18765:`/ready` 200(门控显示 physical 集合与 1082/145);
  kill milvus-lite 服务后 503(`reason=vector_store_gate`),重启服务后自动回到 200。
- 兜底:Milvus 不可达时由 Chroma 回答(`served_by_fallback=1`);截止日期已过时不兜底、退回关键词(`dense_to_keyword=1`)。
- `docker compose config` 校验 compose 文件(默认与 `--profile minio` 两种);`10-milvus.conf` 的 ExecStartPre 脚本用 `sh` 跑过三种情形。
- 依赖解析:`uv pip compile` Py3.10 / 3.11、linux x86_64。

**没有验证的**:真实 Milvus Standalone v3.0.2(本机没起 Docker 镜像)、鉴权与权限组的实际效果、
systemd drop-in 在真实 systemd 上的行为、VM 上的任何步骤、MinIO profile(没有可拉取的镜像)。
