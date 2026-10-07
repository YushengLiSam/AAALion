# SECURITY.md:P0.0 安全止血

> 2026-10-07。对应 PLAN.md §1.5 "线上保护网的洞" 里的三条:验证码随响应返回、越权(IDOR)、没有限流;外加 JWT 默认密钥的失效保护。
> 标注:**[单测]** pytest 跑过;**[本机实跑]** 在本机起了真实 uvicorn,用 curl 走真实 HTTP 打过;**[未验证]** 没跑过。
> 线上 VM 上**都还没跑过**——上线后按文末"上线核对"逐条补证据。

---

## 1. 威胁 → 修复 → 开关 → 怎么验证的

| # | 威胁 | 修复 | 开关(环境变量) | 验证 |
|---|---|---|---|---|
| 1 | **验证码随响应返回**:本地后端把 `dev_code` 放在 `/auth/phone/start`、`/auth/password/reset/start` 的响应里,知道手机号就能登录别人的账号 | `user_store.LocalUserStore`:只有 `DEMO_MODE=1` 才生成并返回验证码。否则既不生成也不保存,接口返回 **503** `{"detail": "...", "error": "sms_not_configured"}`,App 会把 detail 显示出来。重置密码时,无论账号存不存在都返回同样的 503,不泄露账号是否存在。密码登录、Apple 登录不受影响 | `DEMO_MODE`,代码默认 `0`。只认 `1/true/yes/on` | [单测] `test_demo_mode.py`(5 条);[本机实跑] `DEMO_MODE=0` 时 `phone/start` 返回 503 + `sms_not_configured` |
| 2 | **越权(IDOR)**:preferences / price_watch / repurchase / group_buy、`/auth/me`、`/auth/migrate` 直接信任客户端传的 `user_id`。账号 id(`phone:<手机号>` 等)可以枚举 | `app/security.py` 提供统一依赖 `get_caller` → `Caller.authorize(user_id, route=...)`。**账号 id** 必须带 `Authorization: Bearer <JWT>`,且 `sub == user_id`。**匿名设备 id**(IDFV / UUID)不需要 token,照常放行。`/auth/migrate` 要求目标账号是本人;来源如果也是账号,同样要求是本人,防止把别人账号的数据"迁"走 | `AUTH_ENFORCE_MODE=off\|report\|enforce`,默认 **report**:放行,但打结构化 WARNING 并计数。enforce:缺 token 或 token 无效返回 401,token 属于别人返回 403 | [单测] `test_idor_enforcement.py`:13 个接口 × report/enforce × {无 token / 垃圾 token / 别人的 token / 本人 / 匿名 UUID},共 34 条;[本机实跑] enforce 下 `GET /preferences`:无 token 401、别人 token 403、本人 200、匿名 200 |
| 2b | **拼单详情泄露成员 id**:`GET /groupbuy/{id}` 不需要身份,原样返回成员 `user_id`(手机号账号、匿名设备 id;拿到设备 id 就能冒充) | 真实成员的 `user_id` 换成 `u_<sha256 前 10 位>`,模拟成员不变 | 无,始终生效 | [单测] `test_groupbuy_detail_masks_member_ids` |
| 3 | **没有限流**:6 位验证码可以暴力猜;能拿短信轰炸别人;`/chat/stream` 可以被刷,产生 LLM 费用 | `app/services/ratelimit.py`:进程内滑动窗口日志。线程安全,每个桶一个独立 LRU 限制内存,多个桶要么一起记账、要么都不记;被拒绝的请求不新建 key(防"灌 key 把受害账号的计数挤出 LRU"绕过每账号限额)。`/auth/register` 另有每 IP 桶(防枚举账号 + PBKDF2 刷 CPU)。auth 接口在路由里检查,`/chat/stream` 由纯 ASGI 中间件检查(先只读预检 IP 桶,已超限就不读请求体直接 429;否则读出请求体取 `user_id`,再原样回放,不影响 SSE)。超限返回 **429** + `Retry-After` + JSON body。body 的值全部是字符串,老版本 iOS 也能解码 | 见 §2 | [单测] `test_ratelimit.py`(21 条):算法、LRU 淘汰、线程并发计数、IP 取值规则、各接口限额、Retry-After、请求体回放、总开关;`test_security_hardening.py`:被拒请求不建 key / 每桶独立 LRU、超限 IP 不读请求体、注册限流;[本机实跑] 同一手机号第 4 次发码返回 429 / `retry-after: 600`;连续 11 次错误密码,第 11 次返回 429 |
| 3b | **伪造 IP 绕过限流**:直连 8000 端口时,攻击者可以自带 `CF-Connecting-IP` / `X-Forwarded-For` 头 | 两层,见下方"生产上客户端 IP 实际怎么来的"。app 层:只有对端是本机才采用 `CF-Connecting-IP`,其它情况一律用对端 IP;CF 头不是合法 IP 时退回对端 IP | 无(**不要**设 `FORWARDED_ALLOW_IPS=*`) | [单测] `test_client_ip_keying`、`test_direct_clients_cannot_dodge_by_spoofing_cf_header`、`test_tunnel_clients_are_keyed_by_cf_connecting_ip`;`test_security_hardening.py::test_prod_*` 用 uvicorn 0.30.6 自带的 `ProxyHeadersMiddleware` 包住 app,模拟生产代理链;[本机实跑] 真实 uvicorn(Python 3.10)下,本机对端 + 不同 XFF 分别计数,伪造 XFF 前缀无效 |
| 3c | **冒用账号 id 锁死别人的聊天额度** | 聊天的"每用户"桶按身份分别计数:匿名 UUID 猜不到,直接按 `user_id` 计;账号 id 只有带了有效 JWT(且 sub 一致)才按账号计,否则按 `账号@IP` 计 | 无 | [单测] `test_chat_spoofed_account_id_cannot_lock_out_victim` |
| 4 | **JWT 默认密钥**:没设 `LIONPICK_JWT_SECRET` 时会退回源码里公开的默认值,任何人都能伪造 token,强制校验形同虚设 | `jwt_session.using_default_secret()`。当 `AUTH_ENFORCE_MODE=enforce` 且仍在用默认密钥时,**所有** session JWT 一律视为无效(fail closed),包括 `/auth/delete`、`/auth/verify`;启动时打 ERROR。report/off 模式维持旧行为,只告警 | `LIONPICK_JWT_SECRET`(放在 VM 的 `jwt.conf` drop-in 里,**绝不进仓库**) | [单测] `test_enforce_with_default_secret_rejects_even_valid_tokens`、`test_enforce_with_real_secret_accepts_owner` |

