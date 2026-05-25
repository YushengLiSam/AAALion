# 狮选 LionPick — Dev Log

> Rolling, reverse-chronological dev log. Newest entries at the top.
> Each entry = one shipping moment (a merge to main, a verified feature,
> a postmortem). Replaces the historical `docs/WECHAT_UPDATE_*.md` files
> (deleted 2026-05-25; see this round's commit). WeChat status drafts now
> stay **local** under `docs/cluely/` (gitignored) — they are not
> committed.
>
> See also:
> - `docs/commits/YYYYMMDD-NNN-<topic>.md` — per-major-commit deep-dives.
> - `docs/PROPOSAL_YYYY-MM-DD.md` — forward-looking proposals.
> - `docs/POLICY.md` §"Team status updates" — the policy this file
>   implements.

---

## 2026-05-25 night — R8 + R8.D + R8.E (post-Tujie `2f9b6c4`)

**by**: Shufeng

After Tujie's last main-merge at `2f9b6c4` (stateful constraints + Docker
prewarm), Shufeng landed 7 commits across three sub-rounds.

### R8 core (`672c6fc`, `bcfb8ab`)

- iOS Settings: live "缓存命中率" panel polling `/cache/stats` every 10 s
  (surfaces Sam's R7e endpoint that was previously unused on the client).
- Multi-turn negation persistence: `Filter.exclude_keywords` now carries
  across turns via `constraint_state.py` merge — `"推荐防晒霜不要日系"` →
  `"再便宜点的呢"` now excludes JP brands on turn 2.
- Brand-origin coverage extended to KR / DE / GB. `"不要韩系 / 英系 /
  德系"` now hard-filters.
- Golden audit spot-check: confirmed Sam/Tujie's `negation_accuracy=1.000`
  came from real label corrections (not cherry-picking).
- 9 demo screenshots + sidecar `.md` under `docs/demos/2026-05-25-evening/`.

### R8.D — public deployment (`417f840`, `a22ce7d`)

- **Cloudflare Tunnel** exposes `localhost:8000` as
  `https://reader-missile-absolute-memphis.trycloudflare.com`.
  iPhone connects from any network (cellular, public Wi-Fi, hotel SSID)
  with zero LAN setup. `Config.swift` bakes the tunnel URL.
- **Dev-mode gesture**: long-press the gear icon for 1.5 s flips
  `@AppStorage("lionpick.devMode")`; the backend-URL editor in Settings
  is hidden by default and only appears in dev mode. Visual feedback:
  `gearshape` → `gearshape.fill` (amber) when ON.
- **Voice cross-session fix** (`a22ce7d`): `SFSpeechRecognitionTask`
  leaks one final callback after `cancel()`. `sessionID` guard +
  `task.finish()` (instead of `cancel()`) + defensive draft clear in
  `startListening` close the `"toy + cosmetic = 'toys and cosmetic'"`
  bug.

### R8.E — iOS UX parity with ChatGPT / Claude (`1a32a79`, `c14972c`, `0eb143a`)

- **Voice idle-timer auto-stop**, 1.8 s threshold on the main RunLoop in
  `.common` modes. Same-text partials suppressed via `lastTranscript`
  guard so ambient noise doesn't extend the window. `onStop` callback
  syncs the ViewModel's UI-bound flag when the timer fires.
  Composer shows `"正在听… / Listening — 停顿 ~2 秒自动结束"`.
- **Multi-attachment up to 10** (photos + files + camera, mixed). New
  `Attachment` struct (`kind: .photo/.camera/.file`, MIME sniffed from
  magic bytes). Composer horizontal scroll-row of 64×64 chips with
  x-delete and "N/10" counter. Message bubble renders a 2-row
  `LazyVGrid` (5 per row, 96×96) above text.
- **PhotosPicker selection bug**: plural `PhotosPicker` inline in a
  `Menu` had a SwiftUI binding bug. Switched to
  `.photosPicker(isPresented:)` modifier on the NavigationStack; Menu
  now just sets a flag.
- **Image downsample on upload**: `Attachment.compressForUpload` resizes
  to 1280 px on the longest edge and re-encodes JPEG @ 0.78.
  Typical 4032×3024 iPhone photo: 2.4 MB → ~120 KB (20× shrink).
- **Backend async-offload**: `top_k_image()` and `top_k()` now run inside
  `asyncio.to_thread()` so the FastAPI event loop stays responsive
  during retrieval. This was why `/cache/stats` timed out during a
  multi-image chat — single-worker uvicorn was being event-loop-
  blocked by sync torch calls. Cache stats fetch timeout also
  bumped from 15 s → 60 s as belt-and-suspenders.
- **Backend multi-image**: `_extract_image_bytes` →
  `_extract_image_bytes_list` (cap 10). CLIP retriever still uses
  `imgs[0]` (single-image visual retriever); LLM sees all images via
  the content array. Cache key uses `hash_image_bytes_list` (sorted
  SHA concat) so order doesn't matter.

### Quality numbers

Unchanged from R7.3 merge (R8 work was UX + infra, not retrieval):
- `recall@5 = 0.880`, `MRR = 0.828` (audited 59-case set)
- `negation_accuracy = 1.000`
- self-assessed **~91-92 / 100**

### What's open going into R9

- Demo video (3-5 min QuickTime screencast)
- Defense slide deck (Gamma prompt is in `docs/defense/gamma-prompt.md`)
- Phase 2 cloud VM deploy (Hetzner CX22, ~2026-06-05)
- Chroma snapshot zip on Drive for team-internal distribution
