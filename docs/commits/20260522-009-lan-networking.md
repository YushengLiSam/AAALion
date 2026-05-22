# fix(client,server): iPhone-to-Mac LAN networking — bind 0.0.0.0 + bake LAN URL

**Date**: 2026-05-22 (still going, ~5:35 AM next morning)
**SHA**: (fill in after commit)
**Author**: Shufeng Chen <shufeng.c.dev@gmail.com>

## Why

User installed the app on the iPhone, trusted the dev cert, opened the app, typed a query → got "无法连接服务器" banner. Two independent bugs:

1. **Backend**: uvicorn was started with `--host 127.0.0.1`, so only loopback could reach it. The iPhone (a different host on the LAN) couldn't.
2. **Client**: `Config.swift` defaulted to `http://localhost:8000`. On a physical iPhone, `localhost` resolves to the iPhone itself — there's no server there.

Either alone would have broken the demo; both did.

## What changed

### server-side: bind to all interfaces
- Manual: `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- `0.0.0.0` accepts connections on every interface (loopback AND LAN).
- Tested: `curl http://10.76.138.67:8000/health` from the Mac returns 200 (proves the LAN IP is reachable).

### client-side: hardcoded LAN URL in Config.swift
- First attempt: add `INFOPLIST_KEY_LionPickBackendURL` to project.yml → Xcode silently drops custom `INFOPLIST_KEY_*` build settings (only standard Apple ones synthesize). Verified via `plutil -p .app/Info.plist` after build — no LionPickBackendURL key present.
- Second attempt (shipped): hardcode `defaultBackendURL = "http://10.76.138.67:8000"` in `Config.swift` with a clear "change me before each LAN session" comment. Resolution order: `PUBLIC_BACKEND_URL` env var (Xcode debug-launch only) > `defaultBackendURL`.
- Long-term better: a Settings screen with `UserDefaults` persistence. Added to FUTURE_WORK.

### docs/TROUBLESHOOTING.md
- New section "无法连接服务器 / Cannot connect to server" with:
  - 4 independent root causes (bound to 127.0.0.1, localhost-on-iPhone, different Wi-Fi, firewall).
  - 6-step fix sequence.
  - Note about why Info.plist key didn't work.
  - Firewall allowlist command with `SUDO_ASKPASS`.

## Procedure

```bash
# 1. Restart backend on 0.0.0.0
pkill -f "uvicorn app.main"
source .venv/bin/activate
cd server && nohup python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > /tmp/lionpick_server.log 2>&1 &

# 2. Confirm LAN reachability from the Mac itself
ipconfig getifaddr en0          # → 10.76.138.67
curl http://10.76.138.67:8000/health
# → {"status":"ok","version":"0.1.0"} ✓

# 3. Update Config.swift defaultBackendURL = "http://10.76.138.67:8000"

# 4. Rebuild + reinstall on iPhone
cd client/AAALionApp && xcodegen
xcodebuild -project AAALionApp.xcodeproj -scheme AAALionApp \
  -destination 'platform=iOS,id=7310469E-E396-5197-9408-FF1AD58D4CF2' \
  -allowProvisioningUpdates \
  -derivedDataPath /tmp/lionpick-derived-device build
xcrun devicectl device install app --device 7310469E-... \
  /tmp/lionpick-derived-device/Build/Products/Debug-iphoneos/狮选.app
# → App installed: bundleID: com.aaalion.lionpick ✓
```

## Outcome / Verification

- ✅ Backend listening on `0.0.0.0:8000`; both loopback and LAN-IP curl tests return 200.
- ✅ App rebuilt with `defaultBackendURL = "http://10.76.138.67:8000"` (the Mac's current LAN IP).
- ✅ Reinstalled on iPhone 13 Pro.
- ⏳ User to confirm on iPhone: open 狮选 → type a query → see streaming response.

## Follow-ups

- **Settings screen with UserDefaults**: user can change backend URL without rebuilding. Right move for the defense.
- **Calendar reminder**: before each demo, run `ipconfig getifaddr en0` and update `Config.swift` if the Mac's LAN IP changed (it does, on different Wi-Fi networks).
- **Add a `make set-backend-url IP=...`** target to automate the Config.swift edit.

The TROUBLESHOOTING entry is the durable artifact — anyone else hitting this will find the fix in <30 seconds.
