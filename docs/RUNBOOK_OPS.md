# RUNBOOK_OPS:部署、CI 门禁、配置同步、备份、监听地址

> 适用:生产 VM(`34.73.32.90`;`34.139.88.204` 是旧 IP)、用户 `yushengli`、仓库 `/home/yushengli/AAALion-`、
> 服务 `lionpick`(uvicorn 单进程)/ `lionpick-tunnel`(cloudflared quick tunnel)/
> `lionpick-autodeploy.timer`(每 2 分钟)。Milvus 相关操作见 [`RUNBOOK_MILVUS.md`](RUNBOOK_MILVUS.md),
> 目标与度量见 [`SLO.md`](SLO.md)。
>
> **本文档描述的代码在本地测过(见各节"验证"),但在 VM 上还没跑过。** 第一次上线时按 §7 的清单逐项确认。

---

## 1. 部署流程

```
PR ──► CI(pytest py3.10 + py3.11,检索评测门禁)──► 合并 main ──► main 上再跑一次 CI
                                                                  │
lionpick-autodeploy.timer(每 2 分钟)── tools/cloud-autodeploy.sh ◄┘
  1. git fetch;origin/main == HEAD → 结束
  2. origin/main 是已知坏 SHA(~/.lionpick-autodeploy-bad-sha)→ 结束
  3. 查 GitHub check-runs(公开 API,无 token):
       全部必需 job completed+success → 继续
       还在跑 / 还没出现               → 本轮跳过,下一轮再查
       有 failure / timed_out          → 写坏 SHA 标记,不部署
       API 不通 / 限流(403)           → 本轮跳过,**绝不盲部署**
  4. git reset --hard <sha>
  5. 同步 drop-in(§3),有变化就 daemon-reload
  6. systemctl restart lionpick;150 s 内等 /ready 返回 2xx
  7. 不就绪 → 代码和 drop-in 一起回滚到部署前,daemon-reload,restart,写坏 SHA 标记
```

必需的 job 名(脚本默认值,必须和 `ci.yml` 一致,`server/tests/test_cloud_autodeploy.py` 有一致性检查):
`pytest (py3.10)`、`pytest (py3.11)`、`retrieval eval gate`。

看日志:

```bash
journalctl -u lionpick-autodeploy --no-pager -n 50
```

| 日志 | 含义 / 处理 |
|---|---|
| `CI pending for <sha> (...)` | CI 还在跑,等就行。一直 pending 且出现 `=missing`:那个 SHA 上 CI 根本没触发,去 Actions 页手动跑 |
| `CI FAILED for <sha>` | 已写坏 SHA 标记。修好再合并即可(main 前进后标记自动失效)。若确认是偶发失败:在 GitHub 上 Re-run,绿了以后 `rm ~/.lionpick-autodeploy-bad-sha` |
| `CI status unavailable ... http=000` | VM 连不上 api.github.com(超时 10 s)。下一轮自动重试 |
| `... http=403` | 未认证 API 限流(每 IP 每小时 60 次)。脚本只在 main 领先且不是坏 SHA 时才查,pending 期间每 2 分钟 1 次 = 30 次/小时,正常不会触发 |
| `deploy FAILED ready-check — rolling back` | 新版本 150 s 内没就绪。`journalctl -u lionpick -n 200` 看启动报错 |
| `WARNING: AUTODEPLOY_REQUIRE_CI=0` | 有人用了紧急开关 |

**紧急部署(跳过 CI 检查)**:只在 CI 本身坏了(GitHub 故障等)而线上必须修时用,用完即止:

```bash
cd ~/AAALion- && AUTODEPLOY_REQUIRE_CI=0 bash tools/cloud-autodeploy.sh
```

/ready 回滚判定仍然生效。不要把 `AUTODEPLOY_REQUIRE_CI=0` 写进 timer 的 unit。

**建议(机主在 GitHub 设置里操作)**:给 main 开 branch protection,把上面三个 job 设为 required checks。
仓库是公开的,任何能合并到 main 的人都能改线上代码和 systemd 配置(§3),所以合并权限要收紧。

## 2. CI 检索评测门禁

`.github/workflows/ci.yml` 的 `retrieval eval gate` job:

```bash
python -m rag.eval.gate build-index            # 只用 CPU,从 data/seed 重建 Chroma 文本索引
python -m rag.eval.gate run --out eval_current.json
python -m rag.eval.gate compare --baseline docs/bench/eval_baseline.json --current eval_current.json
```

