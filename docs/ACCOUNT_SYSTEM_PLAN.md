# Account / Auth System Plan

> **STATUS (2026-06-02, updated):** most of this plan is now **shipped on
> `main`** — Tujie implemented change-password, forgot/reset, account
> deletion, and the admin user-list (`917d7fe` server, `c1c820b` client +
> `AccountSecurityViews.swift`, with `test_account_management.py`). The
> R11-reconciliation below is **resolved**: the team's R11 login/profile
> shipped; the parallel `shufeng` version was retired.
>
> **Still outstanding** (kept here as the spec): **#0 signed-JWT session
> token** (today `token == user_id`), **#5 login rate-limit / lockout**,
> **#6 Apple JWT signature verification** (cloud). Treat the rest of this
> doc as the design record for what was built + what remains.
>
> *Original proposal text follows.*

> 2026-06-02. Written after surveying the team's remote work. Builds on
> the existing auth backend (`server/app/routes/auth.py`,
> `services/user_store.py`). Owners per the team's WeChat split
> (Tujie: change-pw / forgot / admin; Yusheng: token + cart + cloud).

---

## 0. What the team shipped (findings)

- **`origin/main` (`0854da5`)**: `GET /products/{id}` enriched with
  image_url + provenance (`0c2ff14`); **mock WeChat login** `POST
  /auth/wechat` + `user_store.mock_wechat` (`0854da5`) — explicitly
  labelled demo, not real OAuth.
- **`origin/Tujie` (`841718d`)** — the team's active iOS branch, NOT yet
  in main, containing:
  - **Their own R11** login/sign-up page + ProfileView (`e16e1c4`,
    `9beb6a8`) — a *parallel* implementation to mine (different design:
    🙌 header, polished 我的收藏).
  - **Demo WeChat login button** on the login page (`59928b8`).
  - **Cart + favorites made per-account** (`841718d`) — `CartStore` now
    keyed by `DeviceIdentity.userId` with `reloadForCurrentUser()` +
    migration; **this fixes the "购物车换号之后还是原来那样" bug.** ✅
  - **GitHub Actions iOS CI** (`243da39`, `5bd5c50`, `efab683`).
- **`origin/shufeng` (`cef2d43`)**: my R11 (branded 🦁 login + ProfileView
  + top-bar avatar + sim test hooks). NOT in main.

### ⚠️ R11 is duplicated — decide this first
There are **two R11 implementations** (mine on `shufeng`, theirs on
`Tujie`), both rewriting `LoginView.swift` + adding `ProfileView.swift` +
editing `ChatView.swift`. Merging both = a guaranteed conflict.

**Recommendation: adopt the team's R11 (`Tujie` branch), retire mine.**
Theirs is a superset — it has the **cart/favorites per-account fix**, the
WeChat button, CI, and the 我的收藏 polish, and **Tujie already tested it
("页面没问题的")**. My `shufeng` R11 adds nothing theirs lacks except the
`-test-show-login/-profile` sim hooks (cheap to re-add if wanted). So:
- **Do NOT merge `shufeng` `cef2d43` to main.**
- Merge **`Tujie` → main** (carries their R11 + the cart fix + CI).
- I'll rebase my future work on their R11. (My R11 was a parallel-effort
  miss — theirs wins on completeness; no point fighting it.)

This is the cleanest path and unblocks everything below.

---

## 1. The gaps Tujie listed (the actual ask)

| # | Gap | Status today | Owner (WeChat split) |
|---|---|---|---|
| 0 | **Real signed session token** | `token == user_id` (`auth.py` `_with_token`); no rate-limit/lockout | **Yusheng** (unblocker) |
| 1 | 改密码 change password | no endpoint/UI | Tujie |
| 2 | 忘记密码 / 重置 | no flow | Tujie |
| 3 | 注销账号 / delete | only 退出登录 (clears local) | Tujie |
| 4 | 管理员视角 admin | no backend to list/manage users | Tujie |
| 5 | 登录限流 / 锁定 | none | Yusheng |
| 6 | Apple JWT 签名校验 | demo seam (unverified) | Yusheng (cloud) |
| 7 | 购物车/收藏跟账号走 | **DONE** on Tujie branch (`841718d`) | Yusheng ✅ |

Everything 1–4 **depends on #0** (they need an authenticated session to be
safe). Tujie said exactly this: "我等 token 更新了搞一下…". So **#0 is the
critical path.**

---

## 2. Full solution

### #0 — Real signed session JWT *(Yusheng — do first)*
Replace `_with_token`'s `token = user_id` with a signed token. **No new
dependency needed** — reuse the `hmac`/`hashlib` already imported in
`user_store.py`:

- **Issue** a compact HS256 JWT on every auth success (register / login /
  verify_phone / apple / wechat):
  ```
  token = b64url(header) + "." + b64url(payload) + "." + b64url(HMAC_SHA256(secret, signing_input))
  header  = {"alg":"HS256","typ":"JWT"}
  payload = {"sub": user_id, "iat": now, "exp": now + 30*86400}
  secret  = os.environ["LIONPICK_JWT_SECRET"]   # gitignored, in server/.env + cloud env
  ```
- **Verify** via a FastAPI dependency:
  ```python
  def current_user(authorization: str = Header(None)) -> str:
      # parse "Bearer <jwt>", check HMAC + exp → return sub (user_id), else 401
  ```
  Apply it to `/auth/change-password`, `DELETE /auth/account`, switch
  `/auth/me` to header-based, and the admin routes.
