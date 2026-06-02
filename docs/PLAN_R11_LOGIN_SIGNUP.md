# Plan — R11: Login / Sign-up page + Account profile (2026-06-01)

> Next-step plan. Decision locked by Shufeng: **build a proper login /
> sign-up page — yes, definitely.** The auth *backend* and a basic
> *LoginView sheet* already exist (R10); this round turns them into a
> first-class, polished login/sign-up experience + an account profile.
> Owner: Shufeng (iOS). Touches Yusheng's cloud user seam (coordinate
> only when we wire to cloud — deferred, see §6).

---

## 1. Why now

- We verified accounts work end-to-end (register / password-login /
  Apple / phone / migrate all green on cloud), but the entry is a form
  **buried in Settings → 我的账号**. For a defense demo it should be a
  **proper login/sign-up page** that looks like a real product.
- The team asked "should we build a login/sign-up page?" → yes. It also
  rounds out the "real product" story (accounts → social 拼单 →
  cross-device preferences) without over-reaching on payment.

## 2. What already exists (don't rebuild)

- **Backend** (`server/app/routes/auth.py`, `services/user_store.py`):
  `/auth/register`, `/auth/password/login`, `/auth/apple`,
  `/auth/phone/start|verify`, `/auth/me`, `/auth/migrate`. Pluggable
  cloud seam (`USER_STORE_BACKEND=local|cloud`). **No backend work needed
  for R11.**
- **iOS** (`Views/LoginView.swift`, `Services/AuthService.swift`,
  `AuthState`, `DeviceIdentity` re-key): a working `Form`-based sheet with
  密码 / 短信 / Apple. The re-key + anonymous-data migration already fire
  on sign-in. **Reuse the service layer as-is; rebuild the UI.**

## 3. Design

### 3a. Login / Sign-up page (the headline)
Promote `LoginView` from a Settings `Form` sheet to a **branded,
full-screen page**:
- **Header**: lion logo + 狮选 wordmark + one-line value prop
  ("登录后,你的偏好 / 收藏 / 拼单 跟着账号走").
- **Segmented method picker** (keep the 3 we built): **密码 · 短信 · Apple**,
  密码 default.
- **登录 ↔ 注册 toggle** for the password path (already wired); inline
  validation (email/phone shape, password ≥6) + clear error states.
- **Visual polish**: app theme tokens, rounded fields, primary CTA button,
  loading state, haptic on success.
- **"先逛逛 / Skip"**: core browse + chat stay anonymous — never block the
  app behind login. Login is for social/cross-device only.

### 3b. Entry points
- **Profile avatar in the chat top bar** (next to 🛒 / ⚙️): shows a
  generic person icon when logged out → taps to the login page; shows the
  account initial when logged in → taps to the profile page.
- **Optional first-launch soft prompt** (skippable, shown once): a small
  card "登录解锁拼单 + 跨设备偏好" with 登录 / 先逛逛. Stored "shown" flag in
  UserDefaults so it never nags.

### 3c. Account / profile page (post-login)
A real "我的" page consolidating what's scattered today:
- Account identity (display name + method badge: Apple / 手机 / 邮箱).
- **我的偏好** (move the existing Settings preferences panel here).
- **我的收藏** (the favorites Sam added — currently no dedicated view).
- **我的拼单** (active group-buys via `/groupbuy/active`).
- 退出登录.

## 4. iOS task breakdown

| # | Task | File(s) | Est |
|---|---|---|---|
| 1 | Rebuild `LoginView` as a branded full-screen page (reuse `AuthService`/`AuthState`) | `Views/LoginView.swift` | 0.5 d |
| 2 | Profile avatar entry in chat top bar (logged-out → login, logged-in → profile) | `Views/ChatView.swift` | 0.25 d |
| 3 | New `ProfileView` (identity + preferences + favorites + 拼单 + sign-out) | `Views/ProfileView.swift` (new) | 0.5 d |
| 4 | First-launch soft prompt (skippable, once) | `Views/ChatView.swift` + UserDefaults flag | 0.25 d |
| 5 | Move preferences panel out of Settings into ProfileView; leave Settings for dev/cache | `Views/SettingsView.swift` | 0.25 d |
| 6 | Build + device-install + verify the full sign-up → login → profile flow on iPhone 13 | — | 0.25 d |

**Total ≈ 2 days.** No backend work. New files need an `xcodegen generate`.

## 5. Test plan (how we verify, given headless limits)
- **Backend**: already curl-verified (register/login/wrong-pw 400/migrate).
- **Simulator**: drive chat via the `-test-query` harness for the
  non-tap states; screenshot the login page render. (The login *form* and
  chip taps can't be auto-tapped headlessly — see §note.)
- **iPhone 13 (Shufeng holds it)**: the real tap-through —
  register `x@example.com / lion1234` → profile shows the account → tap 👍
  on a product → it persists in 我的偏好 → favorite a card → it shows in
  我的收藏. This is the authoritative manual pass.
- *Note on "simulating taps":* `simctl` has no tap primitive; the
  `-test-query` harness simulates sending a message (covers chat states).
  For form/chip/heart taps we rely on the physical device + the user, or
  (future) an XCUITest target if we want CI-grade UI automation.

## 6. Scope guardrails (don't let this eat the demo runway)
- **Stay on the LOCAL user store** for the demo. Wiring to Yusheng's cloud
  user service is the seam's job and is **deferred** — cite it as the
  production path. (Per `PROPOSAL_2026-05-30.md` P4.)
- **No payment, no WeChat OAuth** (needs 企业资质). Out of scope.
- Login stays **optional** — never gate browse/chat.

## 7. Parallel team items (still open, not part of R11)
1. **Demo video + deck (P0)** — still the single highest-value unstarted
   item; the deadline is 2026-06-10. Owner: Shufeng. *Sequence R11 to not
   block this — if time is tight, the demo wins.*
2. **Regenerate the committed eval report** — `docs/EVAL_RESULTS.md` still
   says 68/71 cases; current is 82 (recall@5 0.960 full / 0.932 fast, neg
   1.000). Regenerate so the deck cites committed numbers.
3. **Tujie's Docker docs** — still not merged to main (cherry-pick, don't
   merge the stale branch).
4. **Stable cloud domain** — replace the rotating quick-tunnel (demo-day
   risk). Owner: Yusheng.

## 8. Open decisions
1. **First-launch prompt: show it, or only the top-bar avatar entry?** My
   vote: top-bar avatar always; soft prompt once (skippable).
2. **Sequence vs demo video:** start R11 now, or lock the demo first? My
   vote: **R11 is ~2 days and makes the product feel real for the demo, so
   do it first, then record** — but hard-stop R11 if it slips past ~2 days.
3. Profile page scope: minimal (identity + sign-out) vs full (preferences
   + favorites + 拼单)? My vote: full — it consolidates UX that's currently
   scattered and shows depth.