- 只跑生产路径 `hybrid_rerank`,两个评测集:`golden.jsonl`(92)和 `golden_compositional.jsonl`(61)。
- 重排参数用线上值:`RERANK_INPUT_CAP=10`、`RERANK_MAX_LENGTH=128`(workflow 里显式写了;本地默认是 0 / 256)。
  另外固定了:CPU 设备、汇率表(USD→CNY 7.10,不访问 Frankfurter)、Chroma 后端。
- **判失败**:基线里命中(recall@5 > 0)的 case 变成未命中(逐条列出);基线里 top-5 干净的反选 case 漏出 forbidden;
  任一评测集平均 recall@5 或 MRR 下降 > 0.02;有 case 抛异常;有 LLM 请求被拦截。
- **退出码 2 = 设置不一致**(rerank 参数 / 汇率表 / 模式和基线不同):数字没有可比性,需要重新生成基线。
- **不调用 LLM**:检索链路里只有 `rag/retrieve/negation.py` 和 `rewrite.py` 会请求 LLM,都只在
  `TOKENROUTER_API_KEY` 非空时发请求;workflow 把所有 key 置空,`gate.py` 另外拦截发往 LLM 网关的 urllib / httpx 请求。

基线现状(2026-10-07,本机 macOS arm64、强制 CPU、torch 2.12.0 / transformers 4.57.6):
golden recall@5 0.929 / MRR 0.863 / 命中 77/80;compositional recall@5 0.821 / MRR 0.878 / 命中 55/58。
同一台机器重建索引后连跑两次,153 个 case 的 top-10 完全一致。

**有意改变检索效果时(更新基线)**:在同一个 PR 里更新 `docs/bench/eval_baseline.json`,并在提交说明里写清楚哪些 case 变了、为什么。两种做法:

1. 用 CI 的结果:下载该 PR 的 `retrieval-gate` artifact,把 `eval_current.json` 复制成 `docs/bench/eval_baseline.json`;
2. 本地生成(命令同上,先 `export RERANK_INPUT_CAP=10 RERANK_MAX_LENGTH=128 TOKENROUTER_API_KEY=`,
   建议 `export RAG_CHROMA_DIR=/tmp/chroma_gate`,别覆盖自己的开发索引)。

**跨平台注意**:基线是在 macOS arm64 上生成的,CI 是 linux x86_64。两边都是 CPU、版本也钉住了
(`.github/ci-constraints.txt`),但浮点细节仍可能让分数很接近的 case 在第 5 / 6 名之间换位。如果第一次 CI 运行只因为这种差异失败
(失败的 case 在本地是命中的),就按做法 1 改用 CI 生成的基线。**这一点还没在 CI 上验证过。**

## 3. systemd drop-in 同步(配置进 git)

仓库里的 `deploy/systemd/lionpick.service.d/*.conf` 是线上 `lionpick.service` 的配置来源:

| 文件 | 内容 |
|---|---|
| `05-ready-gate.conf` | `RAG_READY_GATE=enforce` |
| `10-milvus.conf` | docker 依赖、Milvus 凭据文件、启动前等 healthz |
| `20-vector-store.conf` | `RAG_STORE=milvus` / 影子 chroma / 兜底 chroma 到 2026-10-21 / `RAG_CHROMA_DIR=data/.chroma_v2` |
| `30-bind-localhost.conf` | uvicorn 只监听 127.0.0.1(§5) |
| `40-demo-mode.conf` | `DEMO_MODE=1`(归属未定,见文件内注释)、`AUTH_ENFORCE_MODE=report` |

规则:
- autodeploy 每次部署把这些文件用 `install -m 644` 装进 `/etc/systemd/system/lionpick.service.d/`(内容相同就不动),有变化就 `daemon-reload`,**然后**才 restart。
- 只管理仓库目录里存在的文件。`jwt.conf`(`LIONPICK_JWT_SECRET`)和其它机器本地文件**永远不碰**;
  `jwt.conf` 就算被误提交进仓库,脚本也拒绝安装(测试覆盖)。机密只能放在 VM 本地。
- 装过的文件名记在 `~/.lionpick-autodeploy-dropins`。仓库里删掉一个 drop-in,只有它在这个清单里才会从 `/etc` 删除。
- 部署前给相关文件拍快照,**回滚时按快照恢复**(内容、有 / 无都恢复)。稳态下快照就是上一个 SHA 树里的 drop-in;
  第一次接管手工装的 05 / 10 / 20 时,如果部署失败,它们会恢复成手工版本而不是被删掉。

