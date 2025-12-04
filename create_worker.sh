#!/bin/bash
# create_worker.sh - Easy worker creation script
#
# Usage: ./create_worker.sh <worker-name>
# Example: ./create_worker.sh whisper

set -e

if [ $# -lt 1 ]; then
    echo "Usage: ./create_worker.sh <worker-name>"
    echo ""
    echo "Example: ./create_worker.sh whisper"
    echo "         ./create_worker.sh llama"
    echo "         ./create_worker.sh flux"
    exit 1
fi

WORKER_NAME=$1
WORKER_DIR="${WORKER_NAME}_worker"
APP_ID="${WORKER_NAME}"
QUEUE_NAME="jobs:${WORKER_NAME}"

# Convert to PascalCase for class name
CLASS_NAME=$(echo "$WORKER_NAME" | sed 's/-/ /g' | sed 's/_/ /g' | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2))}1' | sed 's/ //g')

echo "========================================="
echo "Creating New Worker: $WORKER_NAME"
echo "========================================="
echo "Worker Directory: worker/$WORKER_DIR"
echo "App ID: $APP_ID"
echo "Queue: $QUEUE_NAME"
echo "Class Name: ${CLASS_NAME}Handler"
echo ""

# Create worker directory
mkdir -p "worker/$WORKER_DIR"

# Copy and process templates
echo "[1/5] Creating handler.py..."
sed -e "s/{{APP_NAME}}/$WORKER_NAME/g" \
    -e "s/{{CLASS_NAME}}/$CLASS_NAME/g" \
    -e "s/{{APP_ID}}/$APP_ID/g" \
    templates/worker/handler.py.template > "worker/$WORKER_DIR/handler.py"

echo "[2/5] Creating main.py..."
sed -e "s/{{APP_NAME}}/$WORKER_NAME/g" \
    -e "s/{{CLASS_NAME}}/$CLASS_NAME/g" \
    -e "s/{{APP_ID}}/$APP_ID/g" \
    -e "s/{{QUEUE_NAME}}/$QUEUE_NAME/g" \
    templates/worker/main.py.template > "worker/$WORKER_DIR/main.py"

echo "[3/5] Creating Dockerfile..."
sed -e "s/{{APP_NAME}}/$WORKER_NAME/g" \
    -e "s/{{WORKER_DIR}}/$WORKER_DIR/g" \
    templates/worker/Dockerfile.template > "worker/$WORKER_DIR/Dockerfile"

echo "[4/5] Creating requirements.txt..."
cp templates/worker/requirements.txt.template "worker/$WORKER_DIR/requirements.txt"

echo "[5/5] Creating .gitkeep for models..."
mkdir -p "worker/$WORKER_DIR/models"
touch "worker/$WORKER_DIR/models/.gitkeep"

echo ""
echo "========================================="
echo "Worker Created Successfully!"
echo "========================================="
echo ""
echo "Next Steps:"
echo ""
echo "1. Edit worker/$WORKER_DIR/handler.py"
echo "   - Implement your model loading logic"
echo "   - Implement the process() method"
echo ""
echo "2. Update worker/$WORKER_DIR/requirements.txt"
echo "   - Add your model-specific dependencies"
echo ""
echo "3. Add to config/apps.yaml:"
echo ""
cat <<EOF
  - id: "$APP_ID"
    name: "$WORKER_NAME Worker"
    type: "local"
    queue: "$QUEUE_NAME"
    description: "Description of your worker"
    gpu_vram_gb: 12
    parameters:
      - name: "input"
        type: "string"
        required: true
        description: "Your input parameter"
EOF
echo ""
echo "4. Add to config/workers.yaml:"
echo ""
cat <<EOF
  ${WORKER_NAME}-worker:
    app_id: "$APP_ID"
    queue: "$QUEUE_NAME"
    vram_required_gb: 12
    startup_time_seconds: 30
    shutdown_time_seconds: 10
EOF
echo ""
echo "5. Add to docker-compose.yml:"
echo ""
cat <<EOF
  ${WORKER_NAME}-worker:
    build:
      context: .
      dockerfile: worker/${WORKER_DIR}/Dockerfile
    container_name: ${WORKER_NAME}-worker
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
      etcd:
        condition: service_started
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
      - POSTGRES_HOST=postgres
      - POSTGRES_USER=\${POSTGRES_USER}
      - POSTGRES_PASSWORD=\${POSTGRES_PASSWORD}
      - POSTGRES_DB=\${POSTGRES_DB}
      - ETCD_HOST=etcd
      - ETCD_PORT=2379
      - WORKER_ID=${WORKER_NAME}-worker-1
      - MODEL_DIR=/models
      - HF_TOKEN=\${HF_TOKEN}
    volumes:
      - ./worker/shared:/app/shared:ro
      - ${WORKER_NAME}-models:/models
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              capabilities: [gpu]
EOF
echo ""
echo "6. Add volume to docker-compose.yml volumes section:"
echo "   ${WORKER_NAME}-models:"
echo ""
echo "7. Build and test:"
echo "   docker compose build ${WORKER_NAME}-worker"
echo "   docker compose up -d ${WORKER_NAME}-worker"
echo ""
echo "8. Submit a test job:"
echo "   ./scripts/submit_job.sh $APP_ID '{\"input\": \"test\"}'"
echo ""
echo "========================================="
