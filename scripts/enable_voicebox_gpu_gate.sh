#!/usr/bin/env bash
# Make Voicebox refuse GPU-using requests (generate, transcribe, models/load,
# providers/start) while the GPU is in use. Voicebox itself is not modified; nginx asks
# the orchestrator (/gpu/gate) first.
#
# Run:  sudo bash scripts/enable_voicebox_gpu_gate.sh
# Undo: sudo bash scripts/enable_voicebox_gpu_gate.sh --undo
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/nginx/voicebox-gpu-gate.conf"
DEST=/etc/nginx/includes/voicebox-gpu-gate.conf
TARGET=/etc/nginx/includes/voicebox.conf
LINE="include /etc/nginx/includes/voicebox-gpu-gate.conf;"
STAMP=$(date +%Y%m%d-%H%M%S)

[[ $EUID -eq 0 ]] || { echo "Run with sudo."; exit 1; }
cp "$TARGET" "$TARGET.backup.$STAMP"

rollback() { echo "Rolling back..."; cp "$TARGET.backup.$STAMP" "$TARGET"; rm -f "$DEST"; nginx -t && systemctl reload nginx; }

if [[ "${1:-}" == "--undo" ]]; then
  sed -i "\|$LINE|d" "$TARGET"; rm -f "$DEST"
  nginx -t && systemctl reload nginx && echo "Gate removed."
  exit 0
fi

install -m 644 "$SRC" "$DEST"
grep -qF "$LINE" "$TARGET" || printf '\n# GPU gate (added %s)\n%s\n' "$STAMP" "$LINE" >> "$TARGET"

if ! nginx -t; then rollback; echo "nginx config test failed; nothing changed."; exit 1; fi
systemctl reload nginx
echo "Reloaded. Checking the gate..."

sleep 1
code=$(curl -sk -o /dev/null -w '%{http_code}' -X POST https://127.0.0.1/voicebox-api/generate -H 'Host: compute.explorug.online' || true)
echo "POST /voicebox-api/generate -> HTTP $code  (503 while the GPU is in use; anything else means it is free or passed through)"
curl -sk -o /dev/null -w 'GET  /voicebox-api/history  -> HTTP %{http_code}  (must still work)\n' https://127.0.0.1/voicebox-api/history -H 'Host: compute.explorug.online' || true
echo "Backup: $TARGET.backup.$STAMP"
