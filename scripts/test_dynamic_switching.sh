#!/bin/bash
# test_dynamic_switching.sh - Test dynamic worker switching
#
# This script demonstrates the dynamic worker management by:
# 1. Submitting a Z-Image job
# 2. Submitting an SDXL job (triggers worker switch)
# 3. Submitting another Z-Image job (triggers worker switch back)

set -e

ORCHESTRATOR_URL=${ORCHESTRATOR_URL:-http://localhost:8080}

echo "========================================="
echo "Dynamic Worker Switching Test"
echo "========================================="
echo ""
echo "This test will:"
echo "1. Submit a Z-Image job"
echo "2. Submit an SDXL job (worker switch)"
echo "3. Submit another Z-Image job (worker switch back)"
echo ""
echo "Each worker switch takes approximately 30-90 seconds"
echo ""
read -p "Press Enter to continue..."
echo ""

# Test 1: Z-Image job
echo "========================================="
echo "Test 1: Z-Image Generation"
echo "========================================="
./scripts/submit_job.sh z-image '{"prompt": "A futuristic city at night", "steps": "20"}'
echo ""
echo "Waiting for job to complete..."
sleep 35
echo ""

# Test 2: SDXL job (triggers worker switch)
echo "========================================="
echo "Test 2: SDXL Generation (Worker Switch)"
echo "========================================="
echo "This will trigger a worker switch from z-image to sdxl"
echo "Expected wait: ~60 seconds (stop z-image + start sdxl)"
echo ""
./scripts/submit_job.sh sdxl-image-gen '{"prompt": "A serene mountain landscape", "width": "1024", "height": "1024"}'
echo ""
echo "Waiting for worker switch and job completion..."
sleep 90
echo ""

# Test 3: Z-Image job again (triggers switch back)
echo "========================================="
echo "Test 3: Z-Image Again (Worker Switch Back)"
echo "========================================="
echo "This will trigger a worker switch from sdxl back to z-image"
echo "Expected wait: ~55 seconds (stop sdxl + start z-image)"
echo ""
./scripts/submit_job.sh z-image '{"prompt": "An astronaut riding a horse", "steps": "20"}'
echo ""

echo "========================================="
echo "Test Complete!"
echo "========================================="
echo ""
echo "Check worker status with:"
echo "  ./scripts/worker_manager.py status"
echo ""
echo "View orchestrator logs:"
echo "  docker compose logs -f orchestrator"