### 生产上客户端 IP 实际怎么来的

uvicorn 默认开着 `proxy_headers`,`FORWARDED_ALLOW_IPS` 默认是 `127.0.0.1`(VM 的 ExecStart 没改这两个)。所以对端是 `127.0.0.1` 的请求,在进入 app **之前**,uvicorn 就已经用 `X-Forwarded-For` 的**最右一项**改写了 `scope["client"]`:

- **隧道流量**:Cloudflare 把真实客户端 IP 追加在 XFF 最右,cloudflared 原样转发,于是 app 看到的对端就是真实 IP(不是 127.0.0.1)。客户端自带的 XFF 只会出现在左边,伪造无效。app 层的 `CF-Connecting-IP` 分支只在 uvicorn 没改写时兜底,比如请求不带 XFF,或者 cloudflared 经 `::1` 连进来(`::1` 不在 uvicorn 的信任列表里)。两条路径得到的都是真实 IP。
- **直连 8000 端口**:对端是攻击者自己的 IP,uvicorn 不信任它带的 XFF,app 也不信任它带的 CF 头,于是按对端 IP 计数。
- **不要设 `FORWARDED_ALLOW_IPS=*`**(或 `--forwarded-allow-ips '*'`):8000 端口对公网开着时,这样任何人都能用 XFF 伪造 IP。
- cloudflared 用的是 `http://localhost:8000`。如果部署侧把 uvicorn 改成只监听 `127.0.0.1`,`localhost` 先解析到 `::1` 会连不上,Go 会回退到 `127.0.0.1`,上面的逻辑不变。如果改成监听 `::`/`::1`,对端会是 `::1`,走 app 层的 CF 分支,同样正确(`test_prod_ipv6_loopback_tunnel_uses_cf_header`)。

