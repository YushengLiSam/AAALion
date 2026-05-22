# Commit Records / 重大提交记录

> Rule defined in `docs/POLICY_LOCAL.md` (gitignored). The records themselves are committed and visible to teammates.

For every **major** commit, drop a file here. Trivial commits (typo fixes, single-line doc tweaks, formatting) don't need a record.

## What counts as "major"

- Adds or restructures more than ~3 files outside pure docs.
- Touches an API contract, schema, or anything cross-area.
- Lands a new feature, a non-trivial fix, or a release-gating dependency.
- Renames or moves a directory.

## File naming

`<YYYYMMDD>-<NNN>-<short-slug>.md`, e.g. `20260522-001-initial-scaffold.md`. `NNN` increments per day starting at 001.

## Template

Each record follows this shape (copy-paste):

```markdown
# <type>(<scope>): <summary>

**Date**: YYYY-MM-DD
**SHA**: `<short>` (fill in after commit)
**Author**: 陈澍枫 / 李雨晟 / 管图杰

## Why

<1-2 sentences on motivation.>

## What changed

- <bullet list of concrete edits>

## Procedure

<exact commands or sequence used; future-you should be able to reproduce>

## Outcome / Verification

<what was confirmed working; anomalies surfaced>

## Follow-ups

- <things this commit deferred>
```
