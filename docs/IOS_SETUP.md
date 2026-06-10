# iOS 环境搭建 — 当前状态与待办事项

> 审计更新于 2026-05-22(Shufeng 的 MacBook):**Xcode 26.5 已安装**,位于 `/Applications/Xcode.app`,许可协议已接受,runFirstLaunch 已完成。iPhone 17 Pro 模拟器的构建 + 安装 + 启动已验证。物理 iPhone 13 Pro 已配对(devicectl 显示其为 `connected`)。Apple ID 已在 Xcode → Settings → Accounts 中登录。**剩余的一次性步骤**:在 Xcode → Signing & Capabilities 中为项目指定 Personal Team(见下文"部署到你的 iPhone 13 Pro")。

## 你已具备的条件(已验证)

- **Xcode 26.5**(Build 17F42)+ iOS 26.5 SDK + 预测式代码补全模型(Predictive Code Completion Model)。
- `swift` / `xcodebuild` / `xcrun simctl` / `xcrun devicectl` 全部可用。
- `xcodegen` 已安装。
- `tools/aaalion` 全局辅助脚本已安装在 `~/.local/bin/aaalion` — 在任意目录下均可使用。
- Apple ID `alexcsf01725@gmail.com` 已登录 Xcode(据用户 2026-05-22 晚间确认)。
- iPhone 13 Pro 已配对(`Shufeng's iPhone`,UUID `7310469E-E396-5197-9408-FF1AD58D4CF2`)。
```

不使用 Xcode 来构建 iOS 应用没有任何 Apple 官方支持的途径。(确实有一些社区维护着 `theos` / `cross-compile` 之类的方案,但它们很脆弱,对一个 20 天的冲刺来说不值得花时间。)

## Xcode 安装完成后 — 30 秒跑起应用

```bash
aaalion ios                                      # xcodegen → AAALionApp.xcodeproj
open client/AAALionApp/AAALionApp.xcodeproj      # Xcode opens
# In Xcode: choose iPhone 15 simulator, hit Cmd+R
```

应用应当启动并进入一个空白聊天界面。输入"推荐一款适合油皮的洗面奶" → 它会尝试向 `http://localhost:8000/chat/stream` 发起 POST 请求。在后端运行的情况下(`aaalion backend`),你会看到流式输出的文本 + 商品卡片。

## 部署到你的 iPhone 13 Pro

**2026-05-22 已验证**:iPhone 13 Pro(`Shufeng's iPhone`,标识符 `7310469E-E396-5197-9408-FF1AD58D4CF2`)已通过 USB 与这台 Mac 配对。`xcrun devicectl list devices` 能找到它且状态为 `connected`。

`xcodebuild ... -destination 'platform=iOS,id=<uuid>'` 这一步构建本身是成功的。**缺的是代码签名**:钥匙串中没有有效的签名身份(`security find-identity -p codesigning -v` → "0 valid identities found")。没有签名,`devicectl install` 会以 "No code signature found" 拒绝该 .app。

**一次性的 Apple ID 配置**(交互式,仅限 GUI 操作):

1. 通过 Lightning / USB-C 连接 iPhone。iPhone 可能会弹出"信任此电脑?"提示 — 点击信任。
2. 打开 Xcode → Settings(`Cmd+,`)→ Accounts → 点击 `+` → Apple ID。使用 `alexcsf01725@gmail.com` 登录。登录后,Xcode 会生成一个与该 Apple ID 关联的 "Personal Team"。
3. 打开 `client/AAALionApp/AAALionApp.xcodeproj`。点击 `AAALionApp` target → Signing & Capabilities → 将 Team 设置为 "Shufeng Chen (Personal Team)"。Xcode 会请求下载开发证书和描述文件(provisioning profile);允许即可。
4. 顶部工具栏 → 设备选择器 → 选择 `Shufeng's iPhone`。按 `Cmd+R`。首次安装会在 iPhone 上触发提示:设置 → 通用 → VPN 与设备管理 → 信任该开发者证书。
5. 此后,Xcode GUI 的 Cmd+R 和 `aaalion ios-device`(命令行构建 + 安装)都能正常工作。