### "账号 id" 的判定

以下 id 命名空间是在 `user_store.py` 里逐一核对过的:`apple:<sub>`、`phone:<手机号>`、`pw:<邮箱|手机号>`、`wechat:demo`(演示用,所有人共享)。

判定规则:**含 `:` 或 `@` 的一律当账号处理**,以后新增的命名空间也会被自动覆盖。其余的当匿名设备 id。iOS 的 IDFV 只含十六进制字符和 `-`,不会被误判成账号。

---

## 2. 限流额度(每进程)

| 桶 | 作用于 | 默认 | 环境变量 |
|---|---|---|---|
| `sms_target` | `/auth/phone/start` 按手机号;`/auth/password/reset/start` 按账号。两个接口共用一个桶 | 3 / 10 分钟 | `RL_SMS_PER_TARGET` |
| `sms_ip` | 同上,按 IP | 10 / 10 分钟 | `RL_SMS_PER_IP` |
| `login_ip` | `/auth/phone/verify`、`/auth/password/login`、`/auth/password/reset/verify`、`/auth/password/change`,按 IP | 20 / 10 分钟 | `RL_LOGIN_PER_IP` |
| `login_account` | 同上,按账号 | 10 / 10 分钟 | `RL_LOGIN_PER_ACCOUNT` |
| `register_ip` | `/auth/register`,按 IP(单独计数,不占登录额度) | 10 / 10 分钟 | `RL_REGISTER_PER_IP` |
| `chat_ip` | `POST /chat/stream`,按 IP | 30 / 分钟 | `RL_CHAT_PER_IP` |
| `chat_user` | `POST /chat/stream`,按 user_id(规则见 3c) | 30 / 分钟 | `RL_CHAT_PER_USER` |

- 格式是 `次数/窗口`,窗口可以写 `600`、`600s`、`10m`、`1h`。写错时退回默认值,不会因此把限流关掉。
- 总开关:`RATE_LIMIT_ENABLED=0`,紧急回滚用。LRU 上限 `RL_MAX_KEYS`,**每个桶**默认 50000;配错时退回默认值,不会让进程起不来。
- 被拒绝的请求不新建 key,桶之间的 LRU 互相独立。要把某个账号的计数挤出 LRU,攻击者得在窗口内让同一个桶放行 5 万个新 key,而这些请求本身受每 IP 额度限制,需要成千上万个 IP。
- **这些额度是"每进程"的。** 线上目前只有一个 uvicorn worker,所以等价于全局额度。将来开多个 worker 或多台机器时,实际额度会变成 N 倍,到时需要换成 Redis 这类共享存储。
- 被拒绝的请求不计入窗口,攻击者不会因为一直重试而被越锁越久。
- **压测 / 预热脚本会被限流**:`tools/stress_test.py`、`tools/stress_e2e.py` 从同一 IP 每分钟发几十上百次 `/chat/stream`,超过 30 次/分钟就会收到 429。压测时临时设 `RATE_LIMIT_ENABLED=0`,或调大 `RL_CHAT_PER_IP` / `RL_CHAT_PER_USER`。`tools/demo_prewarm.sh`(14 条)和 `tools/warm-demo.py`(13 条)在额度以内。
- **共享出口 IP**:答辩现场或校园网很多设备共用一个出口 IP 时,共享 `login_ip` 20 次/10 分钟、`chat_ip` 30 次/分钟的额度。需要的话现场临时调大。
- 已知取舍:按账号限流时,攻击者可以把某个手机号或账号的发码/登录额度用完,让本人暂时(10 分钟)无法发码或登录。业界做短信和登录限流都有这个代价;IP 桶限制了单个攻击者能造成的影响。

---

## 3. 运维接口

`GET /auth/enforcement-stats`:返回越权校验计数、限流计数,以及当前安全开关状态。开关状态全部以布尔值或枚举值给出,不含任何密钥。

