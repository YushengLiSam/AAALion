# Deploy Guide — Run 狮选 LionPick on your Mac + iPhone (≥13)

> Audience: **李雨晟 (Sam) and 管图杰 (Tujie)**. You have your own MacBook + an iPhone 13 or newer. This guide takes you from `git clone` to "app running on your iPhone with real LLM responses" in ~45 min, most of which is Xcode + Python downloads.

> See also: [`IOS_SETUP.md`](IOS_SETUP.md) (deep iOS setup), [`HARDWARE.md`](HARDWARE.md) (devices + A100), [`docs/demos/2026-05-22/`](demos/2026-05-22/) (what the running app looks like).

## 0. Prerequisites

| What | Why | How |
|---|---|---|
| **macOS 14+** (Sonoma or later) | Needed for Xcode 26 | check `sw_vers` |
| **Xcode 26.5** (~10 GB) | Build the iOS app, deploy to device | Mac App Store → "Xcode" → Install |
| **Apple ID** | Sign in to Xcode for free device deploy | use your own |
| **Homebrew** | install `xcodegen` | https://brew.sh |
| **Python 3.12** | Backend + RAG | `brew install python@3.12` |
| **An iPhone ≥13** | Real-device demo | already yours |
| **A USB cable** | Pair iPhone with Mac for `devicectl` | Lightning or USB-C, whichever your iPhone uses |
| **A TokenRouter API key** | LLM calls (Doubao is still TBD) | Get one at https://www.tokenrouter.com/console/token. Activation may require email verify. **Or** use `LLM_PROVIDER=echo` for UI-only dev. |

## 1. Clone + tooling (5 min)

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

## 2. Backend + RAG (10 min including model download)

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

If you only want UI dev without LLM cost: `LLM_PROVIDER=echo aaalion backend`. The backend returns a deterministic `[echo]` stream that exercises the SSE path.

On Windows or for an isolated reproducible backend, use Docker instead:

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

