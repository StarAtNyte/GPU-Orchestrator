#!/bin/bash
set -e

echo "=== Rebuilding main-dashboard container ==="
cd /home/at-office/Projects/Nitiz/GPU-Polling/frontend
docker compose up -d --build main-dashboard

echo ""
echo "=== Done ==="
echo "  • Main dashboard: https://compute.explorug.online/gpu-polling/"
echo "  • Qwen3.5 Chat:   https://compute.explorug.online/gpu-polling/qwen35-chat/"
echo "  • compute-root index.html already updated (static file, no rebuild needed)"