**只回答本机请求**:对端是本机、并且没有 `CF-Connecting-IP` / `X-Forwarded-For` / `CF-Ray` 头,才算本机请求,其它请求一律返回 404。生产上隧道流量的对端已经被 uvicorn 改写成真实 IP(见 §1),直接不满足"对端是本机";即使没被改写(经 `::1` 连进来,或请求不带 XFF),Cloudflare 边缘也一定会写入 `CF-Connecting-IP`,本机 curl 不带。

```bash
# 在 VM 上
curl -s http://127.0.0.1:8000/auth/enforcement-stats | python3 -m json.tool
journalctl -u lionpick | grep -E 'security_posture|auth_enforce_violation'
```

启动时会打一行:`security_posture {"auth_enforce_mode": ..., "demo_mode": ..., "jwt_secret_is_default": ..., "rate_limit_enabled": ..., "tokens_trustworthy": ...}`。

启动时如果 `DEMO_MODE=1`,还会多打一行 WARNING,说明此时越权校验保护不了 `phone:` / `pw:` 账号(见 §5)。

违规日志的格式是 `auth_enforce_violation {"mode","route","reason","ip","action","ns","uid_sha"}`。日志里**不写明文手机号或邮箱**,只记命名空间和哈希前缀。

---

## 4. 上线顺序(建议)

1. **合并后什么都不配**:`AUTH_ENFORCE_MODE` 默认 report,不会拦任何请求;限流生效;`DEMO_MODE` 默认 0,所以**短信验证码登录和找回密码会返回 503**(autodeploy 约 2 分钟内上线,不会回滚,因为 `/ready` 不受影响)。如果现在还要演示短信登录,先由部署侧加一个 drop-in,显式设置 `DEMO_MODE=1`(是否这样做由 owner 决定;这个 drop-in 不在本次改动里)。注意:`DEMO_MODE=1` 等于把验证码公开,见 §5 第二条。
2. 确认 `jwt.conf` 已设置真实的 `LIONPICK_JWT_SECRET`。看启动日志,应为 `jwt_secret_is_default: false`。
3. 重新打包 iOS IPA。新客户端会在已登录状态下带上 JWT。
4. 在 report 模式下观察几天 `/auth/enforcement-stats` 的 `violations`。旧客户端、JWT 过期(默认 7 天)、R11 之前登录的账号(本地没存 jwt)都会表现为 `missing_token` 或 `invalid_token`。
5. 违规数降到可以接受后,切换到 `AUTH_ENFORCE_MODE=enforce`。

---

## 5. 还没解决的问题

- **限流是每进程的**,详见 §2。
- **没有真实短信网关**:`DEMO_MODE=0` 时短信登录和找回密码直接不可用;`DEMO_MODE=1` 时验证码又会回到响应里。真正的修法是接入短信服务商,或者走 `USER_STORE_BACKEND=cloud`。
- **`DEMO_MODE=1` 时越权校验对 `phone:` / `pw:` 账号无效**:知道手机号的人可以 `phone/start` 拿到 `dev_code`,再 `phone/verify` 拿到这个 `phone:` 账号的合法 JWT。知道邮箱或手机号的人可以 `password/reset/start` 拿到重置码,改掉 `pw:` 账号的密码。enforce 只校验"JWT 的 sub == user_id",管不了这条路;每号码 3 次/10 分钟的限流只能放慢,挡不住。所以生产上开 `DEMO_MODE=1` 之前,owner 要清楚这一点;启动日志会打 WARNING 提醒。
- **空的 `LIONPICK_JWT_SECRET`**:现在按"没设"处理,退回默认密钥,于是会被检测到并触发告警和 enforce 失效保护。以前会用空密钥签名,同样可以伪造,却躲过了检测。
- **Apple 登录没有验签**(`user_store._decode_apple_sub` 不校验 Apple 的签名,是已知的演示缺口):任何人都能伪造一个 `apple:<sub>` 身份,再拿到对应的合法 JWT。越权校验管不了这种情况。需要按 Apple JWKS 校验 aud/iss/exp。
- **`wechat:demo` 是所有人共享的演示账号**,谁都能登录,上面的数据实际上是公开的。
- **JWT 没有刷新和吊销机制**:有效期 7 天,过期后客户端没有自动续期。enforce 模式下用户需要重新登录;iOS 目前没有"401 → 提示重新登录"的处理。
- **`/chat/stream` 本身没做越权校验**:chat 会按 `req.user_id` 读取偏好权重来重排结果,别人能通过它间接影响或推测偏好。这不在本次范围(chat.py 归其它 track),只做了限流。
- **CORS / TLS**:目前由 Cloudflare 隧道负责 TLS,后端 CORS 是 `*`,而且不带凭据(认证走 Authorization 头,不用 cookie)。**8000 端口目前公网也能直接访问**,要在防火墙关掉(部署 track 负责)。端口关掉之前,直连流量按对端 IP 限流,伪造 CF 头没有用。
- 拼单 id 的生成依赖秒级时间戳:同一人同一秒对同一商品开两次团会撞主键,返回 500(`group_buy_db._gen_group_id`,早就存在的问题,不在本次范围)。
- 没有 CAPTCHA,也没有设备指纹。
- `/auth/register` 仍然会回答"账号已存在",可以用来枚举账号;现在只受每 IP 10 次/10 分钟限制。