核对线上实际生效的配置:

```bash
systemctl cat lionpick                       # unit + 所有 drop-in
systemctl show lionpick -p Environment       # 合并后的环境变量(会显示 RAG_STORE 等,不要贴到公开地方:也会显示 jwt.conf 里的值)
ls -l /etc/systemd/system/lionpick.service.d/ && cat ~/.lionpick-autodeploy-dropins
```

sudo 权限:脚本需要无密码执行 `systemctl restart lionpick`、`systemctl daemon-reload`、`install`、`rm -f`、`mkdir -p`
(目标都在 drop-in 目录下)。GCP 默认把登录用户放进 `google-sudoers`(全权限无密码),先用 `sudo -l` 看现状;如果是收紧过的 sudoers,
补上(`visudo -f /etc/sudoers.d/lionpick-autodeploy`):

```
yushengli ALL=(root) NOPASSWD: /usr/bin/systemctl restart lionpick, /usr/bin/systemctl daemon-reload, \
  /usr/bin/install -m 644 * /etc/systemd/system/lionpick.service.d/*, \
  /usr/bin/rm -f /etc/systemd/system/lionpick.service.d/*, \
  /usr/bin/mkdir -p /etc/systemd/system/lionpick.service.d
```

(sudoers 参数里的 `*` 匹配很宽,这几行本质上等于"能改 lionpick 的 unit 配置"——这和"能合并到 main"是同一个信任边界。)

验证:`server/tests/test_cloud_autodeploy.py` 用临时 git 仓库 + 假 curl / systemctl 跑真实脚本,覆盖
绿 / pending / 缺 job / cancelled / 失败 / API 不通 / 限流 / 紧急开关 / 就绪失败回滚(代码 + drop-in)/ 删除受管 drop-in / 拒绝 jwt.conf。

## 4. SQLite 备份与恢复演练

用户数据在 `data/*.db`(users / preferences / price_watch / repurchase / group_buy,WAL 模式)。向量是派生数据,不在这里备份
(主恢复路径是 npz 重建,见 RUNBOOK_MILVUS.md)。

**安装(一次性)**:

```bash
cd ~/AAALion-
sudo install -m 644 deploy/systemd/lionpick-backup.service deploy/systemd/lionpick-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now lionpick-backup.timer
sudo systemctl start lionpick-backup.service            # 先手动跑一次
journalctl -u lionpick-backup -n 20 --no-pager           # 最后一行是 JSON 摘要
systemctl list-timers lionpick-backup.timer
```

- 每天 03:30 UTC + 最多 30 分钟随机延迟;`Persistent=true`,关机错过的会在开机后补跑。
- 输出:`~/lionpick-backups/sqlite/<UTC 时间戳>/{<name>.db.gz, manifest.json, SHA256SUMS}`,目录权限 700。
- 方法:Python `sqlite3` 在线备份 API(与 `sqlite3 .backup` 同一机制;VM 上没装 sqlite3 CLI),源库以 `mode=ro` 打开,服务不停;
  每个副本跑 `PRAGMA integrity_check`;保留 14 天,最新一份永远保留。
- 任何库缺失或 integrity_check 不是 ok → 退出码 1,`systemctl status lionpick-backup` 会显示 failed。

**恢复演练(只读,建议每次改动备份脚本后、以及每月一次)**:

```bash
python3 tools/restore_drill.py                 # 最新备份 → 临时目录;校验 sha256、integrity_check、逐表行数
python3 tools/restore_drill.py --strict        # 低峰时跑:线上和备份逐表行数必须完全一致
```

默认模式下,线上在备份之后又有写入只会记为 `drift`;表结构不一致、校验和不对、integrity 失败才判失败。

**真正恢复某个库**(例:preferences.db 被误删数据):

```bash
B=~/lionpick-backups/sqlite/<时间戳>
cd $B && sha256sum -c SHA256SUMS
sudo systemctl stop lionpick
cd ~/AAALion-/data && mkdir -p ~/restore-aside && mv preferences.db* ~/restore-aside/   # 先挪开,不删
gunzip -c $B/preferences.db.gz > preferences.db
sudo systemctl start lionpick
```

**限制**:备份和数据在同一块盘上,只防误操作 / 逻辑损坏,**不防 VM 或磁盘丢失**。异地副本(GCS)要机主决定
(PLAN.md §7 第 3 项:少量费用,用户数据会离开 VM)。

