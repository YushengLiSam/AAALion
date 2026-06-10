# 故障排查(Troubleshooting)

> 这里记录了我们在构建狮选 LionPick 过程中踩到的每一个坑,以及对应的精确修复方法。可按类别快速浏览。如果你遇到了这里没有的问题,请补充进来(并提交)。

---

## iPhone 真机

### 点击狮选 App 图标时弹出「未受信任的开发者(Untrusted Developer)」提示

**症状**: 安装 App 后(通过 `aaalion ios-device` 或 Xcode),点击狮选图标时显示:

> Your device management settings do not allow using apps from developer "Apple Development: alexcsf01725@gmail.com (7TQ694CBJV)" on this iPhone. You can allow using these apps in Settings.

**原因**: iOS 要求首次使用时**信任**该开发者证书。这是每次证书签发后只需做一次的操作(由于免费档证书每周续签,所以相当于每周一次)。

**修复**(5 次点击):

1. 在弹窗上点 **取消(Cancel)**。
2. 打开 **设置(Settings)**。
3. **通用(General)** → **VPN 与设备管理(VPN & Device Management)**。
4. 在 **开发者 App(DEVELOPER APP)** 下,点你的开发证书(例如 **"Apple Development: alexcsf01725@gmail.com"**)。
5. 点 **信任 "..."(Trust)** → 确认 **信任**。

现在狮选 App 图标可以正常启动了。

### 昨天还能启动的 App,今天打不开了

**原因**: 免费档 Personal Team 证书在签发约 7 天后过期。

**修复**: 在 Mac 上执行:
```bash
aaalion resign
```
然后在 iPhone 上重新点狮选图标。(如果又出现 "Untrusted Developer",重复上面的信任步骤 — Apple 每次重签都会签发一张*新*证书,每张新证书都需要信任一次。)

日历提醒: 每周日 + 任何 demo / 答辩日的早上。

### 无法部署: `aaalion ios-device` 报 "No iPhone connected"

**修复顺序**:

1. 用 Lightning / USB-C 数据线把 iPhone 直接插到 Mac 上(不要经过集线器)。
2. iPhone 弹出「信任此电脑?(Trust This Computer?)」→ 点 **信任**,输入密码。
3. 在 Mac 上执行 `xcrun devicectl list devices`。iPhone 应出现在 "Available Devices" 列表中,状态为 `connected`。如果没有,换一根线或换一个 USB 口。
4. 重试 `aaalion ios-device`。

### iPhone App 聊天页空白 / 没有 AI 回复

两种可能原因:

1. **后端没在运行**或不可达 — 见下文「"无法连接服务器" / "Cannot connect to server"」。
2. **过期的 SSE 解析器**(旧分支)。修复已在 commit `6e8d7b9` 落地。`git pull origin main` 后重新 `aaalion ios-device`。

### iPhone 上出现"无法连接服务器" / "Cannot connect to server" 横幅

这是局域网(LAN)demo 中**最大的一个坑**。有两件相互独立的事必须同时正确:

**发生原因**(任意一条都足以让连接失败):

1. 后端启动时绑定到了 `127.0.0.1`(仅回环地址)。包括 iPhone 在内的局域网客户端都无法访问。
2. `Config.swift` 用的是 `http://localhost:8000` — 在 iPhone 上这指的是 *iPhone 自己*,而不是 Mac。
3. iPhone 和 Mac 不在同一个 Wi-Fi 网络。
4. Mac 防火墙拦截了入站 TCP :8000。

**修复顺序**:

```bash
# 1. Backend on 0.0.0.0 (all interfaces), not 127.0.0.1
pkill -f "uvicorn app.main"
source .venv/bin/activate
cd server && nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/lionpick_server.log 2>&1 &

# 2. Find your Mac's LAN IP
ipconfig getifaddr en0     # → e.g. 10.76.138.67

# 3. Self-test: curl that IP from the Mac itself (catches firewall problems)
curl http://10.76.138.67:8000/health     # → {"status":"ok",…}
until curl -fsS http://10.76.138.67:8000/ready; do sleep 1; done
# /ready becomes 200 after retrieval startup prewarm; chat returns 503 before then.

# 4. Point the iPhone app at that IP — DON'T edit Config.swift
#    Open the app, tap ⚙ (top-right) → Settings → URL field → type
#    http://<MAC-LAN-IP>:8000 → Test Connection → Save.
#    UserDefaults persists across launches; no rebuild needed.

# 5. (If app isn't installed yet) build + install
aaalion ios-device

# 6. Open 狮选 on iPhone, do step 4, query something. Should now work.
```

