# Troubleshooting / 故障排查

> Every gotcha we hit while building 狮选 LionPick, with the exact fix. Skim by category. If you hit something not in here, add it (and commit).

---

## iPhone device

### "Untrusted Developer" alert when tapping 狮选 app icon

**Symptom**: After installing the app (via `aaalion ios-device` or Xcode), tapping the 狮选 icon shows:

> Your device management settings do not allow using apps from developer "Apple Development: alexcsf01725@gmail.com (7TQ694CBJV)" on this iPhone. You can allow using these apps in Settings.

**Why**: iOS requires you to **trust** the developer certificate the first time. This is a one-time per-cert-issue step (so once per week, since free-tier certs renew weekly).

**Fix** (5 taps):

1. Tap **Cancel** on the dialog.
2. Open **Settings**.
3. **General** → **VPN & Device Management**.
4. Under **DEVELOPER APP**, tap your dev cert (e.g. **"Apple Development: alexcsf01725@gmail.com"**).
5. Tap **Trust "..."** → confirm **Trust**.

Now the 狮选 app icon launches normally.

### App launched yesterday, won't launch today

**Why**: Free-tier Personal Team certs expire ~7 days after issue.

**Fix**: On the Mac:
```bash
aaalion resign
```
Then re-tap the 狮选 icon on the iPhone. (If you also see "Untrusted Developer" again, repeat the trust steps above — Apple issues a *new* cert on each resign, and each new cert needs trusting once.)

Calendar reminder: every Sunday + the morning of any demo / defense day.

### Can't deploy: "No iPhone connected" from `aaalion ios-device`

**Fix sequence**:

1. Plug iPhone in via Lightning / USB-C cable directly to the Mac (not through a hub).
2. iPhone prompts "Trust This Computer?" → tap **Trust**, enter passcode.
3. On the Mac: `xcrun devicectl list devices`. The iPhone should appear in the "Available Devices" list with state `connected`. If not, try a different cable or USB port.
4. Retry `aaalion ios-device`.

### iPhone app shows blank chat / no AI reply

Two possible causes:

1. **Backend not running** or unreachable. Check `aaalion backend` is running on the Mac and `Config.swift`'s `PUBLIC_BACKEND_URL` points at it. For LAN testing with iPhone: set `PUBLIC_BACKEND_URL=http://<mac-lan-ip>:8000` in the Xcode scheme env vars (`ipconfig getifaddr en0` to get the IP).
2. **Stale SSE parser** (old branch). The fix landed in commit `6e8d7b9`. `git pull origin main` then `aaalion ios-device` again.

---

## Xcode / signing

### `error: No Account for Team "XXXXXXXXXX"`

**Why**: The 10-char string Xcode shows after your Apple ID email in the certificate name (e.g. `Apple Development: foo@bar.com (XXXXXXXXXX)`) is a **certificate identifier**, NOT the team identifier. They differ for Personal Teams (free accounts).

**Fix**: Find your actual Team ID from the provisioning profile:

```bash
ls ~/Library/Developer/Xcode/UserData/Provisioning\ Profiles/*.mobileprovision
security cms -D -i <that-file>.mobileprovision \
  | /usr/libexec/PlistBuddy -c "Print :TeamIdentifier" /dev/stdin
# → prints the real Team ID
```

Then set `DEVELOPMENT_TEAM` in `client/AAALionApp/project.yml` to that value (not the cert ID).

For reference: Shufeng's cert ID is `7TQ694CBJV`, Team ID is `V8KDBHKA3P`.

### `Signing for "AAALionApp" requires a development team`

**Why**: `DEVELOPMENT_TEAM` is empty in `project.yml`.

**Fix**: Either set it in `project.yml` (preferred — survives `aaalion ios` regenerations) or in Xcode GUI under the target's Signing & Capabilities tab. If you go the GUI route, remember to also bake the value into `project.yml` so future regenerations don't wipe it.

### `xcrun devicectl ... No code signature found`

**Why**: Building without code signing (sim-only project.yml setting accidentally applied to device build).

**Fix**: Confirm `client/AAALionApp/project.yml` has `CODE_SIGN_STYLE: Automatic` and `DEVELOPMENT_TEAM` set. Re-run `aaalion ios && aaalion ios-device`.

### `make: *** No rule to make target 'ios'` when run from `$HOME`

**Why**: `make` is path-relative; you ran it outside the repo directory.

**Fix**:
```bash
# One-time install of the global helper:
ln -sf "<path-to-repo>/tools/aaalion" "$HOME/.local/bin/aaalion"
# Then from anywhere:
aaalion ios
aaalion backend
aaalion ios-device
```

### Xcode GUI shows team but `xcodebuild` CLI says "No Account for Team"

**Why**: After `xcodegen` regenerates `.xcodeproj`, Xcode IDE may have a stale internal cache.

**Fix**: Close and re-open the project in Xcode (`Cmd+Q` then `open client/AAALionApp/AAALionApp.xcodeproj`). Then re-run `xcodebuild` with `-allowProvisioningUpdates`. If still failing, use the Team-ID-from-profile method above.

---

## Backend / RAG

### `/chat/stream` returns `[echo]` text instead of real LLM output

**Why**: `server/.env` isn't being read, so `LLM_PROVIDER` is unset → factory falls through to `EchoProvider`.

