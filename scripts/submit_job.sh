#!/bin/bash
# submit_job.sh - Submit job with automatic worker switching
#
# Usage:
#   ./submit_job.sh <app_id> '<json_params>'
#
# Examples:
#   ./submit_job.sh z-image '{"prompt": "A cat in space", "steps": "20"}'
#   ./submit_job.sh sdxl-image-gen '{"prompt": "A beautiful sunset", "width": "1024", "height": "1024"}'

set -e

if [ $# -lt 2 ]; then
    echo "Usage: $0 <app_id> '<json_params>'"
    echo ""
    echo "Examples:"
    echo "  $0 z-image '{\"prompt\": \"A cat\", \"steps\": \"20\"}'"
    echo "  $0 sdxl-image-gen '{\"prompt\": \"A dog\", \"width\": \"1024\"}'"
    exit 1
fi

APP_ID=$1
PARAMS=$2
ORCHESTRATOR_URL=${ORCHESTRATOR_URL:-http://localhost:8080}

# Prepare JSON payload
PAYLOAD=$(cat <<EOF
{
  "app_id": "$APP_ID",
  "params": $PARAMS
}
EOF
)

echo "========================================="
echo "Submitting job to orchestrator"
echo "========================================="
echo "App ID: $APP_ID"
echo "Orchestrator: $ORCHESTRATOR_URL"
echo "Params: $PARAMS"
echo ""
echo "Note: The orchestrator will automatically switch to the correct worker"
echo "      This may take 30-90 seconds on first request."
echo ""

# Submit job
echo "Submitting job..."
RESPONSE=$(curl -s -X POST "$ORCHESTRATOR_URL/submit" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD")

# Parse job_id from response
JOB_ID=$(echo "$RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)

if [ -z "$JOB_ID" ]; then
    echo "Error: Failed to submit job"
    echo "Response: $RESPONSE"
    exit 1
fi

echo "✓ Job submitted successfully!"
echo "Job ID: $JOB_ID"
echo ""
echo "Check status with:"
echo "  curl $ORCHESTRATOR_URL/status/$JOB_ID"
echo ""
echo "Or use the monitor script:"
echo "  ./scripts/monitor_job.sh $JOB_ID"
