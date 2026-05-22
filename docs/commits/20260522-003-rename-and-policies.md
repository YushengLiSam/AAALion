# chore(repo): rename product to 狮选 LionPick, add commit-format and major-record policies, solo-dev plan, automation

**Date**: 2026-05-22
**SHA**: `8266853`
**Author**: 陈澍枫 (Shufeng)

## Why

Three drivers landed on the same day:

1. **Identity**: AAALion is the team name; the product needs a distinct, brandable name for the demo and slides. Chose 狮选 LionPick — bilingual, ties to lion identity, describes the AI's job ("picks the right product").
2. **Process**: Going forward we use Conventional Commits and write a record file for every major commit. Codifying both removes ambiguity later.
3. **Reality**: 陈澍枫 is functionally the fallback owner for every area. Documented this as `docs/SOLO_DEV_PLAN.md` so the plan survives any teammate slip.

Also: today's Doubao API test (`curl ... 401 AuthenticationError`) confirmed the PDF key is invalid. Wired the real `DoubaoClient` so it auto-uses any key dropped into `.env` and falls back to a fixture stream otherwise — unblocks iOS dev regardless.

## What changed

### Naming and Chinese names (bilingual context)
- `README.md` — full rewrite: title "狮选 LionPick", Chinese + English context, Chinese teammate names (陈澍枫 / 李雨晟 (Sam) / 管图杰 (Tujie)).
- `docs/POLICY.md` — header now includes product name; team table in bilingual form; decision log appended.
- `docs/EXECUTION_SUMMARY.md` — names updated.
- `docs/ROADMAP.md` — table headers + risk register names updated.
- `docs/FUTURE_WORK.md` — section headers updated.
- `docs/PIPELINE.md` — ownership lines updated.
- `meetings/2026-05-20-kickoff.md` — Present/decisions/action items updated; added fallback约定 line.

### New policies
- `docs/POLICY.md` — added "Commit message format" (Conventional Commits with type/scope/summary); appended three decision-log rows (product name, commit format, solo-dev posture).
- `docs/POLICY_LOCAL.md` — new (gitignored). Contains the major-commit-record rule (what counts as major, file naming, template).

### Commit records infrastructure
- `docs/commits/README.md` — explains the format.
- `docs/commits/20260522-001-initial-scaffold.md` — retroactive record for SHA 235224f.
- `docs/commits/20260522-002-execution-summary.md` — retroactive record for SHA d7d5377.
- `docs/commits/20260522-003-rename-and-policies.md` — this file.

### Automation
- `Makefile` — `backend` / `mock` / `ingest` / `eval` / `ios` / `sync-a100` / `fmt` / `clean`.
- `client/AAALionApp/project.yml` — xcodegen config; `.xcodeproj` is now gitignored and regenerated from this YAML.
- `tools/mock_backend.py` — offline mock that mimics `/chat/stream` so iOS dev can proceed without Doubao or Qdrant.
- `.gitignore` — added `*.xcodeproj/` (xcodegen regenerates it).

### Backend wiring
- `server/app/services/doubao_client.py` — real implementation using `openai.AsyncOpenAI` against the ARK base URL; gracefully unavailable when `DOUBAO_API_KEY` is empty.
- `server/app/routes/chat.py` — routes to real Doubao when configured, falls back to fixture stream otherwise. Pulls top-3 products from `rag_client.stub_top_k` and emits them as `product_card` events.

### Team comms
- `docs/WECHAT_UPDATE_2026-05-22.md` — paste-ready Chinese WeChat message for the team channel; long, structured for AI tool analysis on the teammates' side.
- `docs/SOLO_DEV_PLAN.md` — what 陈澍枫 does if teammates don't deliver; tier-1 / tier-2 / tier-3 tasks; tools to use; risk-cut checklist; when-to-stop-adding-features rule.

## Procedure

```
# 1. Doubao live test
curl https://ark.cn-beijing.volces.com/api/v3/chat/completions \
  -H "Authorization: Bearer ark-2af51d30-..." -H "Content-Type: application/json" \
  -d '{"model":"ep-20260514111645-lmgt2","messages":[{"role":"user","content":"你好"}]}'
# → 401 AuthenticationError → key is not valid; flagged in SOLO_DEV_PLAN.md

# 2. Edit policy, README, docs (see "What changed").
# 3. Write commit records under docs/commits/.
# 4. Wire DoubaoClient and chat route with the real-or-fixture branch.
# 5. Add Makefile, xcodegen project.yml, mock backend.

# 6. Commit (Conventional Commits format).
git add ...
git -c user.email=shufengc@local commit -m "chore(repo): rename product to 狮选 LionPick, add policies + automation"
git push origin main
git checkout shufeng && git merge --ff-only main && git push origin shufeng
make sync-a100   # rsync to ~/shufeng/AAALion-/
```

## Outcome / Verification

- `cat docs/POLICY.md | grep "Commit message format"` → present.
- `cat .gitignore | grep POLICY_LOCAL` → present.
- `ls docs/commits/` → README + 3 record files.
- `make help` lists all targets.
- `xcodegen --spec client/AAALionApp/project.yml` would generate `AAALionApp.xcodeproj` (deferred until 陈澍枫 installs xcodegen — `brew install xcodegen`).
- Backend with no Doubao key still serves `/chat/stream` (fixture branch).

## Follow-ups

- Install xcodegen and confirm the project builds in Xcode (`make ios`).
- Send the WeChat message to the team (paste from `docs/WECHAT_UPDATE_2026-05-22.md`).
- 李雨晟: confirm real Doubao API key with the organizer.
- Schedule Sunday 5/24 sync per the WeChat message.