- **Client**: `AuthState` already stores `token`; `AuthService` adds
  `Authorization: Bearer \(token)` on protected calls. The non-protected
  feature calls (chat/groupbuy/preferences) keep sending `user_id` for
  now — flip them later if desired.
- **Back-compat window**: during transition, `current_user` may also
  accept a bare `user_id` for one release so nothing breaks mid-rollout;
  remove after the client ships.
- **Cloud seam**: `CloudUserStore` already returns whatever `token` the
  cloud issues — the cloud can mint a real RS256 JWT with no client change.

### #1 — Change password *(Tujie)*
- `POST /auth/change-password` (Bearer) `{current_password, new_password}`
  → `verify_password(current)` then re-hash. 400 on wrong current; 401 if
  not authed. Only valid for `provider == "password"` accounts.
- **iOS**: a row in ProfileView → sheet with current + new (+ confirm)
  fields, validation (≥6), success toast.

### #2 — Forgot / reset password *(Tujie)*
- `POST /auth/forgot` `{identifier}` → issues a reset code (reuse the
  `sms_codes` table + TTL); **demo backend returns the code on-screen**
  like the SMS path; cloud emails/SMS it.
- `POST /auth/reset` `{identifier, code, new_password}` → verify code →
  re-hash.
- **iOS**: a "忘记密码?" link on the login page → identifier → code →
  new password.

### #3 — Account deletion / 注销 *(Tujie)*
- `DELETE /auth/account` (Bearer) → delete the `users` row **and cascade**
  the user's data. Reuse the `_REKEY_TABLES` list (preferences /
  price_watch / repurchase) for the server-side delete; the client clears
  the per-account cart/favorites keys (now that they're per-account from
  `841718d`).
- **iOS**: ProfileView → 注销账号 (destructive, double-confirm: "永久删除,
  不可恢复").
- Distinct from 退出登录 (which just clears local session).

### #4 — Admin view *(Tujie)*
- `GET /admin/users` gated by `X-Admin-Token == os.environ["LIONPICK_ADMIN_TOKEN"]`
  (simplest; or a `users.is_admin` flag) → `[{user_id, provider,
  display_name, created_at, counts}]`. Optional `DELETE /admin/users/{id}`.
- **Minimal surface**: expose via the existing Swagger `/docs` for the
  demo; a tiny SwiftUI admin screen (behind a hidden gate) is optional
  polish, not required.

### #5 — Login rate-limit / lockout *(Yusheng)*
- Track failed attempts per `identifier` (in-memory dict or a
  `login_attempts` table): **5 fails / 15 min → 429 + lockout 15 min**;
  reset on success. Cheap, and closes the "no lockout" gap Tujie flagged.

### #6 — Apple JWT verification *(Yusheng — cloud only)*
- The cloud `UserStore` verifies Apple's signature against Apple's JWKS +
  `aud`/`iss`/`exp` (local store decodes unverified, already documented as
  a seam). **Only matters if we demo Apple sign-in — which we don't on the
  free team — so this is lowest priority.**

### #7 — Cart / favorites per-account — ✅ DONE
Yusheng's `841718d` already does it. **Action: just merge `Tujie` → main.**

---

## 3. Sequencing + scope guardrail

```
#0 JWT (Yusheng) ──┬─→ #1 change-pw (Tujie)
                   ├─→ #3 delete (Tujie)
                   └─→ #4 admin (Tujie)
#2 forgot (Tujie)        ── parallel, light dep on #0
#5 rate-limit (Yusheng)  ── independent
#6 Apple verify (cloud)  ── lowest pri (we don't demo Apple)
#7 cart per-account      ── DONE → merge Tujie→main
```

**Honest priority (demo > completeness — same thesis as
`PROPOSAL_2026-05-30.md`):**
- **MUST** for a credible auth story: **#0 JWT + #1 change-pw + #3 delete +
  merge #7**. These make "real accounts" defensible.
- **NICE**: #2 forgot, #4 admin, #5 rate-limit.
- **SKIP for the demo**: #6 (Apple verify) — no Apple demo on free team.
- **Do NOT** let admin/rate-limit polish eat the **demo video + deck**
  runway — that's still P0 and unstarted, and the deadline is **2026-06-10**.

---

## 4. Cross-cutting notes
- **Data cascade**: deletion (#3) is the inverse of `user_store.migrate()`
  — keep both covering the same table set (preferences / price_watch /
  repurchase server-side; cart / favorites client-side per-account).
- **Secrets**: `LIONPICK_JWT_SECRET` + `LIONPICK_ADMIN_TOKEN` go in
  `server/.env` (gitignored) + the cloud env — never committed; the
  pre-commit secret scan still applies.
- **My existing auth backend** (`user_store`/`auth.py`) is the foundation
  all of this extends — it's already on main, so there's nothing to
  "merge" from my side; the work is additive endpoints + iOS surfaces.

## 5. Decisions for the team
1. **Adopt Tujie-branch R11, retire shufeng R11?** (my strong rec: yes.)
2. **JWT now (Yusheng), so Tujie can start #1/#3?** (rec: yes — it's the
   unblocker.)
3. **Admin: Swagger-only, or a SwiftUI screen?** (rec: Swagger-only for
   the demo.)
4. **Merge `Tujie` → main when?** (rec: soon — it carries the cart fix +
   CI that everyone benefits from.)
