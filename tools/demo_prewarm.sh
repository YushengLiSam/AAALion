#!/usr/bin/env bash
# demo_prewarm.sh — warm the backend caches with the EXACT queries from the
# demo script (DEMO_FLOW.md) so the live demo never hits a cold (~4.5–11 s)
# first-screen. A repeated query then returns in ~0.2 s.
#
# Run this RIGHT BEFORE the demo. The retrieval cache TTL is ~5 min, so re-run
# if more than ~5 minutes pass before you present. Idempotent.
#
#   ./tools/demo_prewarm.sh                               # cloud (default)
#   BASE=http://localhost:8000 ./tools/demo_prewarm.sh    # local backend
#
# ⚠️ BASE must point at the SAME backend the demo iPhone uses. The default is a
# rotating *.trycloudflare.com quick tunnel — if the tunnel was restarted,
# update BASE (and Config.swift) to the current URL or warming hits a dead host.
set -uo pipefail

BASE="${BASE:-https://actions-funeral-treating-trigger.trycloudflare.com}"

# ── Single-turn beats (each warms its retrieval + reranker cache) ──────────────
# Kept verbatim-aligned with DEMO_FLOW.md so every typed query is warm. The beat
# number in the comment maps to the script; 备选 = backup beats.
QUERIES=(
  "适合熬夜党用的护肤品"            # beat 1  — semantic reco
  "推荐防晒霜,不要日系的"          # beat 2  — negation / brand-origin
  "推荐手机"                       # beat 3  — turn 1 (multi-turn warmed below too)
  "对比一下这几款降噪耳机的区别"    # beat 4  — comparison table
  "随便看看"                       # beat 5  — clarify (no retrieval, cheap)
  "推荐500元以内的耳机"            # beat 6  — budget over-filter → honest fallback
  "2000以内国产的降噪耳机,不要小米" # beat 7  — budget + origin + negation
  "推荐降噪耳机"                   # beat 8  — turn 1 (cart ops short-circuit, no retrieval)
  "推荐一辆车"                     # beat 10 — out-of-domain decline (no retrieval, cheap)
  # 备选 beats
  "推荐一款适合敏感肌的洁面乳"
  "推荐手机,不要苹果和华为"
  "推荐零食"
  "推荐跑鞋"
)

# ── Multi-turn beats (send the user turns so the SAME contextual retrieval
#    query is computed and cached). build_retrieval_query only reads user
#    messages, so two consecutive user turns warm the exact follow-up key. ──────
# Each entry is the JSON `messages` array for one beat.
SEQUENCES=(
  # beat 3 follow-up: "推荐手机" → "华为以外还有吗"
  '[{"role":"user","content":"推荐手机"},{"role":"user","content":"华为以外还有吗"}]'
)
# NOTE: beat 9 (拍照找货 / CLIP image search) cannot be text-prewarmed; open the
# camera once before the demo to warm the image path. Cart sub-steps in beat 8
# (加入购物车 / 去结算 / 清空) are rule-based short-circuits — no retrieval, nothing to warm.

post() {  # $1 = json body
  curl -s -o /dev/null -w "%{http_code}" --max-time 60 \
    -X POST "$BASE/chat/stream" -H "Content-Type: application/json" -d "$1"
}

total=$(( ${#QUERIES[@]} + ${#SEQUENCES[@]} ))
echo "Prewarming $total queries against $BASE ..."
ok=0
for q in "${QUERIES[@]}"; do
  code=$(post "{\"messages\":[{\"role\":\"user\",\"content\":\"$q\"}]}")
  echo "  [$code] $q"
  [ "$code" = "200" ] && ok=$((ok + 1))
done
for seq in "${SEQUENCES[@]}"; do
  code=$(post "{\"messages\":$seq}")
  echo "  [$code] (multi-turn) $seq"
  [ "$code" = "200" ] && ok=$((ok + 1))
done
echo "Done: $ok/$total warmed. Re-run if >5 min before the demo (cache TTL)."
