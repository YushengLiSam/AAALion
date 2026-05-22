# feat(client,tools): deploy to iPhone 13 Pro with auto-detected signing + weekly resign

**Date**: 2026-05-22 (late late evening; ~5:26 AM next morning)
**SHA**: (fill in after commit)
**Author**: Shufeng Chen <shufeng.c.dev@gmail.com>

## Why

The remaining gap from Round 2 was getting the app onto Shufeng's physical iPhone 13 Pro. User confirmed via Xcode screenshot that the Personal Team was set up correctly. The screenshot also surfaced a "Expires in 6 days" warning, which is the standard free-tier Apple ID cert expiry — this commit documents it clearly + adds a one-command refresh.

A subtle bug got caught: the 10-char string Xcode shows after the email in a certificate name (e.g. `Apple Development: foo@bar.com (7TQ694CBJV)`) is a **certificate identifier**, NOT the Team identifier. The actual Team ID is in the provisioning profile's `TeamIdentifier` field. Using the cert ID as `DEVELOPMENT_TEAM` fails with `No Account for Team "XXXXXXXXXX"`. The right value for Shufeng's account is `V8KDBHKA3P` (Team), not `7TQ694CBJV` (Cert).

## What changed

- `client/AAALionApp/project.yml`: set `DEVELOPMENT_TEAM: "V8KDBHKA3P"` with an inline explainer comment about Cert ID vs Team ID and how teammates can swap in their own.
- `Makefile`:
  - `ios-device` now auto-detects the iPhone UUID via `xcrun devicectl list devices` and chains the install.
  - New `resign:` target (alias for `ios-device` with a reminder to schedule the next refresh).
  - `make help` updated.
- `docs/IOS_SETUP.md`: new sections "Free-tier signing — what expires when" + "Gotcha: Team ID ≠ Certificate ID" + a fresh verified-working transcript (2026-05-22 05:26).
- `docs/DEPLOY_GUIDE.md`: §4 step 6 explicitly walks the user through finding the *real* Team ID via the .mobileprovision file. Added a callout box for the weekly resign cadence. Troubleshooting table gains two new rows.
- `README.md`: status table updated — physical iPhone deploy is now ✅.
- `~/.config/lionpick/credentials.env` (gitignored, outside repo): `APPLE_TEAM_ID="V8KDBHKA3P"` + `APPLE_CERT_ID="7TQ694CBJV"`.

## Procedure

```bash
# 1. User completed the Xcode GUI step → screenshot showed signing valid + 6-day expiry
# 2. Set DEVELOPMENT_TEAM with the visible 10-char ID (7TQ694CBJV) → xcodebuild failed:
#    error: No Account for Team "7TQ694CBJV"
# 3. Inspected the provisioning profile:
security cms -D -i ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/*.mobileprovision \
  | /usr/libexec/PlistBuddy -c "Print :TeamIdentifier" /dev/stdin
# → V8KDBHKA3P (the real Team ID)
# 4. Updated project.yml DEVELOPMENT_TEAM → V8KDBHKA3P, regenerated .xcodeproj
# 5. xcodebuild -allowProvisioningUpdates build → BUILD SUCCEEDED with code signing
# 6. xcrun devicectl device install app → "App installed: bundleID: com.aaalion.lionpick"
```

## Outcome / Verification

- ✅ `xcrun devicectl device install app` returned success and the app appears on the iPhone (assuming Shufeng has the iPhone home screen visible).
- ✅ Signing identity in keychain: `Apple Development: alexcsf01725@gmail.com (7TQ694CBJV)`.
- ✅ Provisioning profile at `~/Library/Developer/Xcode/UserData/Provisioning Profiles/25c3f191-...mobileprovision`, expires 6 days from issue (2026-05-22).
- ✅ Symlinked to legacy `~/Library/MobileDevice/Provisioning Profiles/` so xcodebuild older code paths find it too.
- ⏳ First-launch trust step on the iPhone (Settings → General → VPN & Device Management → Trust) is a user action.

## Follow-ups

- User: open Settings → General → VPN & Device Management on the iPhone and trust the Apple Development cert, then tap the 狮选 icon.
- Set a recurring calendar reminder: "Run `aaalion resign` every Sunday so the iPhone app keeps working."
- Defense day 2026-06-11: ensure `aaalion resign` was run within the previous 6 days.