---

## 6. 本次实际跑过什么

- `.venv/bin/python -m pytest server/tests -q -p no:cacheprovider` → **446 passed, 14 skipped**(基线 370 passed / 14 skipped;首轮实现新增 60 条,复审新增 16 条)。开发 venv 是 fastapi 0.115.0 / starlette 0.38.6 / pydantic 2.9.2 / uvicorn 0.30.6,与生产锁定的版本一致,Python 3.11。
- **Python 3.10**(复审补跑):用 uv 本地缓存离线建了一个 Python 3.10.20 venv,装的是生产锁定的 fastapi 0.115.0 / pydantic 2.9.2 / uvicorn 0.30.6(starlette 0.38.6)。在里面跑了安全相关的 6 个测试文件(`test_security_hardening` / `test_ratelimit` / `test_idor_enforcement` / `test_demo_mode` / `test_delete_authz` / `test_account_management`),**88 passed**;所有改动过的 .py 都能 `py_compile`,`import app.main` 也成功。全量测试没有在 3.10 下跑,因为缺 RAG 相关依赖。
- 复审本机实跑(Python 3.10 + uvicorn 0.30.6,`127.0.0.1:8766`,`DEMO_MODE=0 AUTH_ENFORCE_MODE=enforce`,本地测试密钥):
  - stats 接口:本机直接请求返回 200,带 XFF+CF 头返回 404;
  - `RL_SMS_PER_IP=2/10m` 下,客户端 A(XFF 203.0.113.9)依次返回 503 / 503 / 429,客户端 B 返回 503;
  - 被拒绝的那次请求没有新建 `sms_target` key;
  - 注册:前 10 次 200,第 11 次 429;换一个 XFF 返回 200;伪造 XFF 前缀(`198.18.0.1, 203.0.113.20`)仍然 429;
  - 无 token 的账号 id 返回 401。
- 本机实跑:`DEMO_MODE=0 AUTH_ENFORCE_MODE=enforce LIONPICK_JWT_SECRET=<本地随机值> uvicorn app.main:app --port 8765`,用 curl 打真实 HTTP:
  - 启动日志里有 `security_posture` 那一行;
  - `/auth/enforcement-stats`:本机直接请求返回 200,带 `CF-Connecting-IP` 返回 404;
  - `phone/start`:前 3 次返回 503 `sms_not_configured`,第 4 次返回 429,带 `retry-after: 600`;
  - 越权:401 / 403 / 200 / 匿名 200,和预期一致;
  - 错误密码登录:前 10 次 400,第 11 次 429。
  - **没有调用 `/chat/stream`**(会触发付费 LLM),chat 中间件只在单测里用假的 endpoint 验证过。
- iOS:xcodegen 生成到临时目录,`xcodebuild` 模拟器构建(`CODE_SIGNING_ALLOWED=NO`)**BUILD SUCCEEDED**。没有在真机或模拟器上运行过。
- **[未验证]** 线上 VM、真实 Cloudflare 隧道下 `X-Forwarded-For` / `CF-Connecting-IP` 的实际取值(上面只用 curl 模拟了这两个头)、Python 3.10 下的全量测试。
