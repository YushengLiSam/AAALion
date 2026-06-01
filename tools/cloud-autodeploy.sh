#!/usr/bin/env bash
# cloud-autodeploy.sh — pull origin/main onto the prod VM and restart, with
# a ready-check rollback guard. Driven by lionpick-autodeploy.timer (every
# ~2 min). Safe to run by hand too.
#
# Behaviour:
#   1. git fetch. If origin/main == current HEAD → nothing to do, exit.
#   2. If origin/main is a KNOWN-BAD commit (last deploy failed its
#      ready-check) → skip, so we don't loop redeploy→fail→rollback every
#      2 min. Cleared automatically when origin/main advances past it.
#   3. Otherwise: reset --hard origin/main, restart lionpick, wait for
#      /ready. If ready → done. If NOT ready within ~40 s → roll back to
#      the previous good commit, restart, record the bad SHA.
#
# Runs as the deploy user (git uses ~/.ssh/config github-lionpick alias +
# read-only deploy key). Needs passwordless sudo for `systemctl restart
# lionpick` (see the sudoers drop-in installed alongside).
set -uo pipefail

REPO="${LIONPICK_REPO:-$HOME/AAALion-}"
READY_URL="http://127.0.0.1:8000/ready"
BAD_MARKER="$HOME/.lionpick-autodeploy-bad-sha"
LOG_TAG="lionpick-autodeploy"

log() { echo "$(date -Is) $*"; }

cd "$REPO" 2>/dev/null || { log "repo not found: $REPO"; exit 1; }

# 1) Fetch. Network blip → skip this round quietly (timer retries soon).
git fetch origin -q 2>/dev/null || { log "fetch failed (network?), skipping"; exit 0; }

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse origin/main)"

# Up to date.
[ "$local_sha" = "$remote_sha" ] && exit 0

# 2) Known-bad guard — don't keep redeploying a commit that already failed.
if [ -f "$BAD_MARKER" ] && [ "$(cat "$BAD_MARKER")" = "$remote_sha" ]; then
  log "origin/main $remote_sha is known-bad (a prior deploy failed ready-check); skipping until it advances"
  exit 0
fi

log "deploying $local_sha -> $remote_sha"
git reset --hard origin/main -q
sudo systemctl restart lionpick

# 3) Ready-check. Warmup loads BOTH cross-encoder rerankers + CLIP +
# embeddings; under CPU contention this can take ~60 s (measured), so allow
# a generous 150 s before declaring the deploy failed. A too-tight window
# FALSE-rolls-back a good-but-slow-warming deploy and marks it known-bad —
# strictly worse than a slightly delayed rollback of a genuinely broken one
# (rare, since we build/test before pushing). Was 40 s; that tripped on
# 7c77ad1 whose warmup hit ~60 s under load.
ok=0
for _ in $(seq 1 75); do
  sleep 2
  if curl -sf "$READY_URL" 2>/dev/null | grep -q 'ready'; then ok=1; break; fi
done

if [ "$ok" = "1" ]; then
  log "deploy OK — now at $remote_sha"
  rm -f "$BAD_MARKER"
else
  log "deploy FAILED ready-check — rolling back $remote_sha -> $local_sha"
  echo "$remote_sha" > "$BAD_MARKER"
  git reset --hard "$local_sha" -q
  sudo systemctl restart lionpick
  log "rolled back to $local_sha"
fi
