# 部署指南 — 在你的 Mac + iPhone(≥13)上运行 狮选 LionPick

> 适用读者:**李雨晟 (Sam) 和 管图杰 (Tujie)**。你有自己的 MacBook + 一台 iPhone 13 或更新机型。本指南带你从 `git clone` 一路走到"应用在你的 iPhone 上运行并返回真实 LLM 回复",全程约 45 分钟,其中大部分时间花在 Xcode + Python 的下载上。

> 另见:[`IOS_SETUP.md`](IOS_SETUP.md)(iOS 深度配置)、[`HARDWARE.md`](HARDWARE.md)(设备 + A100)、[`docs/demos/2026-05-22/`](demos/2026-05-22/)(应用跑起来的样子)。

## 0. 前置条件

| 需要什么 | 为什么 | 怎么做 |
|---|---|---|
| **macOS 14+**(Sonoma 或更高) | Xcode 26 需要 | 用 `sw_vers` 检查 |
| **Xcode 26.5**(约 10 GB) | 构建 iOS 应用、部署到真机 | Mac App Store → "Xcode" → 安装 |
| **Apple ID** | 登录 Xcode 以免费部署到真机 | 用你自己的 |
| **Homebrew** | 安装 `xcodegen` | https://brew.sh |
| **Python 3.12** | 后端 + RAG | `brew install python@3.12` |
| **一台 iPhone ≥13** | 真机演示 | 你已经有了 |
| **一根 USB 数据线** | 将 iPhone 与 Mac 配对以使用 `devicectl` | Lightning 或 USB-C,看你的 iPhone 用哪种 |
| **一个 TokenRouter API key** | LLM 调用(Doubao 仍待定) | 在 https://www.tokenrouter.com/console/token 获取。激活可能需要邮箱验证。**或者**用 `LLM_PROVIDER=echo` 做纯 UI 开发。 |

## 1. 克隆 + 工具链(5 分钟)

```bash
git clone https://github.com/YushengLiSam/AAALion-.git
cd AAALion-

# xcodegen for regenerating the .xcodeproj from project.yml
brew install xcodegen

# Install the `aaalion` global helper so you can run `aaalion ios-sim` from anywhere
ln -sf "$(pwd)/tools/aaalion" "$HOME/.local/bin/aaalion"   # if $HOME/.local/bin is in PATH
# OR:
make install-cli                                            # symlinks into /usr/local/bin (needs sudo)

# Verify
aaalion help
```

## 2. 后端 + RAG(10 分钟,含模型下载)

```bash
# Python venv
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
# (sentence-transformers will download ~30 MB of model weights on first ingest)

# Local config
cp .env.example server/.env
# Edit server/.env: set TOKENROUTER_API_KEY to your activated key.
# Optionally change TOKENROUTER_MODEL (default claude-haiku-4-5; alternatives in the TR console).

# Ingest the seed data into Chroma (one time, ~90 sec on Apple Silicon)
aaalion ingest
# Expected: chunks: 992 | upserted; collection now has 992 docs

# Smoke-test. Startup first warms retrieval models and one real query path.
aaalion backend &
curl -s http://127.0.0.1:8000/health
# {"status":"ok","version":"0.1.0"}
until curl -fsS http://127.0.0.1:8000/ready; do sleep 1; done
# {"status":"ready","retrieval":{...,"reranker":"ready","query_path":"ready"}}

curl -sN -X POST http://127.0.0.1:8000/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"推荐适合油皮的洗面奶"}]}' | head -5
# Expect data: {"type":"delta",...} stream
```

如果你只想做 UI 开发、不产生 LLM 费用:`LLM_PROVIDER=echo aaalion backend`。后端会返回一个确定性的 `[echo]` 流,完整走一遍 SSE 路径。

在 Windows 上,或想要一个隔离、可复现的后端时,请改用 Docker:

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

