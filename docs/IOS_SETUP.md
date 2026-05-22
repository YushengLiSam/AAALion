# iOS Setup — honest state and what to do

> Audit on 2026-05-22 (Shufeng's MacBook): full Xcode is **NOT installed**, only Command Line Tools at `/Library/Developer/CommandLineTools`. All Swift source files parse cleanly with `swift -frontend -parse`, so the code is well-formed; it just can't be built into an app yet.

## What you have (verified)

- `swift` compiler frontend (from Command Line Tools) — enough to parse and type-check Swift source.
- `xcodegen` installed (`brew install xcodegen` already done).
- `tools/aaalion` global helper that wraps `make` and finds the repo from anywhere.

## What's missing

- **Xcode.app** — required to build any iOS app, run a simulator, or deploy to a physical device. Not present in `/Applications/`.
- **`xcodebuild`, `xcrun xctrace`, `xcrun devicectl`** — these ship with full Xcode.
- **`ios-deploy` / `libimobiledevice`** — no third-party USB deploy tools either.

## Install Xcode (one-time, ~30 minutes)

```bash
# 1) Open the Mac App Store. Search "Xcode". Click Install. (~10 GB.)
#    OR: brew install --cask xcodes && open -a Xcodes  (manages multiple Xcode versions)

# 2) After install:
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
sudo xcodebuild -runFirstLaunch

# 3) Verify:
xcodebuild -version              # should print "Xcode 15.x" or "16.x"
xcrun simctl list devices        # should list iPhone 15, iPhone 13 etc. simulators
```

There is no Apple-supported way to build an iOS app without Xcode. (Yes, some communities maintain `theos` / `cross-compile` setups, but they're brittle and not worth your time for a 20-day sprint.)

## After Xcode is installed — 30 seconds to a running app

```bash
aaalion ios                                      # xcodegen → AAALionApp.xcodeproj
open client/AAALionApp/AAALionApp.xcodeproj      # Xcode opens
# In Xcode: choose iPhone 15 simulator, hit Cmd+R
```

The app should launch into an empty chat. Type "推荐一款适合油皮的洗面奶" → it tries to POST to `http://localhost:8000/chat/stream`. With the backend running (`aaalion backend`), you'll see streamed text + product cards.

## Deploying to your iPhone 13 Pro

Once Xcode is installed:

1. Plug iPhone in via Lightning / USB-C.
2. iPhone may prompt "Trust This Computer?" — tap Trust.
3. In Xcode → top toolbar, click the device picker → select your iPhone 13 Pro (it appears once the device is paired).
4. Xcode → Settings → Accounts → add your Apple ID (any Apple ID works for personal/free deploys; you get 7-day cert renewal).
5. Click the AAALionApp target → Signing & Capabilities → set Team to your personal team (or "None — Manual signing" for ad-hoc).
6. Cmd+R → builds and installs on the phone. First run, iPhone needs you to trust the dev cert under Settings → General → VPN & Device Management.

LAN testing: backend on the MacBook, iPhone on the same Wi-Fi. Find MacBook IP with `ipconfig getifaddr en0`. Set `PUBLIC_BACKEND_URL=http://<ip>:8000` in Xcode → Product → Scheme → Edit Scheme → Run → Arguments → Environment Variables.

## "Claude Code Mobile" honestly

You asked me to set up "Claude Code Mobile" on your iPhone 13 Pro. I want to be honest: **as of my knowledge, there is no Anthropic-published "Claude Code Mobile" product**. What exists:

- **Anthropic's Claude iOS app** (https://claude.ai on App Store) — a chat client to claude.ai, not a coding agent. Useful for "ask Claude a question on your phone" but doesn't run repos, doesn't run tools, doesn't edit files.
- **Claude Code (the CLI)** — runs on macOS/Linux/Windows. There's no iOS version. To use Claude Code from your phone, the standard trick is SSH from the phone into your Mac/Linux server and run Claude Code there:
  - [Blink Shell](https://apps.apple.com/app/blink-shell-mosh-ssh/id1156707581) (paid, $20) — best in class iOS terminal with mosh + ssh.
  - [Termius](https://apps.apple.com/app/termius/id549039908) (free with paid tier) — alternative.
  - On your iPhone, install one of these, set up SSH key auth, ssh into your MacBook (assuming the MacBook is on the same network or Tailscale), launch `claude` from there.

If you've seen an actual "Claude Code Mobile" product launched recently (post-my-knowledge), tell me what it's called and I'll do my best to set it up. Otherwise I'd recommend skipping this and using the Claude iOS app for casual Q&A + Blink Shell for actual coding work.

## "openclaw" — best-guess honest answer

I don't recognize "openclaw" as a tool. My best guess is you mean **OpenCLIP** (https://github.com/mlfoundations/open_clip).

- **For 拍照找货 (photo-to-product, bonus 4.2): YES, OpenCLIP is the right tool.** It's the standard open-source CLIP implementation, has pre-trained Chinese-aware models (`ViT-B-32` + `laion2b_s34b_b79k`, plus `wukong` / `taiyi-clip` variants for Chinese), runs on the A100 in seconds for 100 images, and exposes both image and text encoders so you can index product photos and query with either a user photo or text.
- **For text-only retrieval (the main loop)**: not the right tool. Use a Chinese sentence embedding model (`BAAI/bge-small-zh-v1.5`, which the RAG pipeline already uses) — it's better at Chinese semantics than CLIP's text encoder.
- **Plan**: index product images with OpenCLIP on the A100 once, store the 512-d vectors in Chroma's `products_image` collection. When the user uploads a photo, embed it with OpenCLIP, query the image collection, return the matching products.

If you meant something else (OpenClaw the game? Open Clio? a tool I haven't heard of?), tell me and I'll re-assess. The "be honest" framing tells me you wanted a real signal — so: OpenCLIP is real and useful for one specific track. If "openclaw" is a marketing-flavored thing you saw in a tweet, it's probably hype.
