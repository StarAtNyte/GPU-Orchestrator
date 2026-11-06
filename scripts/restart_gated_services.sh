#!/usr/bin/env bash
# Restart the GPU-gated services so the new "only run when the GPU is free" code takes effect.
# Needs sudo (both are systemd units). Run:  sudo bash scripts/restart_gated_services.sh
set -euo pipefail

echo "== syntax check =="
python3 -m py_compile /home/at-office/Projects/Nitiz/GalainchaRAG/server_app.py
python3 -m py_compile /home/at-office/Projects/Nitiz/exam-ocr/glm_exam_app.py
echo ok

for svc in galaincha-rag exam-ocr; do
  echo "== restarting $svc =="
  systemctl restart "$svc"
done

sleep 8
for svc in galaincha-rag exam-ocr; do
  printf '%-14s ' "$svc"; systemctl is-active "$svc"
done

echo "== GPU status endpoints (expect JSON with is_available) =="
curl -s -m 5 http://127.0.0.1:8787/api/gpu/status; echo
curl -s -m 5 http://127.0.0.1:8765/api/gpu/status; echo
echo "Done. If a service is not 'active', run: journalctl -u <name> -n 50 --no-pager"