**Mac 防火墙**: 开发用的 Mac 上通常是关闭的(`/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate`)。如果开着,放行 Python:
```bash
SUDO_ASKPASS=$HOME/.config/lionpick/askpass sudo -A /usr/libexec/ApplicationFirewall/socketfilterfw --add /opt/homebrew/bin/python3.12
SUDO_ASKPASS=$HOME/.config/lionpick/askpass sudo -A /usr/libexec/ApplicationFirewall/socketfilterfw --unblock /opt/homebrew/bin/python3.12
```

**为什么不用 Info.plist**: 我最初试过 `INFOPLIST_KEY_LionPickBackendURL` — Xcode 会静默丢弃自定义(非 Apple)的 `INFOPLIST_KEY_*` 构建设置,所以这个值永远到不了 Info.plist。硬编码进 Config.swift 的做法更丑,但确实能用。

**更好的长期方案**: 增加一个设置页面,让用户输入/扫码后端 URL 一次,然后持久化到 `UserDefaults`。已记录在 `docs/FUTURE_WORK.md`。

**必须同一 Wi-Fi**: iPhone 和 Mac 必须在同一网络。手机的「个人热点」或访客网络可能存在隔离。检查两者的 Wi-Fi 设置里显示的是同一个网络名。

### 同一 Wi-Fi 下仍然"无法连接服务器" → 改用 Cloudflare Tunnel(R8.D)

如果上面的修复顺序仍解决不了(例如路由器开了 AP / 客户端隔离、酒店 / 咖啡馆网络、iPhone 悄悄回落到了蜂窝网络),就彻底跳过局域网,通过 Cloudflare Tunnel 把后端放到一个公网 URL 上。任何网络都能用,无需局域网配置,免费。

**一次性设置**:
```bash
brew install cloudflared
```

**每次会话**(在 uvicorn 运行期间执行):
```bash
tools/start-tunnel.sh
# → captures the URL like https://reader-missile-absolute-memphis.trycloudflare.com
```

**固化进 iOS App**:
1. 打开 `client/AAALionApp/AAALionApp/Config.swift`。
2. 把 `defaultBackendURL` 替换为捕获到的 tunnel URL。
3. 执行 `aaalion ios-device` 重新构建 + 重新安装(Xcode 自动续签证书)。
4. 在 iPhone 上强制退出并重新打开狮选。**不需要点设置** — URL 直接生效。

**开发模式覆盖**(高级用法): 在聊天页长按齿轮图标 1.5 秒。会出现一个 toast 提示,设置里重新出现 Backend-URL 编辑框。仅在测试期间需要在 tunnel / 局域网 / 云端之间切换时使用。

**注意事项**:
- tunnel URL 在每次 `cloudflared` 重启后都会变。要么重新固化进 App,要么配置一个命名的 Cloudflare Tunnel(先 `cloudflared tunnel login` 一次 → `cloudflared tunnel create lionpick` → 把它 DNS 路由到一个固定子域名)。
- 答辩日我们会迁移到真正的云 VM(Hetzner CX22 €4.5/mo),这样就不再依赖这台 Mac。见 `docs/DEPLOY_GUIDE.md §Cloud VM (Phase 2)`。

---

## 语音 + 附件交互(R8.E)

### 麦克风一直"红着"不停; 草稿框带上了上一轮的旧文本

**原因**: `SFSpeechRecognizer` 不会在静音时自动释放。在 R8.E 之前,识别器会一直存活,直到用户再次手动点麦克风。存活期间,最后一条部分识别结果(可能来自上一轮、从未最终定稿)一直留在 `draft` 里。

**修复**(R8.E,位于 `SpeechService.swift`): 基于部分结果节奏的空闲计时器,阈值 1.8 秒:
- 每条新的部分结果都会把计时器往后推。
- 1.8 秒没有新的部分结果 → 自动 `stop()`。麦克风释放,草稿保留最终转写文本,用户检查后点发送。
- 与 ChatGPT / Claude iOS App 的交互一致。录音时输入框会显示"正在听… / Listening — auto-stops on silence"提示。

**如需调整**(例如针对语速很慢的用户): 修改 `SpeechService.swift` 中的 `idleTimeout`。不要低于 1.0 秒 — 那会切断中文自然的短语停顿。

### 只能附加一张照片

**原因**: R8.E 之前,`ChatViewModel.pendingImage: Data?` 是单个 Optional,每个选择器都会把它覆盖掉。后端早已通过 `content: list[ContentPart]` 支持多图; 瓶颈只在 iOS 数据模型。

**修复**(R8.E): `pendingImage` → `pendingAttachments: [Attachment]`,并设 `Attachment.maxCount = 10`。输入框显示一排可横向滚动的 64×64 缩略图,带 `x` 删除按钮 + "N / 10" 计数器。所有选择器都受 `remainingAttachmentSlots` 约束:
- PhotosPicker 改为复数形式(`[PhotosPickerItem]`,`maxSelectionCount: remaining`)。
- FileImporter 设为 `allowsMultipleSelection: true`。
- 相机每拍一张追加一张; 可反复拍直到达到上限。