The first Docker build caches both retrieval model weights in the image; later
container starts only load and warm them before accepting chat requests. The
ingest step persists the Chroma text index under `data/.chroma/`; run it again
after catalog data changes. To enable real TokenRouter-generated answers after
the no-key smoke deployment, use the secure key switch block in
[`README.md`](../README.md#docker-deployment-on-windows-copy-and-run).

## 3. iOS Simulator (5 min)

```bash
# Open Xcode once interactively to accept the license + run first-launch (one time)
sudo xcodebuild -license accept
sudo xcodebuild -runFirstLaunch

# Build + run on the iPhone 17 Pro simulator
aaalion ios-sim
# This regenerates the .xcodeproj from project.yml, builds, installs into the booted sim, launches.
open -a Simulator
```

You should see the app launch and the chat UI render. Type a query, hit send, see streaming + product cards (assuming backend is up).

## 4. iOS Device (~10 min the first time)

This is the part that needs **one interactive Apple ID step**.

```bash
# Plug iPhone in via USB. iPhone may prompt "Trust This Computer?" → Trust.
# Verify devicectl sees it:
xcrun devicectl list devices
# Look for your iPhone in the "Available Devices" list. Note the 36-char UUID.
```

In Xcode (one-time setup):

1. Xcode → Settings (`Cmd+,`) → **Accounts** → click `+` → **Apple ID** → sign in.
2. `aaalion ios` to regenerate `.xcodeproj`, then `open client/AAALionApp/AAALionApp.xcodeproj`.
3. Click **AAALionApp** target in the left sidebar → **Signing & Capabilities** tab.
4. Set **Team** to **"<your name> (Personal Team)"**.
5. Let Xcode generate the certificate and profile (5-10 sec; the yellow warning disappears).
6. **Find your real Team ID** (the 10-char string Xcode shows after your email in the cert name is a CERT ID, not the Team ID):
   ```bash
   ls ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/   # find the new .mobileprovision
   security cms -D -i <profile.mobileprovision> | grep -A1 TeamIdentifier
   # → the <string>...</string> is your 10-char Team ID
   ```
7. Edit `settings.base.DEVELOPMENT_TEAM` in `client/AAALionApp/project.yml`:
   ```yaml
   DEVELOPMENT_TEAM: "ABC123DEF4"   # your 10-char team ID from step 6
   ```
   This bakes it in so future `aaalion ios` regenerations preserve it. Keep this as a local-only edit if you don't want to push your Team ID; it's not secret but it's identifying.

Now you can build and install via CLI:

```bash
aaalion ios-device
# At the end, the Makefile prints the exact `xcrun devicectl device install app …` command — run it.
# OR run by hand:
xcrun devicectl device install app --device <YOUR_DEVICE_UUID> \
  /tmp/lionpick-derived-device/Build/Products/Debug-iphoneos/狮选.app
```

First install on the iPhone: tap **Settings → General → VPN & Device Management → Apple Development: <your-apple-id>@... → Trust**. After that, every `aaalion ios-device` works without prompts.

> ⚠️ **Free Apple ID certs expire in 7 days.** Run `aaalion resign` once a week (or before any demo) to refresh. Schedule a calendar reminder. There's no way around this on the free tier; the $99/year Apple Developer Program would give 1-year certs but isn't worth it for this competition.

Connect the iPhone to your Mac's LAN backend. The app defaults to
`http://localhost:8000` (works on the simulator out of the box). For a real
iPhone you need your Mac's LAN IP. **Three options, in order of cleanliness:**

```bash
ipconfig getifaddr en0   # your Mac's LAN IP, e.g. 192.168.1.42
```

1. **In-app Settings sheet (recommended for day-to-day):** open the app on the
   iPhone, tap ⚙ in the top-right, paste `http://192.168.1.42:8000`, hit
   **Test Connection**, then **Save**. Persists in `UserDefaults`, survives
   app relaunches, no rebuild. This is what we expect every dev to use.
2. **Xcode scheme env var (one-off testing):** Xcode → Product → Scheme →
   Edit Scheme → Run → Arguments → Environment Variables, add
   `PUBLIC_BACKEND_URL=http://192.168.1.42:8000`. Only applies while you run
   from Xcode.
3. **Edit `Config.swift` (NOT recommended):** changing `defaultBackendURL`
   collides with other devs' commits. Don't push that change.

The repo default (`localhost`) is intentional — it means a fresh clone runs on
anyone's simulator with zero config.

## 5. Common errors and fixes

| Symptom | Cause | Fix |
|---|---|---|
| `make: *** No rule to make target 'ios'` | You're not in the repo dir | Use `aaalion ios` (works anywhere). Or `cd AAALion-` first. |
| `Signing for "AAALionApp" requires a development team` | `DEVELOPMENT_TEAM` is empty | Do §4 step 3-6 above. |
| `xcrun devicectl ... No code signature found` | Building without signing | Make sure `CODE_SIGN_STYLE: Automatic` is in project.yml (it is, by default). |
| `No Account for Team "XXXXXXXXXX"` | `DEVELOPMENT_TEAM` is the certificate ID, not the Team ID | Use the gotcha workaround in §4 step 6 to find the real Team ID and put THAT in project.yml |
| App launched yesterday but won't open today | 7-day Personal Team cert expired | `aaalion resign` |
| `Internal Server Error` from `/chat/stream` | `server/.env` not loaded | Confirm `server/.env` exists with `LLM_PROVIDER` set. Restart `aaalion backend`. |
| iOS shows user message but no reply | SSE parser hang | Fixed in commit `6e8d7b9`. If you see this on an old branch, pull main. |
| `该令牌状态不可用` from TokenRouter | Key inactive | Activate in https://www.tokenrouter.com/console/token. |
| Slow first ingest | `sentence-transformers` downloading model weights (~30 MB) | One-time. Subsequent ingests reuse the cached model under `~/.cache/torch/sentence_transformers/`. |

## 6. What to try once it works

Run the 6 scripted demos from [`docs/demos/2026-05-22/README.md`](demos/2026-05-22/README.md) to confirm parity with the reference results. If your screenshots look different, post them — we may have fixed something or regressed.

After that, you're set up to develop your area. See [`PIPELINE.md`](PIPELINE.md) for the dev SOP.
