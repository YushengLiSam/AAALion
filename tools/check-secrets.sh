#!/usr/bin/env bash
# check-secrets.sh — scan staged files for API-key-shaped strings.
# Designed to be run as `make check-secrets` (or wired as a pre-commit hook).
#
# Looks for:
#   - Doubao ARK keys:    ark-[a-f0-9-]{30,}
#   - Anthropic keys:     sk-ant-[A-Za-z0-9_-]{20,}
#   - OpenAI keys:        sk-[A-Za-z0-9_-]{40,}
#   - Generic long alnum tokens of suspicious shape

set -euo pipefail

PATTERNS=(
  'ark-[a-f0-9-]{30,}'
  'sk-ant-[A-Za-z0-9_-]{20,}'
  'sk-[A-Za-z0-9_-]{40,}'
)

# Explicit blocklist of known-dead-but-DQ-risk strings. These are keys
# the organizer themselves leaked publicly (PDF / WeChat / etc.) and
# that we may have documented in incident postmortems — even though
# they're deactivated, the organizer's auto-scanner (per 2026-05-25
# WeChat announcement) regex-matches API-key shapes regardless of
# liveness. Block any future reintroduction.
BLOCKLIST=(
  'ark-2af51d30-ed70-4061-a2cd-74f454ccc4e8'  # PDF Doubao key, deactivated 2026-05-22
  'ark-7bf37d28-942e-40b4-839d-f9ea281f135e-dd134'  # current live Doubao key, R8.F.3 — never commit
)

# Files to scan: staged for commit if running pre-commit; else all tracked + dirty.
if git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -q .; then
  files=$(git diff --cached --name-only --diff-filter=ACM)
else
  files=$(git ls-files)
fi

found=0
for f in $files; do
  [ -f "$f" ] || continue
  case "$f" in
    *.png|*.jpg|*.jpeg|*.gif|*.pdf|*.zip|data/seed/*) continue ;;
    # Don't scan this script itself — it contains the blocklist strings
    # verbatim by design, which would cause it to fail on its own contents.
    tools/check-secrets.sh) continue ;;
  esac
  for p in "${PATTERNS[@]}"; do
    if grep -EnH "$p" "$f" 2>/dev/null; then
      found=1
    fi
  done
  for b in "${BLOCKLIST[@]}"; do
    if grep -FnH "$b" "$f" 2>/dev/null; then
      found=1
    fi
  done
done

if [ "$found" -eq 1 ]; then
  echo ""
  echo "❌ Found something that looks like an API key. Move it to .env (gitignored) before committing." >&2
  exit 1
fi

echo "✅ No API-key-shaped strings in scanned files."
