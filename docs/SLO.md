# SLO(初版,2026-10-07)

> 适用:单台 GCP VM(4 vCPU / 15 GB,Ubuntu 22.04,Python 3.10)、**单个 uvicorn 进程**、公网入口是
> Cloudflare **quick tunnel**、向量库 Milvus Standalone(同机 Docker)+ Chroma 兜底。
> 这是单机服务,**不是高可用**:VM 宕机 = 整个服务不可用,SLO 只是把"可用"定义成能度量的数字。
> 标注:**[实测]** 有存档数据;**[目标]** 只是目标,还没有持续度量。

## 1. 指标与目标

| SLI | 定义 | 目标 | 现状 |
|---|---|---|---|
| 可用性 | 外部探针每分钟 `GET /ready`,2xx 的比例(按月) | **≥ 99.5%**(每月约 216 分钟错误预算) | **[目标] 未度量**:没有固定域名,见 §3 |
| 快路首字时间(TTFT) | `/chat/stream` 收到请求 → 第一个回答文本事件,p95 | **≤ 3 s**(中文、检索缓存未命中、快路) | **[目标] 未持续度量**:只有零散观测(TokenRouter haiku 首 token 约 2 s,见 CLAUDE.md §9.9) |
| 5xx 比例 | 5xx 响应数 / 全部响应数(按天),**不含**计划内重启窗口里的 503 | **≤ 1%** | **[目标] 未自动统计**;可从 uvicorn 访问日志手工数(§4) |
| 计划内重启停服 | `systemctl restart lionpick` → `/ready` 返回 200 | **单次 ≤ 60 s** | **[实测] 21–40 s**(2026-10-07,Milvus 模式,多次重启;PLAN.md §9) |

已知例外(不计入 TTFT 目标):英文 / 含英文品牌词的冷查询走 `bge-reranker-v2-m3`,CPU 上冷启动 25–40 s(CLAUDE.md §9.9);
检索缓存(TTL 300 s)命中后很快。

## 2. 错误预算怎么花

- 每次合并到 main = autodeploy 一次重启 = 约 21–40 s 的 503。按 40 s 算,每月 216 分钟的预算够约 300 次部署,
  所以**部署频率不是瓶颈**;真正吃预算的是"坏版本上线到回滚"的时间:最坏情况 = 150 s 就绪窗口 + 回滚重启约 40 s ≈ 3 分钟。
- CI 门禁(`.github/workflows/ci.yml` + autodeploy 只部署绿 SHA)就是为了把"坏版本上线"挡在部署之前。
- 预算用完(月内可用性 < 99.5%)时:冻结除修复以外的合并,先查原因。**目前没有探针,这条规则还无法执行。**

## 3. 还没度量的东西,以及为什么

| 项目 | 为什么还没做 | 前置条件 |
|---|---|---|
| **外部探活 / 可用性数字** | 入口是 quick tunnel,URL(`*.trycloudflare.com`)每次 tunnel 重启都会变,探针没有稳定目标;直连 `IP:8000` 正在关闭(`30-bind-localhost.conf`),也不能当探针目标 | 固定域名(Cloudflare named tunnel,PLAN.md §7 第 1 项,需要机主的账号 / 域名) |
| **告警** | 没有探针就没有可告警的信号;告警通道(邮件 / 飞书)也没定 | 固定域名 + 机主选通道(PLAN.md §7 第 5 项) |
| **容量(单 worker 的 QPS 拐点)** | 压 `/chat/stream` 每个请求都会调付费 LLM,现阶段**不允许为压测调用 LLM**;在生产机上压测也会直接伤害线上 | 用 `LLM_PROVIDER=echo` 在非生产端口 / 临时机上压检索部分(`tools/stress_e2e.py`),或者有 LLM 预算后再测 |
| **TTFT 持续统计** | 没有 `/metrics`,首字时间没有被记录成可聚合的数据 | P0.5 的 `/metrics`(只绑本机)+ 首字时间直方图 |
| **5xx 自动统计** | 同上,没有 metrics;现在只能翻日志 | 同上 |

## 4. 现在能手工看的数据

```bash
# 重启停服时长(计划内):在 VM 上
t0=$(date +%s); sudo systemctl restart lionpick
until curl -sf --max-time 5 http://127.0.0.1:8000/ready >/dev/null; do sleep 1; done; echo "$(( $(date +%s) - t0 )) s"

# 过去 24 小时的 5xx 粗略计数(uvicorn 访问日志在 journal 里)
journalctl -u lionpick --since -24h --no-pager | grep -cE '" 5[0-9]{2}'
journalctl -u lionpick --since -24h --no-pager | grep -cE 'HTTP/1\.[01]" [0-9]{3}'

# 当前生效的 LLM provider / 模型(不含 key)
curl -s http://127.0.0.1:8000/ready | python3 -c 'import json,sys; print(json.load(sys.stdin).get("llm"))'
```

## 5. 修订记录

- 2026-10-07:初版。目标沿用 PLAN.md P0 的草案;计划内重启时长换成实测 21–40 s。
