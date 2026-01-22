#!/bin/bash
# Cleanup orphaned GPU processes from stopped containers

echo "Checking for orphaned GPU processes..."

# Get all GPU process PIDs
GPU_PIDS=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)

for pid in $GPU_PIDS; do
    # Check if process exists
    if ! ps -p $pid > /dev/null 2>&1; then
        continue
    fi

    # Check if it's a Python worker process
    if ps -p $pid -o cmd= | grep -q "/app/worker/main.py"; then
        # Check if its container is still running
        CONTAINER_RUNNING=false
        for container in sdxl-worker z-image-worker; do
            if docker ps --format '{{.Names}}' | grep -q "^${container}$"; then
                # Check if PID is in this running container
                if docker top $container | grep -q " $pid "; then
                    CONTAINER_RUNNING=true
                    break
                fi
            fi
        done

        if [ "$CONTAINER_RUNNING" = false ]; then
            echo "Found orphaned GPU process: PID $pid (killing...)"
            kill -9 $pid 2>/dev/null || true
        fi
    fi
done

echo "Cleanup complete"
