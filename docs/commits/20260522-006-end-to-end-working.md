# fix(client,server) + feat(client): end-to-end working on iPhone 17 Pro simulator with real LLM

**Date**: 2026-05-22 (late evening)
**SHAs**: `6e8d7b9` (fix), `c28b935` (feat)
**Author**: Shufeng Chen <shufeng.c.dev@gmail.com>

## Why

After the previous commit ran TokenRouter against an old key (`该令牌状态不可用`), the user provided a fresh, activated key. Two bugs surfaced when running the full pipeline end-to-end against it:

1. **Server didn't load `server/.env`** — `config.py` was looking at the repo root only. So even after dropping the new key into `server/.env`, the running uvicorn still saw no `TOKENROUTER_API_KEY` and fell through to the echo provider.
2. **iOS SSE parser hung on the empty-line boundary** — my parser was structured to accumulate `data:` lines into a buffer and decode on an empty line. `URLSession.bytes(_:).lines` on iOS 17/18 doesn't actually yield the empty lines between SSE events, so the boundary never fired and no events were emitted to the UI.

Both confirmed by instrumented logs (`print("[ChatService] line #N: …")`), then fixed.

Also: with Apple ID now in Xcode, switched `project.yml` from "skip signing" to `CODE_SIGN_STYLE=Automatic`. Device build still needs the user to assign their Personal Team in Xcode GUI once (documented in `docs/IOS_SETUP.md`).

## What changed

### server/app/config.py
- Load `server/.env` (where the user's local key lives) + repo-root `.env` (legacy/convenience).
- Both via `dotenv.load_dotenv`.

### client/AAALionApp/AAALionApp/Services/ChatService.swift
- Removed the "buffer until blank line then decode" parser.
- Now decodes each `data: <json>\n` line directly with `JSONDecoder`.
- Robust to either SSE delimiter convention.

### client/AAALionApp/AAALionApp/ViewModels/ChatViewModel.swift
- Added `runScriptedQueryIfAny()` — reads `-test-query <text>` from `ProcessInfo.processInfo.arguments` and auto-sends. Used to drive the simulator from CLI.

### client/AAALionApp/AAALionApp/Views/ChatView.swift
- Added a red banner that surfaces `viewModel.errorMessage` so any SSE/HTTP error is visible.
- Calls `viewModel.runScriptedQueryIfAny()` in `.task {}`.

### client/AAALionApp/project.yml
- Removed `CODE_SIGNING_ALLOWED: NO` etc.
- Added `CODE_SIGN_STYLE: Automatic`.
- Simulator builds skip signing implicitly; device builds need a Team ID baked in or set in Xcode.

### docs/IOS_SETUP.md
- Refreshed "what you have" section — Xcode 26.5 installed, all CLI tools working, iPhone 13 Pro paired.
- Tightened the "remaining device-signing step" to a numbered ~60-second checklist with the `DEVELOPMENT_TEAM` bake-in trick.

## Procedure

```
# 1. Update credentials with new TokenRouter key
# (Write tool: ~/.config/lionpick/credentials.env + server/.env; permissions 0700)

# 2. Test new key
curl https://api.tokenrouter.com/v1/models -H "Authorization: Bearer sk-mfU7..."
# → 75 models, claude-haiku-4-5 + many others available
curl https://api.tokenrouter.com/v1/chat/completions ...
# → real Chinese answer

# 3. Restart server
pkill -f uvicorn; cd server && uvicorn app.main:app --port 8000 &
curl /chat/stream → 200 OK but content: [echo] (env not loaded)

# 4. Diagnose: server/.env wasn't being read
# → Fix config.py to point at server/.env

# 5. Restart, retry
curl /chat/stream → real LLM output streamed ✓

# 6. Rebuild iOS app, launch with -test-query
aaalion ios
xcodebuild build -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
xcrun simctl install booted .../狮选.app
xcrun simctl launch booted com.aaalion.lionpick -test-query "我是油皮..."

# 7. UI showed user message but no assistant text. Server logs showed 200 OK.
#    Added print() to ChatService — confirmed 12 lines arrived but
#    all were `data: ...`, no empty separators.
# → Fixed parser to decode per-line.

# 8. Rebuild + relaunch
# → Screenshot shows: user message, AI's structured Chinese
#   recommendation, 3 product cards. End-to-end works.

# 9. Rewrite git author identity for the whole history (11 commits)
git filter-branch --env-filter '<set shufeng.c.dev@gmail.com>' -- main shufeng
git push --force-with-lease origin main shufeng
```

## Outcome / Verification

- ✅ TokenRouter key returns real chat completions across 75 models.
- ✅ `server/.env` is now loaded; `LLM_PROVIDER=tokenrouter` resolves to a working OpenAI-compatible client.
- ✅ iOS app on iPhone 17 Pro simulator renders streaming Chinese reply + product cards. Screenshot at `/tmp/lionpick-sim-fixed.png` (not committed, ephemeral).
- ✅ Backend `INFO: POST /chat/stream HTTP/1.1 200 OK` after every simulator round-trip.
- ✅ Git author is now `Shufeng Chen <shufeng.c.dev@gmail.com>` across the entire 11-commit history.
- ❌ Physical iPhone 13 Pro install still blocked on the "Set Team in Xcode" step (60-second user task; instructions in `docs/IOS_SETUP.md`).

## Follow-ups

- User: open Xcode → AAALionApp target → Signing & Capabilities → set Team to Personal Team. Note the 10-char Team ID. Add to `project.yml` under `targets.AAALionApp.settings.base.DEVELOPMENT_TEAM`.
- Me (next): try OpenCLIP CPU on `uc`, build product-image index, wire `/chat/multimodal`. Then grow eval set.
- Anyone: the 1000 TokenRouter requests is a real budget; favor short test queries during dev to avoid eating it.