> **xcodegen 注意事项**:在完成第 3 步后重新打开项目时,Xcode 会在项目文件中写入 `DEVELOPMENT_TEAM` 和 `CODE_SIGN_STYLE = Automatic`。如果你之后再次运行 `aaalion ios`(它会从 `project.yml` 重新生成 `.xcodeproj`),这些设置会被抹掉。要么 (a) 把你的 team ID 写进 `project.yml` 并提交,要么 (b) 每次重新生成后重做一遍 GUI 步骤。方案 (a) 更干净 — 只要把 `DEVELOPMENT_TEAM` 加到 `targets.AAALionApp.settings.base`,`xcodegen` 就会读取它。

局域网测试:后端跑在 MacBook 上(`aaalion backend`),iPhone 连同一个 Wi-Fi。用 `ipconfig getifaddr en0` 查 MacBook 的 IP。在 Xcode → Product → Scheme → Edit Scheme → Run → Arguments → Environment Variables 中设置 `PUBLIC_BACKEND_URL=http://<ip>:8000`。

## 免费档签名 — 什么时候过期

Personal Team(免费 Apple Developer 账号)签发的**开发证书和描述文件在签发后约 7 天过期**。过期后,安装在 iPhone 上的 IPA 将无法启动。

本项目的应对方案:

| 策略 | 适用场景 | 操作方法 |
|---|---|---|
| **每周重签**(推荐) | 开发期间 + 答辩 | 每周(或任何演示前)跑一次 `aaalion resign`。重签 + 重装在 60 秒内完成。 |
| **付费 $99/yr Apple Developer Program** | 想要 1 年期证书 | 需要实名 DUNS + 几个工作日审批;对一个大学生比赛来说不值得。 |
| **用模拟器演示** | 如果 iPhone 答辩环节确定不用真机 | 无需签名 — `aaalion ios-sim` 永远可用。 |

设两个日历提醒即可覆盖:`next Sunday` 和 `the morning of 2026-06-11`(答辩日)。

## 坑:Team ID ≠ 证书 ID

Xcode 在证书名称后括号里显示的 10 位字符串(例如 `Apple Development: foo@bar.com (XXXXXXXXXX)`)是**证书标识符**,而不是 Team ID。对 Personal Team 来说,这两者通常是不同的。

要找到**真正的** Team ID 填入 `DEVELOPMENT_TEAM`:

```bash
# After you've done the Xcode GUI Signing step at least once:
ls ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/   # find the .mobileprovision
security cms -D -i <profile.mobileprovision> | grep -A1 TeamIdentifier
# The first <string>...</string> is your Team ID.
```

对这台 Mac 上的 Shufeng(2026-05-22):证书 ID `7TQ694CBJV`,Team ID `V8KDBHKA3P`。Project.yml 使用的是 Team ID。

## 已验证可用(2026-05-22 05:26)

```
$ xcodebuild -project AAALionApp.xcodeproj -scheme AAALionApp \
    -destination 'platform=iOS,id=7310469E-...' -allowProvisioningUpdates build
    ** BUILD SUCCEEDED **
$ xcrun devicectl device install app --device 7310469E-... \
    /tmp/lionpick-derived-device/Build/Products/Debug-iphoneos/狮选.app
    App installed: bundleID: com.aaalion.lionpick ✓
```

应用**已部署到物理 iPhone 13 Pro**。首次启动需要先信任证书:iPhone → 设置 → 通用 → VPN 与设备管理 → Apple Development: alexcsf01725@gmail.com → 信任。之后,应用图标(`狮选`)即可正常启动并连接局域网后端。

## 已验证可用(2026-05-22 03:50)