首次 Docker 构建会把两个检索模型的权重都缓存进镜像;之后的容器启动
只需加载并预热它们,然后才开始接受聊天请求。ingest 步骤会把 Chroma
文本索引持久化到 `data/.chroma/` 下;商品目录数据变更后请再跑一次。
在无 key 冒烟部署之后,若要启用真实的 TokenRouter 生成回答,请使用
[`README.md`](../README.md#docker-deployment-on-windows-copy-and-run)
中的安全换 key 代码块。

## 3. iOS 模拟器(5 分钟)

```bash
# Open Xcode once interactively to accept the license + run first-launch (one time)
sudo xcodebuild -license accept
sudo xcodebuild -runFirstLaunch

# Build + run on the iPhone 17 Pro simulator
aaalion ios-sim
# This regenerates the .xcodeproj from project.yml, builds, installs into the booted sim, launches.
open -a Simulator
```

你应该能看到应用启动并渲染出聊天界面。输入一个查询,点击发送,即可看到流式输出 + 商品卡片(前提是后端已启动)。

## 4. iOS 真机(首次约 10 分钟)

这一部分需要**一步交互式的 Apple ID 操作**。

```bash
# Plug iPhone in via USB. iPhone may prompt "Trust This Computer?" → Trust.
# Verify devicectl sees it:
xcrun devicectl list devices
# Look for your iPhone in the "Available Devices" list. Note the 36-char UUID.
```

在 Xcode 中(一次性设置):

1. Xcode → Settings(`Cmd+,`)→ **Accounts** → 点击 `+` → **Apple ID** → 登录。
2. 运行 `aaalion ios` 重新生成 `.xcodeproj`,然后 `open client/AAALionApp/AAALionApp.xcodeproj`。
3. 在左侧边栏点击 **AAALionApp** target → **Signing & Capabilities** 标签页。
4. 把 **Team** 设为 **"<your name> (Personal Team)"**。
5. 让 Xcode 自动生成证书和描述文件(5-10 秒;黄色警告会消失)。
6. **找到你真正的 Team ID**(Xcode 在证书名里、邮箱之后显示的 10 位字符串是 CERT ID,不是 Team ID):
   ```bash
   ls ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/   # find the new .mobileprovision
   security cms -D -i <profile.mobileprovision> | grep -A1 TeamIdentifier
   # → the <string>...</string> is your 10-char Team ID
   ```
7. 编辑 `client/AAALionApp/project.yml` 中的 `settings.base.DEVELOPMENT_TEAM`:
   ```yaml
   DEVELOPMENT_TEAM: "ABC123DEF4"   # your 10-char team ID from step 6
   ```
   这样就把它固化进配置,之后 `aaalion ios` 重新生成时都会保留。如果你不想把自己的 Team ID 推到远端,请把这处改动只留在本地;它不算机密,但具有身份标识性。

现在你可以通过 CLI 构建并安装:

```bash
aaalion ios-device
# At the end, the Makefile prints the exact `xcrun devicectl device install app …` command — run it.
# OR run by hand:
xcrun devicectl device install app --device <YOUR_DEVICE_UUID> \
  /tmp/lionpick-derived-device/Build/Products/Debug-iphoneos/狮选.app
```

首次安装后在 iPhone 上:点 **设置 → 通用 → VPN 与设备管理 → Apple Development: <your-apple-id>@... → 信任**。之后每次 `aaalion ios-device` 都不会再弹提示。

> ⚠️ **免费 Apple ID 证书 7 天过期。** 每周(或任何演示之前)运行一次 `aaalion resign` 刷新。建议设个日历提醒。免费档没有任何绕过办法;$99/年的 Apple Developer Program 可以拿到 1 年期证书,但对这次比赛不值得。

把 iPhone 连到你 Mac 的局域网后端。应用默认指向
`http://localhost:8000`(在模拟器上开箱即用)。真机 iPhone 则需要
你 Mac 的局域网 IP。**三种方式,按干净程度排序:**

```bash
ipconfig getifaddr en0   # your Mac's LAN IP, e.g. 192.168.1.42
```

1. **应用内 Settings 设置页(日常推荐):**在 iPhone 上打开应用,
   点右上角 ⚙,粘贴 `http://192.168.1.42:8000`,点
   **Test Connection**,再点 **Save**。持久化在 `UserDefaults` 中,
   应用重启后仍然生效,无需重新构建。我们期望每位开发者都用这种方式。
2. **Xcode scheme 环境变量(一次性测试):**Xcode → Product → Scheme →
   Edit Scheme → Run → Arguments → Environment Variables,添加
   `PUBLIC_BACKEND_URL=http://192.168.1.42:8000`。只在从 Xcode 运行时
   生效。
3. **改 `Config.swift`(不推荐):**修改 `defaultBackendURL`
   会和其他开发者的提交冲突。不要把这个改动推上去。

仓库默认值(`localhost`)是有意为之 —— 这意味着新克隆下来的仓库在
任何人的模拟器上零配置即可运行。

## 5. 常见错误与修复

| 症状 | 原因 | 修复 |
|---|---|---|
| `make: *** No rule to make target 'ios'` | 你不在仓库目录里 | 用 `aaalion ios`(在任何目录都能用)。或者先 `cd AAALion-`。 |
| `Signing for "AAALionApp" requires a development team` | `DEVELOPMENT_TEAM` 为空 | 按上文 §4 第 3-6 步操作。 |
| `xcrun devicectl ... No code signature found` | 构建时没有签名 | 确认 project.yml 里有 `CODE_SIGN_STYLE: Automatic`(默认就有)。 |
| `No Account for Team "XXXXXXXXXX"` | `DEVELOPMENT_TEAM` 填的是证书 ID,不是 Team ID | 按 §4 第 6 步的坑点解法找到真正的 Team ID,把那个填进 project.yml |
| 应用昨天还能启动,今天打不开 | 7 天 Personal Team 证书过期 | `aaalion resign` |
| `/chat/stream` 返回 `Internal Server Error` | `server/.env` 没有被加载 | 确认 `server/.env` 存在且设置了 `LLM_PROVIDER`。重启 `aaalion backend`。 |
| iOS 显示了用户消息但没有回复 | SSE 解析器挂起 | 已在 commit `6e8d7b9` 修复。如果在旧分支上看到此问题,请拉取 main。 |
| TokenRouter 返回 `该令牌状态不可用` | Key 未激活 | 在 https://www.tokenrouter.com/console/token 激活。 |
| 首次 ingest 很慢 | `sentence-transformers` 正在下载模型权重(约 30 MB) | 仅此一次。后续 ingest 会复用 `~/.cache/torch/sentence_transformers/` 下缓存的模型。 |

## 6. 跑通之后可以试什么

运行 [`docs/demos/2026-05-22/README.md`](demos/2026-05-22/README.md) 中的 6 个脚本化演示,确认与参考结果一致。如果你的截图看起来不一样,发出来 —— 可能是我们修了什么,也可能是出现了回归。

之后你就可以开始开发自己负责的部分了。开发 SOP 见 [`PIPELINE.md`](PIPELINE.md)。

---

## 7. 通过 Cloudflare Tunnel 暴露公网后端(R8.D,任何非局域网测试都推荐)

当 iPhone 在另一个 Wi-Fi 上、走蜂窝网络,或在任何基于局域网的发现会失效的场地做测试时,可通过 Cloudflare Tunnel 把 Mac 的 `localhost:8000` 暴露到一个公网 HTTPS URL 上。**免费**,快速隧道无需账号,支持 HTTPS。

### 一次性安装

```bash
brew install cloudflared
```

### 每次会话

```bash
tools/start-tunnel.sh
# → Tunnel URL:  https://reader-missile-absolute-memphis.trycloudflare.com
```

脚本会把 URL 捕获到 `/tmp/cloudflared.log` 并打印出来。URL 每次重启都会变化。

### 固化进应用

1. 打开 `client/AAALionApp/AAALionApp/Config.swift`。
2. 把 `defaultBackendURL` 替换为捕获到的隧道 URL。
3. `aaalion ios-device` 重新构建并重装到 iPhone(Xcode 通过 `-allowProvisioningUpdates` 自动续期证书)。
4. 在 iPhone 上强制退出并重新打开 狮选。**无需再点 Settings。**

### 开发者模式覆盖(进阶)

在聊天界面长按**齿轮图标** 1.5 秒。会弹出提示"开发者模式已开启 / Dev mode ON",图标填充为琥珀色。此时 Settings 中会出现 Backend URL 编辑器 —— 测试期间在隧道 / 局域网 / 云端之间切换时用它。再次长按即可隐藏。

### 命名隧道获得稳定 URL

对于需要稳定 URL 的较长测试窗口(例如持续多天的答辩彩排),一次性注册 Cloudflare 账号 + 创建命名隧道:

```bash
cloudflared tunnel login                    # one-time browser auth
cloudflared tunnel create lionpick          # creates a long-lived tunnel
cloudflared tunnel route dns lionpick api.<your-domain>
cloudflared tunnel run lionpick
```

URL 变为 `https://api.<your-domain>`,且在 `cloudflared` 重启之间保持稳定。

---

## 8. 云端 VM(Phase 2,答辩日稳健方案,≈ ¥35/月)

Cloudflare Tunnel 仍然要求 Mac 处于运行状态。答辩当天在现场(笔记本电量、Wi-Fi 切换、免人值守演示都很关键),应把后端部署到真正的云端 VM,这样 Mac 就无关紧要了。

### 推荐:Hetzner CX22

- 4 vCPU、8 GB 内存、80 GB SSD = 跑 Chroma + BGE-zh + bge-reranker-base(常驻约 2 GB)绰绰有余。
- €4.51/月(≈ ¥35)—— 价格/规格比最佳。
- 区域:新加坡到中国大陆延迟最低,法兰克福作备选。

### 已评估的替代方案

| 供应商 | 套餐 | 规格 | 结论 |
|---|---|---|---|
| Hetzner CX22 | 共享型 | 4 vCPU / 8 GB / 80 GB | **推荐** —— 价格最优 |
| DigitalOcean | 基础 droplet $6 | 1 vCPU / 1 GB / 25 GB | 内存吃紧;reranker 可能 OOM |
| AWS Lightsail | $5 | 1 vCPU / 0.5 GB | 太小 |
| Vultr | $6 | 1 vCPU / 1 GB | 与 DO 相同 |
| Fly.io | 免费 | 3 × 256 MB | 对我们的模型占用来说太小 |
| Render | 免费 | 共享,闲置 15 分钟后休眠 | 冷启动 30 秒 = 演示观感差 |

### 部署步骤(计划于 2026-06-05 → 06-08)

1. 开通 Hetzner CX22(新加坡)。
2. 把现有的 `server/Dockerfile` 镜像推送到 `ghcr.io`。
3. 在 VM 上 `docker compose up -d`,`.env`(权限 0600)中放入 TokenRouter key。
4. 通过 Cloudflare 配置 DNS:`api.lionpick.<domain>` → VM 公网 IP。
5. 把 `Config.swift::defaultBackendURL` 更新为 VM 的域名。
6. 重新构建 iPhone 应用。不再需要 Mac。
7. UptimeRobot 免费监控(每 5 分钟检查)→ 宕机时 Slack 告警。

Phase 2 的最新进展见 `docs/PROPOSAL_2026-05-25.md` Tier 2 backlog。
