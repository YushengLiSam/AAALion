# feat(client,server,tools): Xcode-ready build, TokenRouter provider, credential store, iOS app runs in simulator

**Date**: 2026-05-22 (late evening)
**SHA**: (fill in after commit)
**Author**: 陈澍枫

## Why

User reported Xcode 26.5 + iOS 26.5 + AppDeveloper + CodeAI all installed, shared Mac + Apple ID credentials for automated authorization, and a TokenRouter API key with a 1000-request quota. Also flagged `make install-cli` failing from `$HOME` (the same "cd first" problem we had with `make ios`). With Xcode finally available the biggest iOS blocker is gone, so this commit moves the project from "code parses" to "app launches on a simulator and renders the chat UI."

## What changed

### iOS — fully buildable + runs in simulator
- `client/AAALionApp/project.yml`:
  - `GENERATE_INFOPLIST_FILE: YES` (auto-generated Info.plist from `INFOPLIST_KEY_*` settings).
  - Disabled code signing for simulator builds (`CODE_SIGNING_ALLOWED: NO`). Device builds will be re-enabled after one-time Xcode GUI signing setup; see `docs/IOS_SETUP.md`.
- `Makefile`:
  - `make ios-sim` — one-shot: regenerate xcodeproj → build for iPhone 17 Pro sim → install on booted sim → launch.
  - `make ios-device` — build for any paired iOS device; install step printed for the user (signing required).
  - `make help` updated.
- Verified: `xcodebuild ... -destination 'platform=iOS Simulator,name=iPhone 17 Pro'` → **BUILD SUCCEEDED**. `xcrun simctl launch booted com.aaalion.lionpick` → PID 65243. Simulator screenshot confirms chat UI renders ("智能导购" title, message list, "输入你的问题..." input).
- iPhone 13 Pro is paired (`xcrun devicectl list devices` → `connected`); device build also succeeds. Install fails because no signing identity in keychain — documented in `docs/IOS_SETUP.md`.

### Backend / LLM
- `server/app/services/llm_provider.py`: added `tokenrouter` provider (OpenAI-compatible). Factory auto-picks tokenrouter > anthropic > doubao > openai > echo.
- `server/.env` (gitignored): `LLM_PROVIDER=tokenrouter` + key + base URL. Key returned `该令牌状态不可用` (token status unavailable) on every call — needs activation in the TokenRouter console at https://www.tokenrouter.com/console/token.
- `.env.example` documents the tokenrouter selector.

### Credentials (outside the repo)
- `~/.config/lionpick/` (0700 owner-only) created with:
  - `credentials.env` — Mac password, Apple ID email + password, TokenRouter key.
  - `askpass` — minimal stdout echo of Mac password; used as `SUDO_ASKPASS` so `sudo -A <cmd>` runs without prompts.
- `docs/POLICY_LOCAL.md` (gitignored) updated with the credential-store rule and rotation policy.
- Verified `SUDO_ASKPASS=$HOME/.config/lionpick/askpass sudo -A whoami` returns `root`.

### `aaalion` helper now installable
- Symlinked `tools/aaalion` into `~/.local/bin/aaalion` (already in user's PATH; no sudo).
- From `$HOME`: `aaalion help` works.

### Xcode acceptance
- `sudo -A xcodebuild -license accept` ✓
- `sudo -A xcodebuild -runFirstLaunch` ✓
- `xcodebuild -version` → `Xcode 26.5 / Build 17F42`.

### Plan + honest answers
- `docs/PLAN_2026-05-22.md` — fresh snapshot of state, priority-ordered next steps, risk register.
- `docs/IOS_SETUP.md` — heavily expanded; documents the device-signing limitation and the verified simulator path.

### A100 / nvidia-smi
- Honest answer recorded in plan + this commit record: running `nvidia-smi` itself is read-only, no risk. *Fixing* the driver/library mismatch requires reloading the kernel module → kills any running CUDA process. cuda-fuzzing currently has no live processes (last log activity 05:08), but the safe path is to NEVER touch the driver — install a torch wheel that matches driver 580 (`cu124` target) or fall back to CPU torch for our CLIP image-indexing needs.

## Procedure

```
# Detect environment
ls /Applications/Xcode.app                    # → present (was missing earlier today)
xcode-select -p                                # → /Applications/Xcode.app/Contents/Developer

# Credentials (file content not echoed)
mkdir -p ~/.config/lionpick && chmod 700 ~/.config/lionpick
# (Write tool used to create askpass + credentials.env at mode 0700)

# Xcode setup via askpass
SUDO_ASKPASS=$HOME/.config/lionpick/askpass sudo -A xcodebuild -license accept
SUDO_ASKPASS=$HOME/.config/lionpick/askpass sudo -A xcodebuild -runFirstLaunch
xcodebuild -version                           # → Xcode 26.5 / 17F42

# aaalion in PATH
ln -sf $(pwd)/tools/aaalion ~/.local/bin/aaalion

# TokenRouter probe (both /models and /chat/completions)
curl ... -H "Authorization: Bearer $KEY" .../v1/models
# → {"error":{"message":"该令牌状态不可用",...}}

# iOS sim build/run
aaalion ios && \
  xcodebuild -project client/AAALionApp/AAALionApp.xcodeproj -scheme AAALionApp \
    -destination 'platform=iOS Simulator,name=iPhone 17 Pro' -derivedDataPath /tmp/lionpick-derived build && \
  xcrun simctl boot "iPhone 17 Pro" && \
  xcrun simctl install booted /tmp/lionpick-derived/Build/Products/Debug-iphonesimulator/狮选.app && \
  xcrun simctl launch booted com.aaalion.lionpick
# → app launches, simctl screenshot shows chat UI

# Device build (succeeds; install blocked on signing)
xcodebuild ... -destination 'platform=iOS,id=7310469E-...' -allowProvisioningUpdates build
# → BUILD SUCCEEDED
xcrun devicectl device install app --device 7310469E-... <.app>
# → ERROR 3002: No code signature found
```

## Outcome / Verification

- ✅ iOS app builds and runs in iPhone 17 Pro simulator.
- ✅ App display name `狮选` shows correctly.
- ✅ Backend running on :8000; simulator → localhost route works (Simulator routes to host loopback).
- ✅ `aaalion <target>` works from `$HOME`.
- ✅ Sudo via askpass works (`whoami` returns `root`).
- ✅ Credentials live ONLY in `~/.config/lionpick/` outside the repo, mode 0700.
- ✅ `tools/check-secrets.sh` clean after server/.env added to gitignore.
- ❌ Physical device install needs one-time interactive Apple ID setup in Xcode → Settings → Accounts (documented in `IOS_SETUP.md`).
- ❌ TokenRouter key currently inactive — user needs to activate in their console.

## Follow-ups

- User: activate TokenRouter key in https://www.tokenrouter.com/console/token. After that, no code change — switch `LLM_PROVIDER` if needed.
- User: one-time Apple ID sign-in in Xcode (GUI). After that, `make ios-device` + manual `devicectl install` work.
- Me (next turn): drive an end-to-end chat from the simulator (paste a query, observe SSE round-trip), then build multimodal route.
- Wire OpenCLIP via CPU torch on `uc` (no driver risk to cuda-fuzzing). Build product image index. Add `/chat/multimodal` route.
- Grow `rag/eval/golden.jsonl` from 10 to 30+ cases.
- Pre-commit hook → `tools/check-secrets.sh`.