验证:`server/tests/test_backup_sqlite.py`(临时 WAL 库:WAL 里未 checkpoint 的数据也被备份、manifest / SHA256SUMS、
源库字节不变、缺库 / 坏库报错、保留期、恢复演练 strict / drift / 篡改检测、CLI 端到端)。

## 5. uvicorn 只监听本机 + 不重启 tunnel 的验证方法

`30-bind-localhost.conf` 把 `--host 0.0.0.0` 改成 `--host 127.0.0.1`。**合并即生效**(autodeploy 同步 + 重启)。

**合并前**(在 VM 上确认没人直连 8000,以及 tunnel 连的是回环):

```bash
systemctl cat lionpick-tunnel | grep -- --url        # 应是 http://localhost:8000
getent ahosts localhost                               # 应包含 127.0.0.1(只有 ::1 的话 cloudflared 连不上只绑 IPv4 的 uvicorn)
# 过去一天里非回环来源的请求(uvicorn 访问日志的客户端地址);有输出说明还有客户端直连 IP:8000
journalctl -u lionpick --since -24h --no-pager | grep -E 'HTTP/1\.[01]"' | grep -vE ' (127\.0\.0\.1|::1|\[::1\]):[0-9]+ ' | head
sudo ss -tn state established '( sport = :8000 )'     # 当前连接的对端
```

**合并后验证(不要重启 tunnel——quick tunnel 一重启 URL 就变)**:

```bash
sudo ss -ltnp | grep ':8000'                          # 只应看到 127.0.0.1:8000
curl -sf http://127.0.0.1:8000/ready >/dev/null && echo local-ok
# 现有 tunnel 的公网 URL(从 tunnel 自己的日志里取,不重启)
URL=$(journalctl -u lionpick-tunnel --no-pager | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1); echo $URL
curl -sf --max-time 10 "$URL/health" && echo tunnel-ok
journalctl -u lionpick-tunnel --since -10min --no-pager | grep -iE 'error|refused|unable to reach' | tail
```

再从 VM 外面(笔记本)确认:`curl -sf "$URL/health"` 成功,`curl -m 5 http://34.73.32.90:8000/health` 失败。
uvicorn 重启期间 tunnel 会短暂返回 502,origin 回来后 cloudflared 自己重新连上(之前每次部署重启都是这样),不需要重启 tunnel。

纵深防御(机主用 gcloud 操作):把 GCP 防火墙里放行 tcp:8000 的规则删掉。

**回退**:提交删除 `30-bind-localhost.conf`(autodeploy 会从 `/etc` 移除并 daemon-reload + restart)。

**副作用**:之后所有请求的 socket 对端都是 127.0.0.1。按 IP 限流 / 记录客户端 IP 的代码必须读 `CF-Connecting-IP`,
而且只在对端是回环时才信任这个头(否则任何人都能伪造)。

## 6. 其它

- `/ready` 现在带 `llm` 字段:`requested`(LLM_PROVIDER 原值)、`provider`(实际生效的,key 缺失时会显示 `echo`)、
  `model`、`agent_model`(设置了 AGENT_LLM_MODEL 时)。不含 key 和 base_url。
  `curl -s http://127.0.0.1:8000/ready | python3 -c 'import json,sys; print(json.load(sys.stdin)["llm"])'`
- TokenRouter 默认模型统一为 `claude-haiku-4-5`(llm_provider.py、negation.py、.env.example 一致;线上 .env 本来就显式设了它)。

## 7. 第一次上线检查清单(合并本批改动时)

1. 合并前:按 §5 确认没有直连 8000 的客户端、`localhost` 解析到 127.0.0.1;`sudo -l` 确认 sudo 权限(§3)。
2. 合并后第一次 CI 在 main 上跑完前,autodeploy 会一直 `CI pending`(正常)。若 retrieval gate 因跨平台差异失败,按 §2 处理。
3. 部署后:`journalctl -u lionpick-autodeploy -n 30` 应看到 `drop-in installed: 20-vector-store.conf / 30-bind-localhost.conf / 40-demo-mode.conf`
   (05 / 10 内容相同则不打印)、`daemon-reload`、`deploy OK`。
4. `systemctl cat lionpick` 里 jwt.conf 仍在;`curl -s 127.0.0.1:8000/ready` 里 `vector_store_gate.backend` 仍是 milvus、`llm.model` 是 haiku。
5. 按 §5 验证 tunnel;按 §4 安装备份 timer 并跑一次恢复演练,把输出存档。