**Fix**: Confirm `server/.env` exists (it's gitignored — see `.env.example` for the template). `server/app/config.py` already loads from `server/.env` (after commit `6e8d7b9`); make sure you're on `main` or later.

Test:
```bash
source .venv/bin/activate
cd server && python -c "from app.config import settings; print(settings.doubao_api_key, settings.qdrant_url)"
# should print non-empty values
```

### `Internal Server Error` from `/chat/stream`

Check `tail -30 /tmp/lionpick_server.log` (or wherever your uvicorn output went). Most common:

- `ModuleNotFoundError: No module named 'rag'` → `parents[]` off-by-one in `rag_client.py` (fixed in `6e8d7b9` — pull latest).
- `Could not resolve authentication method` → LLM provider auth empty. Provide a key (`TOKENROUTER_API_KEY` or `ANTHROPIC_API_KEY`) in `server/.env` or set `LLM_PROVIDER=echo`.

### TokenRouter returns `该令牌状态不可用` ("token status unavailable")

**Why**: TokenRouter key is created but not activated.

**Fix**: Go to https://www.tokenrouter.com/console/token — there's usually an "activate" button or email-verification step. After activation, immediate calls succeed. (Our current key `sk-mfU7…` activated on 2026-05-22 evening.)

### Doubao PDF API key returns 401

**Why**: The key in the competition PDF was leaked publicly on someone's GitHub and Apple^H^H^Hthe organizer deactivated it. New keys are pending.

**Fix**: Until the new key arrives, use `LLM_PROVIDER=tokenrouter` (current default in `.env.example`).

### Chroma telemetry warnings spam the log

```
Failed to send telemetry event ClientStartEvent: capture() takes 1 positional argument but 3 were given
```

Harmless but noisy. **Fix**: `CHROMA_TELEMETRY=False` in `server/.env` (already set by default if you copied from `.env.example`).

### `pip install` fails on `pydantic-core` wheel build

**Why**: Python 3.14 has no prebuilt wheel for `pydantic-core` yet — `pip` tries to compile from source and needs Rust.

**Fix**: Use Python 3.12:
```bash
brew install python@3.12
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r server/requirements.txt
```

### `aaalion ingest` very slow on first run

**Why**: `sentence-transformers` downloads the `BAAI/bge-small-zh-v1.5` model (~30 MB) on first use. Subsequent runs reuse the cache at `~/.cache/torch/sentence_transformers/`.

**Fix**: just wait once. ~30 sec on a normal connection.

---

## A100 / `uc`

### `nvidia-smi` reports "Driver/library version mismatch"

**Why**: System driver was updated but the kernel module wasn't reloaded (or vice versa).

**Fix**: **Do NOT touch the system driver.** Fixing the mismatch requires reloading the kernel module, which kills every running CUDA process — and the shared `~/shufeng/cuda-fuzzing/` project may be running jobs.

**Workaround**: install a torch wheel matching the kernel driver into our venv:
```bash
ssh uc
cd ~/shufeng/AAALion-
source .venv/bin/activate
pip install torch==2.4.1+cu124 --index-url https://download.pytorch.org/whl/cu124
# OR fall back to CPU torch — fast enough for 100 product images:
pip install torch --index-url https://download.pytorch.org/whl/cpu
```

### Don't touch `~/shufeng/cuda-fuzzing/`

It's a different active project. Every command on `uc` should target paths under `~/shufeng/AAALion-/` only. `rsync` from MacBook with `--exclude=cuda-fuzzing` if you ever do a directory-level copy (we don't — we use targeted rsync).

---

## Repo / git

### Commits attributed to `shufengc@local` instead of GitHub login

**Why**: Old session used a placeholder email.

**Fix**: Already rewritten history to `Shufeng Chen <shufeng.c.dev@gmail.com>` and force-pushed (commit chain starting `5bad8a2`). Going forward, set:
```bash
git config user.email "<your-github-email>"
git config user.name "<your-name>"
```
Or use `git -c user.email=... -c user.name=... commit ...` per-command.

### `tools/check-secrets.sh` flagged something in my commit

**Why**: The scanner caught an ARK/Anthropic/OpenAI key shape in a tracked file.

**Fix**: Remove or redact the key (use `<REDACTED>` or a clear placeholder). Real keys go in `server/.env` (gitignored) or `~/.config/lionpick/credentials.env` (outside repo). Re-run `tools/check-secrets.sh` until it returns clean.

### Force push to shared remote

Done once on 2026-05-22 to rewrite author identity. Don't do it again without team sign-off — teammates' local refs would diverge.

---

## How to add to this page

When you hit a new gotcha:

1. Add a `### Symptom` section under the right category.
2. **Why** (1 line), **Fix** (concrete commands).
3. Commit with `docs(troubleshooting): add <symptom>`.
4. If the gotcha is severe enough to affect teammates, add a 1-line callout in the WeChat group.

Cross-references that already cover specific topics in depth:

- iOS signing & device deploy: [`IOS_SETUP.md`](IOS_SETUP.md), [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md)
- Backend / LLM / multi-provider: [`API.md`](API.md), [`server/README.md`](../server/README.md)
- Honest-answers on architecture decisions: [`HONEST_ANSWERS.md`](HONEST_ANSWERS.md)
- Major commit explanations: [`commits/`](commits/)
