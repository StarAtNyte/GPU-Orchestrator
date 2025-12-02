#!/bin/bash
# Installation and testing script for SDXL Worker
#
# Usage: bash install_and_test.sh

set -e  # Exit on error

echo "==============================================="
echo "[SETUP] SDXL Worker Setup & Test"
echo "==============================================="

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Check prerequisites
echo -e "\n${YELLOW}Step 1: Checking prerequisites...${NC}"

# Check Python version
if ! command -v python3.11 &> /dev/null; then
    echo -e "${RED}[ERROR] Python 3.11 not found${NC}"
    echo "Install with: sudo apt install python3.11 python3.11-venv"
    exit 1
fi
echo -e "${GREEN}[OK] Python 3.11 found${NC}"

# Check NVIDIA GPU
if ! command -v nvidia-smi &> /dev/null; then
    echo -e "${RED}Warning: nvidia-smi not found. Running in CPU mode (very slow)${NC}"
else
    echo -e "${GREEN}[OK] NVIDIA GPU detected:${NC}"
    nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
fi

# Check Redis
if ! systemctl is-active --quiet redis; then
    echo -e "${RED}[ERROR] Redis is not running${NC}"
    echo "Start with: sudo systemctl start redis"
    exit 1
fi
echo -e "${GREEN}[OK] Redis is running${NC}"

# Step 2: Create virtual environment
echo -e "\n${YELLOW}Step 2: Creating virtual environment...${NC}"
cd "$(dirname "$0")"

if [ ! -d "venv" ]; then
    python3.11 -m venv venv
    echo -e "${GREEN}[OK] Virtual environment created${NC}"
else
    echo -e "${GREEN}[OK] Virtual environment already exists${NC}"
fi

# Activate virtual environment
source venv/bin/activate

# Step 3: Install dependencies
echo -e "\n${YELLOW}Step 3: Installing dependencies...${NC}"
echo "This may take 5-10 minutes (downloading ~4GB)..."

pip install --upgrade pip
pip install -r requirements.txt

echo -e "${GREEN}[OK] Dependencies installed${NC}"

# Step 4: Verify PyTorch GPU support
echo -e "\n${YELLOW}Step 4: Verifying PyTorch GPU support...${NC}"
python3 -c "
import torch
print(f'PyTorch version: {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'CUDA version: {torch.version.cuda}')
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    print(f'VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')
else:
    print('WARNING: Running in CPU mode')
"

# Step 5: Test Redis connection
echo -e "\n${YELLOW}Step 5: Testing Redis connection...${NC}"
cd ../..
python3 -c "
import sys
sys.path.insert(0, 'worker')
from shared.redis_client import RedisStreamClient

client = RedisStreamClient(stream_key='jobs:sdxl-test')
if client.ping():
    print('[OK] Redis connection successful')
else:
    print('[ERROR] Redis connection failed')
    sys.exit(1)
"

echo -e "\n${GREEN}===============================================${NC}"
echo -e "${GREEN}[SUCCESS] Setup complete!${NC}"
echo -e "${GREEN}===============================================${NC}"

echo -e "\n${YELLOW}To run the worker:${NC}"
echo "  cd worker/sdxl_worker"
echo "  source venv/bin/activate"
echo "  cd ../.."
echo "  python3 worker/sdxl_worker/main.py"

echo -e "\n${YELLOW}To test with a job:${NC}"
echo '  curl -X POST http://localhost:8080/submit \'
echo '    -H "Content-Type: application/json" \'
echo '    -d '"'"'{"app_id": "sdxl-local", "params": {"prompt": "A sunset over mountains"}}'"'"
