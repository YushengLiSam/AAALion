#!/usr/bin/env bash
# demo_prewarm.sh — warm the backend caches with the queries you'll show
# live, so the demo never hits a cold (~4.5–8 s) first-screen. The response
# + retrieval caches make a repeated query return in ~0.2 s.
#
# Run this RIGHT BEFORE the demo. The retrieval cache TTL is ~5 min, so
# re-run if more than ~5 minutes pass before you present. Idempotent.
#
#   ./tools/demo_prewarm.sh                          # cloud (default)
#   BASE=http://localhost:8000 ./tools/demo_prewarm.sh   # local backend
set -uo pipefail

BASE="${BASE:-https://actions-funeral-treating-trigger.trycloudflare.com}"

# The scripted demo queries — add yours here. Cover the differentiators:
# basic reco, negation/exclusion, comparison, clarify, multi-turn, cart.
QUERIES=(
  "推荐面霜"
  "推荐一款适合敏感肌的洁面乳"
  "推荐降噪耳机"
  "推荐跑鞋"
  "除了耐克还有什么运动鞋"
  "推荐防晒霜,不要日系的"
  "推荐跑鞋,要国产的"
  "对比一下这几款防晒霜哪个好"
  "推荐个礼物"
  "随便看看"
  "iphone"
  "推荐零食"
)

echo "Prewarming ${#QUERIES[@]} queries against $BASE ..."
ok=0
for q in "${QUERIES[@]}"; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 60 \
    -X POST "$BASE/chat/stream" -H "Content-Type: application/json" \
    -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$q\"}]}")
  echo "  [$code] $q"
  [ "$code" = "200" ] && ok=$((ok + 1))
done
echo "Done: $ok/${#QUERIES[@]} warmed. Re-run if >5 min before the demo (cache TTL)."
