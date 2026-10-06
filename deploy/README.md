# deploy/

生产 VM(单机 Ubuntu 22.04,4 vCPU / 15 GB,和 API 进程共用)上的部署资产。
完整操作步骤、演练和回滚见 [`docs/RUNBOOK_MILVUS.md`](../docs/RUNBOOK_MILVUS.md);这里只说明每个文件是干什么的。

| 文件 | 用途 |
|---|---|
| `milvus/docker-compose.yml` | Milvus Standalone v3.0.2(显式钉版本)+ etcd;可选 MinIO(`--profile minio`)。端口只绑 127.0.0.1,鉴权打开,`restart: unless-stopped`,每个容器有 `mem_limit`,数据在 `/srv/milvus` |
| `milvus/user.yaml` | 覆盖镜像内 `milvus.yaml`:`common.security.authorizationEnabled: true`。挂载到 `/milvus/configs/user.yaml` |
| `milvus/.env.example` | 复制成 `milvus/.env`(gitignore,`chmod 600`):root / app / admin 三个密码,存储类型 |
| `systemd/lionpick.service.d/05-ready-gate.conf` | `RAG_READY_GATE=enforce`:向量库门控不过时 `/ready` 返回 503,autodeploy 回滚 |
| `systemd/lionpick.service.d/10-milvus.conf` | `After=/Wants=docker.service`;读 `/etc/lionpick/milvus.secret.env`;`RAG_STORE=milvus` 时启动前最多等 80 s Milvus healthz |
| `../tools/milvus_bootstrap.py` | 轮换 root 密码,建 `lionpick_app`(只读 + Load)和 `lionpick_admin`(入库 / 切别名) |

几个必须知道的事实(2026-10-06 核实):

- 官方 v3.0.2 tag 下的 compose 文件里镜像还写着 `v3.0.1`;本目录显式钉 `milvusdb/milvus:v3.0.2`(Docker Hub manifest 已核实存在)。
- `docker.io/minio/minio` 已经拉不到(registry 返回 401;MinIO 2025 年底停止发布社区镜像)。所以默认用 Milvus 的本地磁盘存储(`COMMON_STORAGETYPE=local`,与官方 `standalone_embed.sh` 相同的模式);要用 MinIO 必须自己指定一个可信镜像(按 digest 钉)。
- Docker 发布的端口会绕过 ufw,所以每个 `ports:` 都写成 `127.0.0.1:…`;部署后用 `sudo ss -ltnp | grep -E '19530|9091'` 确认只监听 127.0.0.1。
- 这些文件在本地只做过 `docker compose config` 语法校验,**没有在 VM 上起过**。
