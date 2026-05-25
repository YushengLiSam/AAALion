#!/usr/bin/env bash
# Start a Cloudflare quick-tunnel that exposes the local FastAPI backend
# (http://localhost:8000) on a public HTTPS URL.
#
# Why we need this:
#   The iOS app must reach the backend from any network (cellular, hotel
#   Wi-Fi, judge's laptop). A baked LAN IP (192.168.x.x) only works on
#   the dev's Wi-Fi. Cloudflare Tunnel gives the Mac a public URL so the
#   iPhone connects regardless of where it is — no LAN setup, no IP entry.
#
# Usage:
#   tools/start-tunnel.sh                  # run in foreground
#   tools/start-tunnel.sh &                # run in background; URL in log
#
# After running:
#   1. Copy the URL.
#   2. Open client/AAALionApp/AAALionApp/Config.swift.
#   3. Replace `defaultBackendURL` with the URL.
#   4. Rebuild iOS: aaalion ios-device (or xcodebuild).
#   5. Force-quit and reopen 狮选 on the iPhone.
#
# Phase 2 (defense): replace this with a real cloud VM (Hetzner CX22
# €4.5/mo recommended). See docs/DEPLOY_GUIDE.md §Cloud VM.

set -euo pipefail

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not installed. Install via: brew install cloudflared" >&2
  exit 1
fi

LOG=/tmp/cloudflared.log
: > "$LOG"
echo "Starting cloudflared tunnel -> http://localhost:8000"
cloudflared tunnel --url http://localhost:8000 --no-autoupdate > "$LOG" 2>&1 &
PID=$!
trap "kill $PID 2>/dev/null || true" EXIT INT TERM

for i in $(seq 1 30); do
  sleep 1
  URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" "$LOG" | head -1 || true)
  if [ -n "${URL:-}" ]; then
    echo
    echo "Tunnel URL:  $URL"
    echo
    echo "Tail the log:   tail -f $LOG"
    echo "Stop:           kill $PID"
    echo
    wait $PID
    exit 0
  fi
done

echo "Tunnel did not register within 30 s. Check $LOG." >&2
exit 1