消息气泡把附件渲染为文本上方的 2 行 LazyVGrid(每行 5 个,96×96)— 与 ChatGPT 同样的模式。

**传输层**: 每张图变成一个 `image_url` part,MIME 类型(JPEG / PNG / HEIC / PDF)通过魔数字节嗅探得出。后端 `_extract_image_bytes_list` 返回该列表; 缓存键使用 `hash_image_bytes_list`(排序后的 SHA 拼接,上限 10 个),因此与顺序无关且键长度有界。

**CLIP 检索器**只看 attachments[0](单图视觉检索器); LLM 仍通过 content 数组看到全部 N 张图。已在 `server/app/routes/chat.py::_extract_image_bytes_list` 的 docstring 中记录。

---

## Xcode / 签名

### `error: No Account for Team "XXXXXXXXXX"`

**原因**: Xcode 在证书名里你的 Apple ID 邮箱后面显示的那串 10 位字符(例如 `Apple Development: foo@bar.com (XXXXXXXXXX)`)是**证书标识符**,而不是团队标识符。对 Personal Team(免费账号)来说两者并不相同。

**修复**: 从描述文件(provisioning profile)里找出真正的 Team ID:

```bash
ls ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/*.mobileprovision
security cms -D -i <that-file>.mobileprovision \
  | /usr/libexec/PlistBuddy -c "Print :TeamIdentifier" /dev/stdin
# → prints the real Team ID
```

然后把 `client/AAALionApp/project.yml` 中的 `DEVELOPMENT_TEAM` 设为该值(不是证书 ID)。

参考: Shufeng 的证书 ID 是 `7TQ694CBJV`,Team ID 是 `V8KDBHKA3P`。

### `Signing for "AAALionApp" requires a development team`

**原因**: `project.yml` 里的 `DEVELOPMENT_TEAM` 为空。

**修复**: 要么在 `project.yml` 中设置(推荐 — 能在 `aaalion ios` 重新生成工程时保留),要么在 Xcode GUI 中该 target 的 Signing & Capabilities 标签页里设置。如果走 GUI 路线,记得把值同步写回 `project.yml`,以免后续重新生成时被抹掉。

### `xcrun devicectl ... No code signature found`

**原因**: 构建时没有做代码签名(仅模拟器用的 project.yml 设置被误用于真机构建)。

**修复**: 确认 `client/AAALionApp/project.yml` 中有 `CODE_SIGN_STYLE: Automatic` 且设置了 `DEVELOPMENT_TEAM`。重新运行 `aaalion ios && aaalion ios-device`。

### 在 `$HOME` 下运行时报 `make: *** No rule to make target 'ios'`

**原因**: `make` 依赖相对路径; 你是在仓库目录之外运行的。

**修复**:
```bash
# One-time install of the global helper:
ln -sf "<path-to-repo>/tools/aaalion" "$HOME/.local/bin/aaalion"
# Then from anywhere:
aaalion ios
aaalion backend
aaalion ios-device
```

### Xcode GUI 里显示了团队,但 `xcodebuild` CLI 报 "No Account for Team"

**原因**: `xcodegen` 重新生成 `.xcodeproj` 之后,Xcode IDE 可能还留着过期的内部缓存。

**修复**: 关闭项目并在 Xcode 中重新打开(`Cmd+Q` 然后 `open client/AAALionApp/AAALionApp.xcodeproj`)。然后带 `-allowProvisioningUpdates` 重新运行 `xcodebuild`。若仍失败,用上面「从描述文件取 Team ID」的方法。

---

## 后端 / RAG

### `/chat/stream` 返回 `[echo]` 文本而不是真实的 LLM 输出

**原因**: `server/.env` 没有被读取,导致 `LLM_PROVIDER` 未设置 → 工厂方法回落到 `EchoProvider`。

**修复**: 确认 `server/.env` 存在(它被 gitignore 了 — 模板见 `.env.example`)。`server/app/config.py` 已经会从 `server/.env` 加载(commit `6e8d7b9` 之后); 确保你在 `main` 或更新的提交上。

测试:
```bash
source .venv/bin/activate
cd server && python -c "from app.config import settings; print(settings.doubao_api_key, settings.qdrant_url)"
# should print non-empty values
```

### `/chat/stream` 返回 `Internal Server Error`

查看 `tail -30 /tmp/lionpick_server.log`(或你的 uvicorn 输出落在的位置)。最常见的有:

- `ModuleNotFoundError: No module named 'rag'` → `rag_client.py` 里 `parents[]` 差一(已在 `6e8d7b9` 修复 — 拉取最新代码)。
- `Could not resolve authentication method` → LLM 提供方鉴权为空。在 `server/.env` 中提供一个密钥(`TOKENROUTER_API_KEY` 或 `ANTHROPIC_API_KEY`),或设置 `LLM_PROVIDER=echo`。