```
$ aaalion ios                            # generated AAALionApp.xcodeproj
$ xcodebuild -project AAALionApp.xcodeproj -scheme AAALionApp \
    -destination 'platform=iOS Simulator,name=iPhone 17 Pro' \
    -derivedDataPath /tmp/lionpick-derived build
    ** BUILD SUCCEEDED **

$ xcrun simctl boot "iPhone 17 Pro"
$ xcrun simctl install booted /tmp/lionpick-derived/.../狮选.app
$ xcrun simctl launch booted com.aaalion.lionpick
com.aaalion.lionpick: 65243                # app launched

$ open -a Simulator                        # → chat UI rendered correctly
```

已端到端验证:模拟器中运行的应用截图 + 后端(`/health` 200,`/chat/stream` SSE 流式输出 deltas + 商品卡片)。

## 关于 "Claude Code Mobile" 的诚实说明

你让我在你的 iPhone 13 Pro 上安装 "Claude Code Mobile"。我想坦诚地说:**就我所知,Anthropic 没有发布过名为 "Claude Code Mobile" 的产品**。实际存在的是:

- **Anthropic 的 Claude iOS 应用**(App Store 上的 https://claude.ai)— 一个连接 claude.ai 的聊天客户端,不是编码 agent。适合"在手机上问 Claude 一个问题",但不能跑仓库、不能跑工具、不能编辑文件。
- **Claude Code(命令行工具)** — 运行在 macOS/Linux/Windows 上。没有 iOS 版本。要在手机上使用 Claude Code,常规做法是从手机 SSH 到你的 Mac/Linux 服务器,在那边运行 Claude Code:
  - [Blink Shell](https://apps.apple.com/app/blink-shell-mosh-ssh/id1156707581)(付费,$20)— iOS 上最好的终端,支持 mosh + ssh。
  - [Termius](https://apps.apple.com/app/termius/id549039908)(免费,有付费档)— 备选方案。
  - 在你的 iPhone 上安装其中一个,配好 SSH 密钥认证,ssh 到你的 MacBook(假设 MacBook 在同一网络或 Tailscale 上),在那边启动 `claude`。

如果你最近看到了一个真实发布的 "Claude Code Mobile" 产品(在我的知识截止之后),告诉我它的名字,我会尽力帮你装上。否则我建议跳过这一项,日常问答用 Claude iOS 应用 + 真正的编码工作用 Blink Shell。

## "openclaw" — 尽我所知的诚实回答

我不认识 "openclaw" 这个工具。我的最佳猜测是你指的是 **OpenCLIP**(https://github.com/mlfoundations/open_clip)。

- **对于拍照找货(photo-to-product,加分项 4.2):是的,OpenCLIP 是正确的工具。** 它是标准的开源 CLIP 实现,有预训练的中文感知模型(`ViT-B-32` + `laion2b_s34b_b79k`,以及面向中文的 `wukong` / `taiyi-clip` 变体),在 A100 上处理 100 张图片只需几秒,并且同时暴露图像和文本编码器,因此你可以对商品图片建索引,然后用用户照片或文本任意一种方式查询。
- **对于纯文本检索(主流程)**:不是正确的工具。应使用中文句向量模型(`BAAI/bge-small-zh-v1.5`,RAG 管线已经在用)— 它在中文语义上优于 CLIP 的文本编码器。
- **方案**:在 A100 上用 OpenCLIP 对商品图片一次性建索引,把 512 维向量存入 Chroma 的 `products_image` 集合。当用户上传照片时,用 OpenCLIP 对其编码,查询图像集合,返回匹配的商品。

如果你指的是别的东西(游戏 OpenClaw?Open Clio?某个我没听说过的工具?),告诉我,我会重新评估。"要诚实"这个措辞告诉我你想要一个真实的信号 — 所以:OpenCLIP 是真实存在的,并且对一条特定赛道有用。如果 "openclaw" 是你在某条推文里看到的营销味十足的东西,那它大概率是炒作。
