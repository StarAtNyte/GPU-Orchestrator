#!/bin/bash
# monitor_job.sh - Monitor job status in real-time
#
# Usage:
#   ./monitor_job.sh <job_id>

set -e

if [ $# -lt 1 ]; then
    echo "Usage: $0 <job_id>"
    echo ""
    echo "Example:"
    echo "  $0 abc123-def456-ghi789"
    exit 1
fi

JOB_ID=$1
ORCHESTRATOR_URL=${ORCHESTRATOR_URL:-http://localhost:8080}

echo "========================================="
echo "Monitoring Job: $JOB_ID"
echo "========================================="
echo ""

PREV_STATUS=""

while true; do
    # Get job status
    RESPONSE=$(curl -s "$ORCHESTRATOR_URL/status/$JOB_ID")

    # Parse status
    STATUS=$(echo "$RESPONSE" | grep -o '"status":"[^"]*"' | cut -d'"' -f4)

    if [ -z "$STATUS" ]; then
        echo "Error: Job not found or invalid response"
        echo "Response: $RESPONSE"
        exit 1
    fi

    # Only print if status changed
    if [ "$STATUS" != "$PREV_STATUS" ]; then
        TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
        echo "[$TIMESTAMP] Status: $STATUS"
        PREV_STATUS=$STATUS
    fi

    # Check if job is complete
    if [ "$STATUS" = "COMPLETED" ]; then
        echo ""
        echo "========================================="
        echo "Job Completed Successfully!"
        echo "========================================="
        echo ""
        echo "Full response:"
        echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
        break
    elif [ "$STATUS" = "FAILED" ]; then
        echo ""
        echo "========================================="
        echo "Job Failed"
        echo "========================================="
        echo ""
        echo "Full response:"
        echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
        exit 1
    fi

    # Wait before checking again
    sleep 2
done
