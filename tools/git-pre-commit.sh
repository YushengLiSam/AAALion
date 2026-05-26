#!/usr/bin/env bash
# git-pre-commit.sh — wire as .git/hooks/pre-commit to block any commit
# that would land an API-key-shaped string in the repo.
#
# Install:
#   ln -sf ../../tools/git-pre-commit.sh .git/hooks/pre-commit
#
# Behaviour:
#   * Delegates the actual scan to tools/check-secrets.sh, which already
#     reads `git diff --cached` and exits non-zero on any match.
#   * On match: the commit is aborted; reviewer sees which file / line.
#   * To bypass intentionally (rare, e.g. committing a test fixture that
#     legitimately contains a key-shaped string): `git commit --no-verify`.

set -euo pipefail

# Walk up to the repo root so the hook works no matter where git is invoked from.
REPO_ROOT=$(git rev-parse --show-toplevel)
exec "$REPO_ROOT/tools/check-secrets.sh"