### TokenRouter 返回 `该令牌状态不可用`("token status unavailable")

**原因**: TokenRouter 密钥已创建但尚未激活。

**修复**: 打开 https://www.tokenrouter.com/console/token — 通常有一个「激活」按钮或邮箱验证步骤。激活后立刻调用即可成功。(我们当前的密钥 `sk-mfU7…` 于 2026-05-22 晚间激活。)

### Doubao PDF API 密钥返回 401

**原因**: 比赛 PDF 里附带的密钥被人公开泄露到了 GitHub 上,Apple^H^H^H主办方把它停用了。新密钥待发放。

**修复**: 在拿到新密钥之前,使用 `LLM_PROVIDER=tokenrouter`(`.env.example` 中的当前默认值)。

### Chroma telemetry 警告刷屏日志

```
Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given
```

无害但很吵。**修复**: 在 `server/.env` 中设置 `CHROMA_TELEMETRY=False`(如果你是从 `.env.example` 复制的,默认已设置)。

### `pip install` 在构建 `pydantic-core` wheel 时失败

**原因**: Python 3.14 目前还没有 `pydantic-core` 的预编译 wheel — `pip` 会尝试从源码编译,需要 Rust。

**修复**: 使用 Python 3.12:
```bash
brew install python@3.12
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
```

### `aaalion ingest` 首次运行非常慢

**原因**: `sentence-transformers` 首次使用时会下载 `BAAI/bge-small-zh-v1.5` 模型(~30 MB)。后续运行会复用 `~/.cache/torch/sentence_transformers/` 的缓存。

**修复**: 等这一次就好。正常网络下约 30 秒。

---

## A100 / `uc`

### `nvidia-smi` 报 "Driver/library version mismatch"

**原因**: 系统驱动更新了但内核模块没有重新加载(或反过来)。

**修复**: **不要动系统驱动。** 修复这个不匹配需要重新加载内核模块,会杀掉所有正在运行的 CUDA 进程 — 而共享的 `~/shufeng/cuda-fuzzing/` 项目可能正在跑任务。

**变通方案**: 在我们的 venv 里安装与内核驱动匹配的 torch wheel:
```bash
ssh uc
cd ~/shufeng/AAALion-
source .venv/bin/activate
pip install torch==2.4.1+cu124 --index-url https://download.pytorch.org/whl/cu124
# OR fall back to CPU torch — fast enough for 100 product images:
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### 不要碰 `~/shufeng/cuda-fuzzing/`

那是另一个活跃项目。`uc` 上的每条命令都应只针对 `~/shufeng/AAALion-/` 下的路径。如果你真要做目录级复制,从 MacBook `rsync` 时务必加 `--exclude=cuda-fuzzing`(我们不这么做 — 我们用定向 rsync)。

---

## 仓库 / git

### 提交被归到 `shufengc@local` 而不是 GitHub 登录名下

**原因**: 旧会话用了占位邮箱。

**修复**: 历史已重写为 `Shufeng Chen <shufeng.c.dev@gmail.com>` 并已强制推送(commit 链起自 `5bad8a2`)。今后请设置:
```bash
git config user.email "<your-github-email>"
git config user.name "<your-name>"
```
或者按单条命令使用 `git -c user.email=... -c user.name=... commit ...`。

### `tools/check-secrets.sh` 标记了我提交里的内容

**原因**: 扫描器在被跟踪的文件里发现了形如 ARK/Anthropic/OpenAI 密钥的字符串。

**修复**: 删除或脱敏该密钥(用 `<REDACTED>` 或明确的占位符)。真实密钥放在 `server/.env`(已 gitignore)或 `~/.config/lionpick/credentials.env`(在仓库之外)。重新运行 `tools/check-secrets.sh` 直到结果干净。

### 向共享远端强制推送

2026-05-22 为重写作者身份做过一次。未经团队认可不要再做 — 队友的本地 refs 会因此分叉。

---

## 如何往本页添加内容

当你踩到新坑时:

1. 在对应类别下添加一个 `### Symptom`(症状)小节。
2. 写明**原因**(1 行)、**修复**(具体命令)。
3. 用 `docs(troubleshooting): add <symptom>` 提交。
4. 如果这个坑严重到会影响队友,在微信群里发一行提示。

以下交叉引用已对特定主题做了深入覆盖:

- iOS 签名与真机部署: [`IOS_SETUP.md`](IOS_SETUP.md), [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)
- 后端 / LLM / 多提供方: [`API.md`](API.md), [`server/README.md`](../server/README.md)
- 架构决策的坦诚问答: [`HONEST_ANSWERS.md`](HONEST_ANSWERS.md)
- 重要提交说明: [`commits/`](commits/)
