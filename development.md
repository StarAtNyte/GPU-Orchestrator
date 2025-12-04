Here is the proper Markdown code for your guide. I have organized the component list into a clean table and added syntax highlighting (`bash`, `go`) to the code blocks for better readability.

```markdown
# GPU Orchestrator Setup Guide

This guide walks you through setting up the **Professional GPU Orchestration System** environment on Ubuntu.

## 1. Overview: Installed Components

This section explains the technology stack and architectural decisions for the GPU Orchestrator system.

| Component | Purpose | Why We Chose It |
| :--- | :--- | :--- |
| **Go** | Orchestrator core | Go provides excellent concurrency primitives (goroutines), low memory footprint, and fast HTTP/gRPC performance. Perfect for routing and managing thousands of concurrent job requests. |
| **Python 3.11** | Worker runtime | Python is the de facto standard for ML/AI workloads. Version 3.11 offers significant performance improvements and is compatible with all major ML frameworks (PyTorch, TensorFlow, Transformers). |
| **CUDA Toolkit** | GPU inference | NVIDIA's CUDA is required for GPU-accelerated computing. It provides the low-level drivers and libraries that PyTorch and other frameworks use to communicate with NVIDIA GPUs. |
| **PyTorch, vLLM, diffusers** | ML inference frameworks | These are the actual ML libraries: PyTorch for general deep learning, vLLM for optimized LLM inference, and diffusers for Stable Diffusion models. |
| **Redis Streams** | Job queue | Redis Streams provide a persistent, ordered message queue with consumer groups. Unlike simple pub/sub, Streams guarantee message delivery even if a worker crashes mid-processing. The acknowledgment system ensures no job is lost. |
| **etcd** | Worker registry and distributed locking | etcd is a distributed key-value store used for service discovery (which workers are online?) and distributed locks (preventing two workers from grabbing the same GPU). It's more robust than Redis for coordination tasks. |
| **PostgreSQL + TimescaleDB** | Job logs and time-series GPU metrics | PostgreSQL stores job metadata, results, and logs. TimescaleDB extends it with time-series capabilities, perfect for storing continuous GPU metrics (temperature, utilization, memory) efficiently over time. |
| **Docker** | Container runtime for workers | Docker isolates worker environments, prevents dependency conflicts, and enables easy deployment. Each worker can have its own Python environment and CUDA version without affecting the host system. |
| **gRPC + Protobuf** | RPC communication between worker & orchestrator | Protobuf provides strongly-typed, schema-based serialization (faster and smaller than JSON). gRPC uses HTTP/2 for bidirectional streaming, allowing workers to report progress in real-time. |
| **Prometheus + Grafana** | Monitoring and metrics dashboards | Prometheus scrapes and stores metrics (jobs/sec, GPU utilization, queue depth). Grafana visualizes them in real-time dashboards. Essential for production observability. |

### **Architectural Decision: Why This Stack?**

**Problem:** We need to orchestrate GPU workloads across multiple machines, handle both local GPUs and cloud APIs, and ensure fault tolerance.

**Solution:**
- **Go orchestrator** acts as a lightweight, high-performance router
- **Redis Streams** decouple job submission from execution (clients don't wait for GPU processing)
- **Python workers** handle the heavy ML computation where Python's ecosystem is strongest
- **Protobuf** creates a language-agnostic contract between Go and Python
- **PostgreSQL/TimescaleDB** provide durable storage for auditing and analytics
- **Docker** enables horizontal scaling (spin up more workers as needed)

---

## 2. Step-by-step Installation

### Step 1: Update system packages

**What it does:** Refreshes the package index and upgrades all installed packages to their latest versions.

**Why it matters:** Ensures you have the latest security patches and compatible dependency versions before installing new software.

```bash
sudo apt update && sudo apt upgrade -y
```

---

### Step 2: Install Go (for orchestrator)

**What it does:** Installs the Go programming language compiler and runtime.

**Why we need it:** The orchestrator service is written in Go. We need the Go toolchain to compile and run the orchestrator code that handles HTTP requests, routes jobs to Redis, and manages the application registry.

**What happens behind the scenes:** This installs the `go` binary, standard library, and build tools. Go compiles to a single static binary with no runtime dependencies, making deployment simple.

```bash
sudo apt install -y golang-go
```

**Verify:**
```bash
go version
# Should output: go version go1.X.X linux/amd64
```

---

### Step 3: Install Python 3.11 (worker runtime)

**What it does:** Installs Python 3.11 interpreter, virtual environment tools, development headers, and pip package manager.

**Why we need it:**
- The GPU workers run Python code because all major ML frameworks (PyTorch, Transformers, Diffusers) are Python-based
- `python3.11-venv` allows isolated virtual environments for clean dependency management
- `python3.11-dev` provides C headers needed to compile native extensions (required by NumPy, PyTorch, etc.)
- `python3-pip` is the package installer for Python libraries

**Why Python 3.11 specifically:** Offers 10-60% performance improvements over 3.10, better error messages, and is the version most ML libraries are optimized for in 2025.

```bash
sudo apt install -y python3.11 python3.11-venv python3.11-dev python3-pip
```

**Verify:**
```bash
python3.11 --version  # Should show: Python 3.11.X
pip3 --version        # Should reference python 3.11
```

### Step 4: Install NVIDIA CUDA Toolkit (for GPU acceleration)

**What it does:** Installs NVIDIA's CUDA drivers, runtime libraries, and development tools.

**Why we need it:** CUDA is the foundation for all GPU computing on NVIDIA hardware. It provides:
- **Device drivers** that allow the OS to communicate with the GPU
- **cuDNN** (CUDA Deep Neural Network library) for optimized neural network operations
- **CUDA runtime** that PyTorch and TensorFlow use to execute code on the GPU

**What happens when you run ML code:**
1. Your Python code calls PyTorch
2. PyTorch uses CUDA to compile kernels (GPU programs)
3. CUDA sends these kernels to the GPU
4. GPU executes them in parallel across thousands of cores

**Without CUDA:** Your ML models would run on CPU only (10-100x slower).

Follow NVIDIA's official instructions for your Ubuntu version:
* [CUDA Toolkit installation guide](https://developer.nvidia.com/cuda-downloads)

**Verify:**
```bash
nvidia-smi
# Should show a table with GPU name, driver version, CUDA version, and current GPU usage
```

---

### Step 5: Install Redis (for job queues)

**What it does:** Installs Redis server and enables it as a system service.

**Why we need it:** Redis Streams act as our job queue:
- **Persistence:** Jobs survive system crashes (written to disk)
- **Consumer Groups:** Multiple workers can process jobs in parallel without duplicates
- **Acknowledgments:** If a worker crashes mid-job, the job is automatically reassigned
- **Ordering:** Jobs are processed in the order they arrive (FIFO)

**How it works in our system:**
1. Orchestrator receives HTTP request → serializes job to Protobuf → pushes to Redis Stream
2. Worker reads from stream → processes job → acknowledges completion
3. If worker crashes before ACK, Redis redelivers the job to another worker

**Alternative we didn't choose:** RabbitMQ (more complex), Kafka (overkill for single-node), or database polling (inefficient).

```bash
sudo apt install -y redis-server
sudo systemctl enable --now redis  # Start Redis and enable auto-start on boot
redis-cli ping                      # Should respond: PONG
```

---

### Step 6: Install etcd (for worker registry & locking)

**What it does:** Installs etcd, a distributed key-value store designed for coordination.

**Why we need it:**
- **Service Discovery:** Workers register themselves on startup (e.g., `worker-1: {gpu_id: 0, status: idle}`)
- **Health Checks:** Workers send periodic heartbeats; orchestrator removes dead workers
- **Distributed Locks:** Prevents two workers from grabbing the same GPU (critical for multi-GPU systems)
- **Configuration:** Store dynamic config (e.g., max_batch_size) without restarting services

**Why not just use Redis?** While Redis can do key-value storage, etcd provides:
- Stronger consistency guarantees (based on Raft consensus protocol)
- Built-in TTL and watch mechanisms for liveness detection
- Better suited for cluster coordination

**Example use case:** When a worker starts, it writes:
```
etcd.put("/workers/worker-1", {"status": "ready", "gpu": 0}, ttl=10s)
```
If the worker crashes, the key expires after 10 seconds, and the orchestrator knows not to send it jobs.

```bash
sudo apt install -y etcd
sudo systemctl enable --now etcd  # Start etcd and enable auto-start
```

### Step 7: Install PostgreSQL

**What it does:** Installs PostgreSQL database server and extension modules.

**Why we need it:** PostgreSQL provides persistent, structured storage for:
- **Job metadata:** Job ID, status (pending/running/completed/failed), timestamps
- **Job results:** Generated images, text outputs, model metadata
- **Audit logs:** Who submitted which job, when, and from where
- **User data:** API keys, quotas, billing information

**Why PostgreSQL over alternatives:**
- **MySQL:** PostgreSQL has better JSON support and complex query capabilities
- **MongoDB:** We need ACID transactions for financial/billing data
- **SQLite:** Not suitable for concurrent writes from multiple workers

**Database schema example:**
```sql
CREATE TABLE jobs (
  job_id UUID PRIMARY KEY,
  app_id TEXT,
  status TEXT,
  created_at TIMESTAMP,
  completed_at TIMESTAMP,
  result_path TEXT
);
```

```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl enable --now postgresql  # Start PostgreSQL and enable auto-start
sudo -i -u postgres psql -c '\l'        # List databases to verify installation
```

---

### Step 8: Install TimescaleDB (time-series extension for PostgreSQL)

**What it does:** Adds time-series optimization to PostgreSQL through an extension.

**Why we need it:** GPU metrics are time-series data (values recorded at regular intervals):
- GPU temperature every 5 seconds
- Memory usage every 10 seconds
- Job throughput per minute
- Queue depth every second

**What TimescaleDB provides:**
- **Automatic partitioning:** Data is split into chunks by time (e.g., one chunk per day)
- **Compression:** Old data is compressed to save 90% disk space
- **Fast queries:** Optimized for "last 24 hours" or "average over past week" queries
- **Retention policies:** Automatically delete data older than N days

**Example use case:**
```sql
-- Create hypertable (time-series optimized table)
CREATE TABLE gpu_metrics (
  time TIMESTAMPTZ NOT NULL,
  gpu_id INT,
  temp_celsius FLOAT,
  utilization_percent FLOAT
);

SELECT create_hypertable('gpu_metrics', 'time');

-- Query: Average GPU temp in last hour
SELECT avg(temp_celsius)
FROM gpu_metrics
WHERE time > NOW() - INTERVAL '1 hour';
```

**Why the complex installation?** TimescaleDB requires a GPG key verification to ensure package authenticity (security measure).

```bash
# Add TimescaleDB GPG key (verifies package authenticity)
curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/timescale-archive-keyring.gpg

# Add the TimescaleDB repository (adjust 'jammy' to your Ubuntu codename: focal, jammy, noble)
echo "deb [signed-by=/usr/share/keyrings/timescale-archive-keyring.gpg] https://packagecloud.io/timescale/timescaledb/ubuntu jammy main" | sudo tee /etc/apt/sources.list.d/timescaledb.list

# Update package index and install TimescaleDB
sudo apt update
sudo apt install -y timescaledb-2-postgresql-15

# Run automatic tuning wizard (configures memory, parallelism based on your hardware)
sudo timescaledb-tune --yes

# Restart PostgreSQL to load the TimescaleDB extension
sudo systemctl restart postgresql
```

**Verify:**
```bash
psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
# Should output: CREATE EXTENSION (or notice that it already exists)
```

### Step 9: Install Docker (for containerized workers)

**What it does:** Installs Docker engine, which runs containerized applications.

**Why we need it:**
- **Isolation:** Each worker runs in its own container with isolated file system, preventing dependency conflicts
- **Reproducibility:** Docker images guarantee the same Python/CUDA/library versions on every machine
- **GPU passthrough:** Docker can expose GPUs to containers using `--gpus` flag
- **Easy scaling:** `docker run` to add more workers, `docker stop` to remove them

**Why containerize workers (not the orchestrator)?**
- Orchestrator is lightweight (just Go binary) and doesn't have complex dependencies
- Workers have heavy ML libraries (PyTorch = 2GB+) that can conflict between projects
- Different workers might need different CUDA versions for different models

**Real-world benefit:** You can run SDXL workers with CUDA 11.8 and LLaMA workers with CUDA 12.1 on the same machine without conflicts.

```bash
sudo apt install -y docker.io
sudo systemctl enable --now docker  # Start Docker and enable auto-start
sudo usermod -aG docker $USER        # Add your user to 'docker' group (allows running Docker without sudo)
# ⚠️ IMPORTANT: Log out and log back in for group changes to take effect
```

**Verify:**
```bash
docker run hello-world
# Should download a test image and print "Hello from Docker!"

# Test GPU access in Docker:
docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
# Should show the same GPU table as running nvidia-smi on host
```

---

### Step 10: Install gRPC and Protobuf tools (for RPC definitions)

**What it does:** Installs code generators that compile `.proto` schema files into Go and Python code.

**Why we need it:** Protobuf defines the data contract between orchestrator and worker:
- **Schema definition:** We define message structure once in `worker.proto`
- **Code generation:** Tools generate serialization/deserialization code for both languages
- **Type safety:** Compiler checks ensure both sides use the same message format
- **Efficiency:** Binary serialization is 3-10x smaller and faster than JSON

**The workflow:**
1. Write `worker.proto` (defines JobRequest message structure)
2. Run `protoc` compiler to generate `worker_pb2.py` and `pb/worker.pb.go`
3. Import these generated files in your worker and orchestrator code
4. Serialize in Go: `proto.Marshal(pbJob)` → bytes
5. Deserialize in Python: `worker_pb2.JobRequest().ParseFromString(bytes)` → object

**Why gRPC vs REST/JSON?**
- **Performance:** Binary encoding is faster than text parsing
- **Type safety:** Catching bugs at compile time, not runtime
- **Streaming:** gRPC supports bidirectional streaming (for progress updates)
- **Language agnostic:** Same `.proto` file works for Go, Python, Rust, Java, etc.

**For Go:**
```bash
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest       # Generates message structs
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest      # Generates gRPC service stubs
```

**For Python:**
```bash
pip3 install grpcio grpcio-tools protobuf
# grpcio: Runtime library for gRPC communication
# grpcio-tools: Includes protoc compiler for Python
# protobuf: Runtime library for serialization
```

---

### Step 11: Install monitoring tools (Prometheus + Grafana)

**What it does:** Runs Prometheus (metrics storage) and Grafana (visualization) in Docker containers.

**Why we need it:**

**Prometheus** is a time-series database that scrapes metrics from services:
- **Pull-based:** Services expose `/metrics` endpoint, Prometheus scrapes them every 15s
- **PromQL:** Powerful query language for aggregations (e.g., "jobs per second by status")
- **Alerting:** Trigger alerts when GPU temp > 85°C or queue depth > 100

**Grafana** visualizes Prometheus data:
- **Dashboards:** Real-time graphs of GPU usage, job throughput, queue depth
- **Alerting:** Send Slack/email notifications when metrics exceed thresholds
- **Multi-source:** Can combine metrics from Prometheus, PostgreSQL, and custom sources

**What to monitor:**
- **Queue metrics:** Jobs pending, jobs/sec, average wait time
- **Worker metrics:** Active workers, jobs per worker, worker uptime
- **GPU metrics:** Temperature, utilization %, memory usage, power draw
- **System metrics:** CPU, RAM, disk I/O

**Typical dashboard panels:**
1. Jobs processed per minute (line chart)
2. Current queue depth (gauge)
3. GPU temperature across all GPUs (multi-line chart)
4. Job success/failure ratio (pie chart)

Refer to official guides or use Docker Compose (recommended for dev):

```bash
# Start Prometheus (metrics storage and querying)
docker run -d --name prometheus -p 9090:9090 prom/prometheus
# Access at: http://localhost:9090

# Start Grafana (visualization and dashboards)
docker run -d --name grafana -p 3000:3000 grafana/grafana
# Access at: http://localhost:3000 (default login: admin/admin)
```

**Next step after installation:** Configure Prometheus to scrape your orchestrator's `/metrics` endpoint and import GPU metrics dashboard in Grafana.

---

## 3. Verify Your Setup

*   Run `nvidia-smi` to verify GPU visibility.
*   Check Redis status with `redis-cli ping`.
*   Confirm etcd service status: `systemctl status etcd`.
*   Verify PostgreSQL + TimescaleDB extensions are working.
*   **Test Docker with GPU:**
    ```bash
    docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
    ```

---

## 4. Next Steps

*   Clone or create your orchestrator Go project.
*   Define protobuf RPC schemas.
*   Develop the Python worker consuming Redis streams.
*   Use Docker Compose or Kubernetes for orchestration.
*   Set up Prometheus and Grafana dashboards to monitor GPU metrics and job queues.
```

---

# 🏗️ Building the GPU Orchestrator System

**Goal:** Create a Unidirectional Data Flow (`Client` -> `Go Orchestrator` -> `Redis Queue` -> `Python Worker`) that is generic enough to handle both Local GPU jobs and future Cloud API proxies.

## Architecture Overview

**The Problem We're Solving:**
Traditional monolithic systems tightly couple the API server with the ML model execution. This creates bottlenecks:
- API requests block waiting for slow GPU processing (10+ seconds)
- Can't scale workers independently from the API
- Single point of failure (if GPU process crashes, the whole API goes down)

**Our Solution: Queue-Based Architecture**
```
Client → [HTTP] → Go Orchestrator → [Redis Stream] → Python Worker → [GPU]
                         ↓
                   [PostgreSQL]
                   (logs & results)
```

**Key Design Principles:**
1. **Decoupling:** Orchestrator and workers are separate processes, can restart independently
2. **Async Processing:** Client gets immediate `job_id`, polls for results later
3. **Horizontal Scaling:** Need more throughput? Start more worker containers
4. **Fault Tolerance:** Redis persistence ensures jobs survive crashes
5. **Extensibility:** Adding new models (LLMs, audio, video) requires only worker code changes

---

## 📂 1. Directory Structure

**What we're creating:** A multi-service project with shared protocol definitions.

**Why this structure?**
- **proto/**: Shared contract between Go and Python (single source of truth)
- **orchestrator/**: Go service (handles HTTP, routing, Redis publishing)
- **worker/**: Python service (consumes Redis, runs ML inference)

Open your terminal and create the project skeleton:

```bash
mkdir -p ~/gpu-orchestrator/{proto,orchestrator,worker}
cd ~/gpu-orchestrator
```

**Expected result:**
```
~/gpu-orchestrator/
├── proto/           # Protocol Buffer definitions
├── orchestrator/    # Go API server
└── worker/          # Python GPU worker
```

---

## 📜 2. Define the Protocol (Protobuf)

**The Challenge:** How do we pass job parameters from Go to Python when different ML models need different inputs?

**Bad approach (rigid schema):**
```protobuf
message JobRequest {
  string prompt = 1;
  int32 width = 2;      // Only works for image models
  int32 height = 3;     // What about LLMs? Audio models?
}
```

**Our approach (generic schema):**
We use a **Generic Parameter Map** (`map<string, string>`) instead of hardcoded fields. This allows the system to carry data for *any* app (SDXL, LLMs, Audio, etc.) without changing the schema.

**Why this works:**
- **Orchestrator** doesn't need to know what parameters mean (just forwards them)
- **Worker** is app-aware (knows how to parse parameters for each model)
- **Extensibility:** Adding a new model type doesn't require changing the protobuf schema

**File:** `proto/worker.proto`

```protobuf
syntax = "proto3";      // Use Protocol Buffers version 3 syntax

package worker;         // Namespace to avoid naming conflicts

option go_package = "./pb";  // Tell protoc where to put generated Go files

message JobRequest {
  string job_id = 1;         // Unique identifier (UUID) for tracking
  string app_id = 2;         // Which application/model? e.g., "sdxl-local", "gpt4-cloud", "whisper-api"
  string handler_type = 3;   // Routing hint: "local_gpu" or "cloud_http"

  // Generic params: The worker is responsible for casting strings to ints/floats
  // Example for SDXL: {"prompt": "sunset", "width": "1024", "height": "768"}
  // Example for LLM: {"prompt": "Explain quantum computing", "max_tokens": "500"}
  map<string, string> params = 4;
}
```

**Field breakdown:**

1. **job_id** (string): Unique identifier generated by orchestrator (UUID format)
   - Used for tracking job through its lifecycle
   - Client polls `/status/{job_id}` to check completion

2. **app_id** (string): Identifies which application/model to use
   - Examples: `"sdxl-local"`, `"llama3-local"`, `"gpt4-cloud"`
   - Orchestrator looks this up in `appRegistry` to determine routing

3. **handler_type** (string): How to execute this job
   - `"local_gpu"`: Push to Redis, let worker process on local GPU
   - `"cloud_http"`: Make HTTP request to cloud provider (Modal, Replicate, etc.)

4. **params** (map<string, string>): Generic key-value pairs
   - **Why strings?** JSON/HTTP naturally use strings; keeps protocol simple
   - **Worker's job:** Cast to appropriate types (`int(params["width"])`)
   - **Benefit:** No schema changes when adding new models

**Real-world example:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "app_id": "sdxl-local",
  "handler_type": "local_gpu",
  "params": {
    "prompt": "A cyberpunk city at night",
    "width": "1024",
    "height": "768",
    "num_inference_steps": "50"
  }
}
```

---

### Generate the Code

**What this does:** Compiles the `.proto` file into language-specific code.

**Generated files:**
- **Go:** `orchestrator/pb/worker.pb.go` (message structs + marshal/unmarshal functions)
- **Python:** `worker/worker_pb2.py` (message classes + serialization)

Run these commands to generate the bindings for both languages:

```bash
# 1. Generate Go code
# --go_out: Output directory for Go files
# --go_opt=paths=source_relative: Keep same directory structure as proto file
protoc --go_out=orchestrator --go_opt=paths=source_relative proto/worker.proto

# 2. Generate Python code
# -I.: Look for imports in current directory
# --python_out: Output directory for Python files
# --pyi_out: Generate type stub files for IDE autocomplete
python3 -m grpc_tools.protoc -I. --python_out=worker --pyi_out=worker proto/worker.proto
```

**What you should see:**
```
✅ orchestrator/pb/worker.pb.go created
✅ worker/worker_pb2.py created
✅ worker/worker_pb2.pyi created (type hints)
```

---

## 🧠 3. Build the Orchestrator (Go)

**Role:** The Orchestrator acts as a **Smart Router**. It receives HTTP requests, validates them, and routes jobs to the appropriate backend (local GPU queue or cloud API).

**Why Go for the orchestrator?**
- **Concurrency:** Goroutines handle thousands of simultaneous HTTP connections
- **Performance:** Low latency (< 1ms routing decision) and minimal memory overhead
- **Simplicity:** Single binary deployment, no runtime dependencies
- **Type safety:** Compile-time checks prevent runtime errors

**Orchestrator responsibilities:**
1. **Receive HTTP POST** requests with job parameters
2. **Validate** the `app_id` against the registry
3. **Generate** a unique `job_id` (UUID)
4. **Route** based on app configuration:
   - **Local:** Serialize to Protobuf → Push to Redis Stream
   - **Cloud:** Make HTTP request to external API (future implementation)
5. **Respond** immediately with `job_id` (don't wait for GPU processing)

---

### 3.1 Initialize Module

**What this does:** Creates a Go module and downloads required libraries.

**Dependencies explained:**
- **redis/go-redis/v9:** Redis client for pushing jobs to streams
- **protobuf/proto:** Serialization library (converts JobRequest struct → bytes)
- **google/uuid:** Generates unique identifiers (UUIDs) for jobs

```bash
cd ~/gpu-orchestrator/orchestrator
go mod init gpu-orchestrator                           # Initialize Go module
go get github.com/redis/go-redis/v9                   # Redis client
go get google.golang.org/protobuf/proto               # Protobuf runtime
go get github.com/google/uuid                          # UUID generator
```

**What you should see:**
```
go: creating new go.mod: module gpu-orchestrator
go: downloading github.com/redis/go-redis/v9...
```

---

### 3.2 Create Main Logic

**Code walkthrough:** The orchestrator has three main components:
1. **App Registry:** Configuration map defining where each app routes
2. **HTTP Handler:** Parses requests and routes jobs
3. **Main function:** Initializes Redis connection and starts HTTP server

**File:** `orchestrator/main.go`

```go
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
	"google.golang.org/protobuf/proto"
	"gpu-orchestrator/orchestrator/pb"
)

// Global Redis client (shared across all goroutines handling requests)
var rdb *redis.Client
var ctx = context.Background()  // Context for Redis operations

// --- App Registry ---
// This is the "brain" of the routing system. Each entry defines:
// 1. Where the job should go (local queue or cloud endpoint)
// 2. How to reach that destination
//
// DESIGN DECISION: Using a map[string]struct instead of a database allows:
// - Zero-latency lookups (no database query on every request)
// - Type-safe configuration (compiler catches typos)
// - Easy to migrate to config file later (JSON/YAML)
//
// FUTURE IMPROVEMENT: Load this from etcd for dynamic updates without restart

var appRegistry = map[string]struct {
	Type      string // "local" or "cloud" - determines the execution path
	QueueName string // If local: which Redis Stream to push to
	Endpoint  string // If cloud: HTTP endpoint for the cloud provider
}{
	// Example 1: Local GPU worker for Stable Diffusion XL
	// Jobs with app_id="sdxl-local" will be pushed to Redis Stream "jobs:sdxl"
	"sdxl-local": {
		Type:      "local",
		QueueName: "jobs:sdxl",
	},

	// Example 2: Cloud-based Whisper transcription (Modal.com or similar)
	// Jobs with app_id="whisper-cloud" will be proxied to external API
	"whisper-cloud": {
		Type:     "cloud",
		Endpoint: "https://api.modal.com/...",  // Replace with actual endpoint
	},
}

// HTTP Request Payload
// This struct defines what JSON the client must send
// Example request body:
// {
//   "app_id": "sdxl-local",
//   "params": {"prompt": "sunset", "width": "1024"}
// }
type SubmitRequest struct {
	AppID  string            `json:"app_id"`  // Which model/service to use
	Params map[string]string `json:"params"`  // Generic parameters (model-specific)
}

func main() {
	// 1. Connect to Redis
	// IMPORTANT: This creates a connection pool, not a single connection
	// Go's redis client automatically handles connection pooling and retries
	rdb = redis.NewClient(&redis.Options{Addr: "localhost:6379"})

	// Ping to verify connectivity (fails fast if Redis is down)
	if err := rdb.Ping(ctx).Err(); err != nil {
		log.Fatalf("❌ Redis connection failed: %v", err)
	}
	fmt.Println("✅ Connected to Redis")

	// 2. Register HTTP routes
	// Each request is handled in a separate goroutine (Go's concurrency model)
	http.HandleFunc("/submit", submitJobHandler)

	// 3. Start HTTP server
	// BLOCKING CALL: This runs forever until process is killed
	fmt.Println("🚀 Orchestrator running on :8080")
	log.Fatal(http.ListenAndServe(":8080", nil))
}

func submitJobHandler(w http.ResponseWriter, r *http.Request) {
	// STEP 1: Validate HTTP method
	// Only accept POST requests (RESTful convention for creating resources)
	if r.Method != http.MethodPost {
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// STEP 2: Parse JSON request body
	// json.NewDecoder streams the body (efficient for large payloads)
	var req SubmitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "Invalid JSON", http.StatusBadRequest)
		return
	}

	// STEP 3: Validate App ID against registry
	// This prevents typos and ensures only configured apps are used
	appConfig, exists := appRegistry[req.AppID]
	if !exists {
		http.Error(w, "Unknown App ID", http.StatusBadRequest)
		return
	}

	// STEP 4: Generate unique job ID
	// UUID v4 provides 128-bit randomness (collision probability ~= 0)
	jobID := uuid.New().String()  // e.g., "550e8400-e29b-41d4-a716-446655440000"

	// STEP 5: ROUTING LOGIC - This is the core decision point
	if appConfig.Type == "local" {
		// === PATH A: Local GPU Worker ===
		// Job will be processed by a Python worker consuming Redis Streams

		// Create Protobuf message
		// WHY PROTOBUF: 3-10x smaller than JSON, faster serialization
		pbJob := &pb.JobRequest{
			JobId:       jobID,
			AppId:       req.AppID,
			HandlerType: "local_gpu",
			Params:      req.Params,  // Map[string]string passes through directly
		}

		// Serialize to binary format
		// The byte slice can be transmitted over any protocol (Redis, gRPC, HTTP)
		data, err := proto.Marshal(pbJob)
		if err != nil {
			http.Error(w, "Proto marshal error", http.StatusInternalServerError)
			return
		}

		// Push to Redis Stream
		// XAdd is like "append to log" - messages are never deleted until explicitly trimmed
		// Stream name (e.g., "jobs:sdxl") acts as a topic/channel
		err = rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: appConfig.QueueName,  // e.g., "jobs:sdxl"
			Values: map[string]interface{}{"payload": data},  // Field name "payload" is arbitrary
		}).Err()

		if err != nil {
			http.Error(w, "Redis error", http.StatusInternalServerError)
			return
		}

		fmt.Printf("📥 [Local] Job %s queued for %s\n", jobID, req.AppID)

	} else {
		// === PATH B: Cloud/Hybrid (Future Implementation) ===
		// Job will be proxied to external API (Modal, Replicate, RunPod, etc.)
		fmt.Printf("☁️ [Cloud] Proxying job %s to %s\n", jobID, appConfig.Endpoint)

		// FUTURE CODE:
		// resp, err := http.Post(appConfig.Endpoint, "application/json", ...)
		// Store cloud job ID mapping for status polling
	}

	// STEP 6: Respond immediately (asynchronous pattern)
	// Client uses job_id to poll /status/{job_id} endpoint (not shown in this example)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{
		"job_id": jobID,
		"status": "queued",  // Status progression: queued → processing → completed/failed
	})
}
```

**Key architectural decisions explained:**

1. **Why immediate response?** GPU processing can take 10+ seconds. Blocking the HTTP connection wastes resources and limits concurrency. Async pattern allows client to do other work while waiting.

2. **Why Protobuf over JSON?**
   - JSON: ~500 bytes for typical job
   - Protobuf: ~150 bytes (70% reduction)
   - Matters when processing millions of jobs/day

3. **Why Redis Streams over simple Pub/Sub?**
   - Pub/Sub: If no worker is listening, message is lost
   - Streams: Messages persist until acknowledged
   - Consumer groups: Automatic load balancing across workers

4. **Why separate streams per app?**
   - Allows different worker types (SDXL workers only consume `jobs:sdxl`)
   - Enables per-app rate limiting and prioritization
   - Simplifies monitoring (queue depth per model type)

---

## 👷 4. Build the Worker (Python)

**Role:** The Worker is the **GPU Execution Engine**. It consumes jobs from Redis, deserializes them, loads the appropriate ML model, runs inference, and stores results.

**Why Python for workers?**
- **ML Ecosystem:** PyTorch, TensorFlow, Transformers, Diffusers are all Python-first
- **CUDA Integration:** Python bindings for CUDA are mature and well-tested
- **Development Speed:** Rapid iteration on model code (no compilation step)

**Worker characteristics:**
- **App-Aware:** Knows how to parse parameters for each model type
- **Type Casting:** Converts string parameters (from JSON/Proto) to appropriate types (int, float)
- **Consumer Groups:** Multiple workers can process jobs in parallel without duplicates
- **Fault Tolerant:** If worker crashes, job is automatically reassigned

**Worker lifecycle:**
1. **Connect** to Redis and join consumer group
2. **Poll** for new messages on specific stream (blocking read)
3. **Deserialize** Protobuf message
4. **Extract & Cast** parameters based on `app_id`
5. **Load Model** (if not already in memory)
6. **Run Inference** on GPU
7. **Store Result** (save image to disk, write to PostgreSQL)
8. **Acknowledge** job completion to Redis (removes from pending list)

---

### 4.1 Setup Environment

**What this does:** Creates an isolated Python environment and installs required libraries.

**Why virtual environment?**
- **Isolation:** Prevents conflicts with system Python packages
- **Reproducibility:** `pip freeze > requirements.txt` captures exact versions
- **Clean uninstall:** Just delete `venv/` folder to remove everything

**Dependencies:**
- **redis:** Python client for Redis (streams, pub/sub, etc.)
- **protobuf:** Runtime library for deserializing messages
- **grpcio-tools:** Only needed if regenerating Python code from `.proto` files

```bash
cd ~/gpu-orchestrator/worker
python3.11 -m venv venv              # Create virtual environment
source venv/bin/activate             # Activate it (prompt changes to show (venv))
pip install redis protobuf grpcio-tools  # Install dependencies

# Later, you'll also install:
# pip install torch torchvision diffusers transformers
# (Skipped for now since we're using mock inference)
```

---

### 4.2 Create Worker Logic

**Code walkthrough:** The worker implements a simple event loop:
1. Connect to Redis
2. Create consumer group (first worker creates it, others join)
3. Loop forever: read messages → process → acknowledge

**File:** `worker/main.py`

```python
import redis
import time
import sys
import os

# Ensure we can find the generated proto file
# This adds current directory to Python's import path
sys.path.append(os.getcwd())
import worker_pb2  # Generated by protoc from worker.proto

# === CONFIGURATION ===
# DESIGN DECISION: Hardcoded config for simplicity
# FUTURE IMPROVEMENT: Load from environment variables or config file

REDIS_HOST = "localhost"
STREAM_KEY = "jobs:sdxl"  # This worker is SPECIALIZED for SDXL jobs only
                          # You'd run separate workers for "jobs:llama", "jobs:whisper", etc.

GROUP_NAME = "gpu-workers"  # Consumer group name (shared across all SDXL workers)
                            # Redis ensures each message goes to only ONE worker in the group

CONSUMER_NAME = "worker-1"  # Unique identifier for this worker instance
                            # Use hostname or container ID in production


def connect_redis():
    """
    Establishes Redis connection and creates consumer group.

    Consumer Groups explained:
    - Multiple workers can join the same group
    - Each message is delivered to only ONE worker in the group (load balancing)
    - If a worker crashes, pending messages are reassigned
    - Workers outside the group don't interfere (can have monitoring consumers)
    """
    r = redis.Redis(host=REDIS_HOST, port=6379, decode_responses=False)
    # decode_responses=False: Keep byte strings (required for binary protobuf data)

    try:
        # Create Consumer Group
        # id="0": Start from the beginning of the stream
        # mkstream=True: Create stream if it doesn't exist yet
        r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
        print(f"✅ Consumer group '{GROUP_NAME}' created")
    except redis.exceptions.ResponseError as e:
        # BUSYGROUP error means group already exists (another worker created it)
        # This is expected and safe to ignore
        if "BUSYGROUP" not in str(e):
            raise e  # Re-raise if it's a different error

    return r


def process_job(payload):
    """
    Deserializes job, extracts parameters, and runs inference.

    This is where the "app-aware" logic lives:
    - Different app_ids have different parameter requirements
    - Worker casts strings to appropriate types for the ML framework
    """
    # STEP 1: Deserialize Protobuf
    job = worker_pb2.JobRequest()
    job.ParseFromString(payload)  # Binary → Python object

    print(f"\n⚙️  Processing Job: {job.job_id} | App: {job.app_id}")

    # STEP 2: Extract & Cast Parameters
    # This is the "Hybrid Logic" - flexible protocol, strict execution
    #
    # WHY THIS APPROACH:
    # - Protobuf map<string,string> is generic (works for any model)
    # - Python code knows what types each model needs
    # - Type errors are caught here, not in the orchestrator
    try:
        prompt = job.params.get("prompt", "")  # Default: empty string
        width = int(job.params.get("width", "512"))   # Default: 512
        height = int(job.params.get("height", "512"))

        print(f"   Model Args: Prompt='{prompt}', Size={width}x{height}")

        # STEP 3: Run inference
        # MOCK IMPLEMENTATION (replace with actual model code):
        #
        # from diffusers import StableDiffusionXLPipeline
        # import torch
        #
        # # Load model (cache globally to avoid reloading)
        # pipe = StableDiffusionXLPipeline.from_pretrained(
        #     "stabilityai/stable-diffusion-xl-base-1.0",
        #     torch_dtype=torch.float16
        # ).to("cuda")
        #
        # # Generate image
        # image = pipe(
        #     prompt=prompt,
        #     width=width,
        #     height=height
        # ).images[0]
        #
        # # Save result
        # output_path = f"/results/{job.job_id}.png"
        # image.save(output_path)

        time.sleep(2)  # Simulate 2-second inference time

        print(f"✅ Job {job.job_id} Completed!")

        # FUTURE: Write results to PostgreSQL
        # db.execute("UPDATE jobs SET status='completed', result_path=? WHERE job_id=?",
        #            (output_path, job.job_id))

    except ValueError as e:
        # Catch type conversion errors (e.g., width="abc")
        print(f"❌ Parameter Error: {e}")
        # FUTURE: Mark job as failed in database


def main():
    """
    Main event loop: connect → poll → process → acknowledge
    """
    r = connect_redis()
    print(f"👀 Worker listening on '{STREAM_KEY}'...")
    print(f"   Consumer: {CONSUMER_NAME} in group '{GROUP_NAME}'")

    while True:
        try:
            # XREADGROUP: Read messages from stream (blocking call)
            #
            # PARAMETERS:
            # - GROUP_NAME: Which consumer group this worker belongs to
            # - CONSUMER_NAME: Unique identifier for this worker
            # - {STREAM_KEY: ">"}: Read only NEW messages (not previously delivered)
            # - count=1: Read one message at a time (process immediately)
            # - block=2000: Wait up to 2 seconds for new messages (then retry)
            #
            # WHY BLOCKING READ:
            # - Efficient: No CPU waste polling empty queue
            # - Real-time: Processes jobs as soon as they arrive
            # - Backpressure: If worker is slow, orchestrator just queues more jobs

            entries = r.xreadgroup(
                GROUP_NAME,
                CONSUMER_NAME,
                {STREAM_KEY: ">"},
                count=1,
                block=2000  # milliseconds
            )

            if entries:
                # entries format: [(stream_name, [(message_id, {field: value})])]
                for stream, messages in entries:
                    for message_id, fields in messages:
                        # Process the job
                        process_job(fields[b'payload'])  # b'payload' is bytes

                        # ACKNOWLEDGE: Tell Redis this message was processed
                        # CRITICAL: Without this, message stays in "pending" state
                        # If worker crashes before ACK, Redis will redeliver to another worker
                        r.xack(STREAM_KEY, GROUP_NAME, message_id)

        except KeyboardInterrupt:
            print("\n🛑 Worker shutting down...")
            break
        except Exception as e:
            # Catch-all error handler
            # In production, log to file or monitoring system
            print(f"❌ Error: {e}")
            time.sleep(1)  # Avoid tight error loop


if __name__ == "__main__":
    main()
```

**Key design patterns explained:**

1. **Consumer Groups vs Simple Polling:**
   ```
   WITHOUT consumer groups (multiple workers):
   - Worker 1 reads message A
   - Worker 2 reads message A  ← DUPLICATE PROCESSING

   WITH consumer groups:
   - Worker 1 reads message A
   - Worker 2 reads message B  ← LOAD BALANCING
   ```

2. **Acknowledgment Pattern:**
   ```
   Process job → ACK

   If crash happens BEFORE ACK:
   - Message stays in "pending-entries list"
   - After timeout, Redis redelivers to another worker
   - Guarantees at-least-once processing
   ```

3. **String-to-Type Casting:**
   ```
   Why not use int32 in protobuf?

   Problem: JSON doesn't distinguish int vs float vs string
   Client sends: {"width": "1024"}  ← string from form input

   If proto uses int32:
   - Orchestrator must validate and cast
   - Couples orchestrator to model details (bad separation of concerns)

   Our approach:
   - Orchestrator blindly forwards strings
   - Worker casts based on model requirements
   - Easy to add new parameters without touching orchestrator
   ```

4. **Blocking Read vs Polling:**
   ```
   POLLING (inefficient):
   while True:
       jobs = redis.get("jobs")
       if jobs: process(jobs)
       time.sleep(0.1)  ← wastes 90% CPU checking empty queue

   BLOCKING READ (efficient):
   while True:
       jobs = redis.xreadgroup(..., block=2000)  ← sleeps until message arrives
       process(jobs)
   ```

---

## 5. Run & Test

**What we're testing:** The complete pipeline from HTTP request to job processing.

**Why 3 terminals?** Each service (Redis, Orchestrator, Worker) runs as a separate process. Keeping them in separate terminals lets you see their logs in real-time.

**Expected flow:**
```
Terminal 3 (Client)
   ↓ HTTP POST
Terminal 1 (Orchestrator) → "Job queued in Redis"
   ↓ Redis Stream
Terminal 2 (Worker) → "Job processing..." → "Job completed!"
```

---

### Terminal 1: Start Orchestrator

**What happens:**
1. Connects to Redis (verifies with PING)
2. Starts HTTP server on port 8080
3. Waits for incoming requests

```bash
cd ~/gpu-orchestrator/orchestrator
go run main.go

# Expected output:
# ✅ Connected to Redis
# 🚀 Orchestrator running on :8080
```

**If you see errors:**
- `Redis connection failed`: Check if Redis is running (`sudo systemctl status redis`)
- `address already in use`: Another process is using port 8080 (`lsof -i :8080`)

---

### Terminal 2: Start Worker

**What happens:**
1. Connects to Redis
2. Creates/joins consumer group `gpu-workers`
3. Enters blocking read loop (waiting for jobs)

```bash
cd ~/gpu-orchestrator/worker
source venv/bin/activate  # Activate virtual environment (if not already active)
python main.py

# Expected output:
# ✅ Consumer group 'gpu-workers' created
# 👀 Worker listening on 'jobs:sdxl'...
#    Consumer: worker-1 in group 'gpu-workers'
```

**If you see errors:**
- `ModuleNotFoundError: No module named 'worker_pb2'`: Run protoc to generate Python files
- `Connection refused`: Redis is not running

---

### Terminal 3: Submit a "Local" Job

**What this tests:**
- HTTP request parsing
- App ID validation
- Protobuf serialization
- Redis Stream publishing
- Worker consumption and processing

**Notice:** Parameters are strings (not integers). This is intentional and matches our flexible architecture.

```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "sdxl-local",
    "params": {
      "prompt": "Cyberpunk city in rain",
      "width": "1024",
      "height": "768"
    }
  }'
```

**Expected outputs:**

**Terminal 3 (curl):**
```json
{"job_id":"550e8400-e29b-41d4-a716-446655440000","status":"queued"}
```

**Terminal 1 (Orchestrator):**
```
📥 [Local] Job 550e8400-e29b-41d4-a716-446655440000 queued for sdxl-local
```

**Terminal 2 (Worker):**
```
⚙️  Processing Job: 550e8400-e29b-41d4-a716-446655440000 | App: sdxl-local
   Model Args: Prompt='Cyberpunk city in rain', Size=1024x768
✅ Job 550e8400-e29b-41d4-a716-446655440000 Completed!
```

**What just happened:**
1. Client → Orchestrator: HTTP POST with JSON
2. Orchestrator validates `app_id` exists in registry
3. Orchestrator generates UUID
4. Orchestrator serializes to Protobuf (JSON → binary)
5. Orchestrator pushes to Redis Stream `jobs:sdxl`
6. Worker receives message from stream
7. Worker deserializes Protobuf (binary → Python object)
8. Worker casts string parameters to integers
9. Worker simulates inference (2-second sleep)
10. Worker acknowledges message to Redis
11. Job removed from pending list

---

### Terminal 3: Submit a "Cloud" Job (Test Routing)

**What this tests:**
- Routing logic based on `app_id`
- Demonstrates extensibility (adding cloud providers)

This tests if your Orchestrator correctly identifies cloud apps. Since cloud integration isn't implemented yet, it will only print a log message (no Redis push).

```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "whisper-cloud",
    "params": {
      "audio_url": "https://example.com/audio.mp3"
    }
  }'
```

**Expected outputs:**

**Terminal 3 (curl):**
```json
{"job_id":"660f9500-f30c-52e5-b827-557766551111","status":"queued"}
```

**Terminal 1 (Orchestrator):**
```
☁️ [Cloud] Proxying job 660f9500-f30c-52e5-b827-557766551111 to https://api.modal.com/...
```

**Terminal 2 (Worker):**
```
(no output - worker only listens to jobs:sdxl stream)
```

**What this demonstrates:**
- Same HTTP interface for local and cloud jobs
- Orchestrator acts as a smart proxy
- Workers are specialized (SDXL worker ignores cloud jobs)
- Easy to add new backends without changing clients

---

### 🧪 Advanced Testing Scenarios

**Test 1: Invalid App ID**
```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{"app_id": "invalid", "params": {}}'

# Expected: HTTP 400 Bad Request
# Body: "Unknown App ID"
```

**Test 2: Invalid Parameters (Type Error)**
```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "sdxl-local",
    "params": {
      "prompt": "test",
      "width": "not-a-number",
      "height": "768"
    }
  }'

# Expected in Worker terminal:
# ❌ Parameter Error: invalid literal for int() with base 10: 'not-a-number'
```

**Test 3: Multiple Workers (Load Balancing)**

Open a 4th terminal and start a second worker:
```bash
cd ~/gpu-orchestrator/worker
source venv/bin/activate
# Change CONSUMER_NAME in main.py to "worker-2"
python main.py
```

Submit 5 jobs rapidly:
```bash
for i in {1..5}; do
  curl -X POST http://localhost:8080/submit \
    -H "Content-Type: application/json" \
    -d "{\"app_id\": \"sdxl-local\", \"params\": {\"prompt\": \"test $i\"}}"
done
```

**Expected:** Jobs are distributed across worker-1 and worker-2 (load balancing). Check both worker terminals to see them processing different jobs.

---

### Monitoring with Redis CLI

Open a 4th terminal to inspect the queue:

```bash
redis-cli

# View stream length
> XLEN jobs:sdxl
(integer) 5

# View pending jobs
> XPENDING jobs:sdxl gpu-workers
1) (integer) 0  # No pending jobs (all processed)
2) (integer) 5  # 5 jobs total
3) ...

# View consumer group info
> XINFO GROUPS jobs:sdxl
1) 1) "name"
   2) "gpu-workers"
   3) "consumers"
   4) (integer) 1  # Number of workers connected
   5) "pending"
   6) (integer) 0  # No unacknowledged messages
```

This lets you debug issues like:
- Jobs stuck in pending state (worker crashed)
- Queue growing faster than processing (need more workers)
- No workers connected (worker startup failed)


# Phase 2: State & Robustness - Testing Guide

Phase 2 adds critical infrastructure for production readiness:
- **etcd**: Worker registration and health monitoring
- **PostgreSQL**: Persistent job tracking and audit logs
- **Heartbeats**: Automatic detection of worker crashes
- **Status API**: Query job status from database

## What's New in Phase 2

### Orchestrator Enhancements
- ✅ PostgreSQL connection for job persistence
- ✅ etcd connection for worker discovery
- ✅ Background goroutine watching worker health
- ✅ New endpoints:
  - `GET /status/{job_id}` - Query job status
  - `GET /workers` - List active workers

### Worker Enhancements
- ✅ etcd registration on startup
- ✅ Background heartbeat thread (every 5 seconds)
- ✅ PostgreSQL connection for status updates
- ✅ Automatic status transitions: PENDING → QUEUED → PROCESSING → COMPLETED/FAILED

## Prerequisites

Make sure you have all dependencies installed:

```bash
# Go dependencies
cd orchestrator
go mod tidy

# Python dependencies
pip3 install redis protobuf etcd3 psycopg2-binary
```

## Testing Phase 2

### Option 1: Local Testing (3 Terminals)

This option runs services locally for development.

#### Terminal 1: Start Infrastructure

Start Redis, PostgreSQL, and etcd:

```bash
docker compose up redis postgres etcd
```

Wait for:
```
✅ PostgreSQL is ready
✅ Redis is ready
✅ etcd is ready
```

The `init.sql` script will automatically create tables on first run.

#### Terminal 2: Start Orchestrator

```bash
cd orchestrator
source ../.env  # Load environment variables
go run main.go
```

Expected output:
```
✅ Connected to Redis
✅ Connected to PostgreSQL
✅ Connected to etcd
👀 Watching for workers in etcd...
🚀 Orchestrator running on :8080
```

#### Terminal 3: Start Worker

```bash
cd worker
source ../.env
python3 main.py
```

Expected output:
```
🚀 Starting worker: <hostname>
✅ Connected to PostgreSQL
✅ Connected to etcd
✅ Registered as <hostname> in etcd
👀 Worker listening on 'jobs:sdxl'...
```

**In Terminal 2 (Orchestrator)**, you should see:
```
✅ Worker <hostname> is ONLINE
```

### Option 2: Full Docker Compose

Run everything in Docker (recommended for production-like testing):

```bash
docker compose up --build
```

This starts all services (redis, postgres, etcd, orchestrator, worker).

---

## Test Scenarios

### Test 1: Submit a Job and Track Its Status

**Submit job:**
```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "sdxl-local",
    "params": {
      "prompt": "A futuristic city at sunset",
      "width": "1024",
      "height": "768"
    }
  }'
```

**Response:**
```json
{"job_id":"550e8400-e29b-41d4-a716-446655440000","status":"queued"}
```

**Query job status immediately:**
```bash
curl http://localhost:8080/status/550e8400-e29b-41d4-a716-446655440000
```

**Response (while processing):**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "PROCESSING",
  "created_at": "2025-11-28T10:30:00Z"
}
```

**Query again after 2 seconds:**
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "COMPLETED",
  "created_at": "2025-11-28T10:30:00Z",
  "completed_at": "2025-11-28T10:30:02Z"
}
```

### Test 2: Worker Health Monitoring

**Check active workers:**
```bash
curl http://localhost:8080/workers
```

**Response:**
```json
{
  "count": 1,
  "workers": [
    {
      "WorkerID": "hostname",
      "LastHeartbeat": "2025-11-28T10:30:45Z",
      "Status": "ONLINE"
    }
  ]
}
```

**Simulate worker crash:**
1. Kill the worker process (Ctrl+C in Terminal 3)
2. Wait 10 seconds (lease TTL)
3. Check orchestrator logs:

```
❌ Worker hostname went OFFLINE
```

4. Query workers endpoint again:
```json
{
  "count": 0,
  "workers": []
}
```

### Test 3: Failed Job Handling

**Submit job with invalid parameters:**
```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "sdxl-local",
    "params": {
      "prompt": "test",
      "width": "not-a-number",
      "height": "768"
    }
  }'
```

**Query status:**
```bash
curl http://localhost:8080/status/{job_id}
```

**Response:**
```json
{
  "job_id": "...",
  "status": "FAILED",
  "error_log": "Parameter Error: invalid literal for int() with base 10: 'not-a-number'"
}
```

### Test 4: Multiple Workers (Load Balancing)

**Start a second worker:**
```bash
# Terminal 4
cd worker
WORKER_ID=worker-2 python3 main.py
```

**Orchestrator should show:**
```
✅ Worker worker-2 is ONLINE
```

**Submit 5 jobs rapidly:**
```bash
for i in {1..5}; do
  curl -X POST http://localhost:8080/submit \
    -H "Content-Type: application/json" \
    -d "{\"app_id\": \"sdxl-local\", \"params\": {\"prompt\": \"test $i\"}}"
done
```

**Check worker logs:**
- Jobs should be distributed across worker-1 and worker-2
- Redis Consumer Groups ensure no duplicate processing

---

## Database Inspection

### Connect to PostgreSQL

```bash
docker exec -it postgres psql -U postgres -d gpu_orchestrator
```

### Useful Queries

**View all jobs:**
```sql
SELECT id, app_id, status, created_at, completed_at, worker_id
FROM jobs
ORDER BY created_at DESC
LIMIT 10;
```

**Check job status distribution:**
```sql
SELECT status, COUNT(*) as count
FROM jobs
GROUP BY status;
```

**Find failed jobs:**
```sql
SELECT id, app_id, error_log
FROM jobs
WHERE status = 'FAILED';
```

**Average processing time:**
```sql
SELECT AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) as avg_seconds
FROM jobs
WHERE status = 'COMPLETED';
```

---

## etcd Inspection

### List all workers

```bash
docker exec -it etcd etcdctl get /workers/ --prefix
```

**Output:**
```
/workers/hostname
{"status": "ONLINE", "stream": "jobs:sdxl"}
```

### Watch worker changes in real-time

```bash
docker exec -it etcd etcdctl watch /workers/ --prefix
```

---

## Redis Inspection

### Connect to Redis CLI

```bash
docker exec -it redis redis-cli
```

### Check stream length

```
> XLEN jobs:sdxl
(integer) 5
```

### View pending jobs

```
> XPENDING jobs:sdxl sdxl-workers
```

### View consumer group info

```
> XINFO GROUPS jobs:sdxl
```

---

## Troubleshooting

### Orchestrator can't connect to PostgreSQL

**Error:** `PostgreSQL connection failed: connection refused`

**Solution:** Ensure PostgreSQL is running and accessible:
```bash
docker compose up postgres
# Wait for: "database system is ready to accept connections"
```

### Worker registration fails

**Error:** `etcd connection failed`

**Solution:** Check etcd is running:
```bash
docker compose up etcd
curl http://localhost:2379/version
```

### Jobs stuck in PROCESSING state

**Cause:** Worker crashed before updating status.

**Manual fix:**
```sql
UPDATE jobs
SET status = 'FAILED', error_log = 'Worker crashed'
WHERE status = 'PROCESSING'
  AND started_at < NOW() - INTERVAL '5 minutes';
```

**Future improvement:** Add orchestrator background job to auto-detect stale jobs.

---

## Next Steps: Phase 3

Once Phase 2 is working, you're ready for Phase 3:
- Cloud provider integration (Modal/RunPod)
- Hybrid routing logic
- Cost optimization
- Prometheus metrics
- Grafana dashboards

See `orchestration-architecture.md` for details.


# Phase 3: Hybrid Workloads Implementation - Deployment Guide

## Overview
Phase 3 implements the **App Registry** system that enables routing jobs to different backends based on configuration:
- **Local Workers**: GPU jobs running on local/edge GPUs (SDXL, etc.)
- **Cloud Workers**: Jobs routed to Modal, Replicate, or other cloud providers

## What's New in Phase 3

### 1. App Registry Configuration (`config/apps.yaml`)
- Define apps with their routing configuration
- Specify whether apps run locally or in the cloud
- Configure parameters, GPU requirements, and endpoints

### 2. Go Orchestrator Enhancements
- Loads app registry from YAML on startup
- Routes jobs based on app type (`local` vs `modal`)
- HTTP proxy for cloud endpoints with timeout and error handling
- Backward compatible with existing Phase 2 functionality

### 3. Modal Integration
- Panorama processor now has `/process` endpoint for orchestrator integration
- Supports both `rug_removal` and `outpaint` operations
- Standardized request/response format

## Deployment Steps

### Step 1: Deploy Panorama Processor to Modal

1. **Install Modal CLI** (if not already installed):
```bash
pip install modal
```

2. **Login to Modal**:
```bash
modal token new
```

3. **Create Modal Secret** for Hugging Face:
```bash
modal secret create huggingface HF_TOKEN=<your-hf-token>
```

4. **Deploy the Panorama Processor**:
```bash
modal deploy panorama_processor.py
```

5. **Get the Deployment URL**:
After deployment, Modal will show you the URL. It should look like:
```
https://your-org--panorama-processor.modal.run
```

6. **Update `config/apps.yaml`**:
Edit the `panorama-processor` app entry and replace the endpoint:
```yaml
- id: "panorama-processor"
  name: "360° Panorama Processor"
  type: "modal"
  endpoint: "https://your-org--panorama-processor.modal.run/process"  # ← Update this
```

### Step 2: Start the Local Orchestration System

1. **Ensure Environment Variables are Set** (`.env` file):
```bash
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password
POSTGRES_DB=gpu_orchestrator
```

2. **Build and Start All Services**:
```bash
docker compose up --build
```

This will start:
- ✅ Redis (message broker)
- ✅ PostgreSQL (job persistence)
- ✅ etcd (worker coordination)
- ✅ Orchestrator (with app registry loaded)
- ✅ GPU Worker (for local SDXL jobs)

3. **Verify App Registry Loaded**:
Check the orchestrator logs:
```bash
docker compose logs orchestrator | grep "Loaded app"
```

You should see:
```
✅ Loaded app: sdxl-image-gen (SDXL Image Generator) - Type: local
✅ Loaded app: panorama-processor (360° Panorama Processor) - Type: modal
✅ Loaded 2 apps from registry
```

## Testing

### Test 1: Local App (SDXL Image Generation)

Submit a job to the local SDXL worker:

```bash
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d '{
    "app_id": "sdxl-image-gen",
    "params": {
      "prompt": "A futuristic cityscape at sunset",
      "width": "1024",
      "height": "1024",
      "num_inference_steps": "50"
    }
  }'
```

**Expected Response**:
```json
{
  "job_id": "uuid-here",
  "status": "queued"
}
```

**Check Job Status**:
```bash
curl http://localhost:8080/status/<job_id>
```

### Test 2: Cloud App (Modal Panorama Processor)

Submit a job to the Modal panorama processor:

```bash
# First, encode an image to base64
IMAGE_BASE64=$(base64 -w 0 your_panorama.jpg)

# Submit the job
curl -X POST http://localhost:8080/submit \
  -H "Content-Type: application/json" \
  -d "{
    \"app_id\": \"panorama-processor\",
    \"params\": {
      \"operation\": \"outpaint\",
      \"image_data\": \"$IMAGE_BASE64\",
      \"prompt\": \"seamless natural environment continuation\",
      \"target_width\": \"2048\",
      \"target_height\": \"1024\",
      \"guidance_scale\": \"7.5\",
      \"num_inference_steps\": \"50\"
    }
  }"
```

**Check Logs**:
```bash
# Orchestrator logs
docker compose logs -f orchestrator

# Look for:
# ☁️ [Modal] Proxying job <uuid> to https://...modal.run/process
# ✅ [Modal] Job <uuid> sent to cloud endpoint
```

### Test 3: Check Active Workers

```bash
curl http://localhost:8080/workers
```

**Expected Response**:
```json
{
  "count": 1,
  "workers": [
    {
      "WorkerID": "gpu-worker-1",
      "LastHeartbeat": "2025-12-01T...",
      "Status": "ONLINE"
    }
  ]
}
```

## App Registry Management

### Adding a New Local App

Edit `config/apps.yaml`:

```yaml
- id: "your-new-app"
  name: "Your New App"
  type: "local"
  queue: "jobs:your-app"
  gpu_vram_gb: 24
  parameters:
    - name: "input"
      type: "string"
      required: true
```

Restart the orchestrator:
```bash
docker compose restart orchestrator
```

### Adding a New Cloud App

```yaml
- id: "replicate-whisper"
  name: "Whisper Transcription"
  type: "modal"  # or "replicate", "runpod", etc.
  endpoint: "https://your-endpoint.com/process"
  timeout_seconds: 600
  parameters:
    - name: "audio_url"
      type: "string"
      required: true
```

## Architecture Flow

```
Client Request
    ↓
POST /submit {app_id, params}
    ↓
Orchestrator reads App Registry
    ↓
    ├─ Type: "local" ──→ Push to Redis Stream ──→ Local GPU Worker
    │                                                     ↓
    │                                              Process with CUDA
    │                                                     ↓
    │                                              Update PostgreSQL
    │
    └─ Type: "modal" ──→ HTTP POST to Modal ──→ Modal H100 GPU
                                                         ↓
                                                  Process & Return
                                                         ↓
                                                  Update PostgreSQL
```

## Monitoring

### View All Jobs
```bash
docker exec -it postgres psql -U postgres -d gpu_orchestrator -c "SELECT id, app_id, status, created_at FROM jobs ORDER BY created_at DESC LIMIT 10;"
```

### Check Redis Streams
```bash
docker exec -it redis redis-cli XINFO STREAM jobs:sdxl
```

### Monitor Modal Logs
```bash
modal logs panorama-processor
```

## Troubleshooting

### Orchestrator Can't Load App Registry
**Symptom**: `❌ Failed to load app registry`

**Solution**:
1. Check that `config/apps.yaml` exists
2. Verify YAML syntax: `yamllint config/apps.yaml`
3. Check docker volume mount: `docker inspect orchestrator | grep Mounts`

### Modal Endpoint Returns 404
**Symptom**: `modal endpoint returned status 404`

**Solution**:
1. Verify Modal deployment: `modal app list`
2. Check the endpoint URL in `config/apps.yaml`
3. Test the endpoint directly:
   ```bash
   curl https://your-org--panorama-processor.modal.run/health
   ```

### Local Worker Not Processing Jobs
**Symptom**: Jobs stuck in QUEUED status

**Solution**:
1. Check worker is registered: `curl http://localhost:8080/workers`
2. Check Redis stream: `docker exec -it redis redis-cli XLEN jobs:sdxl`
3. View worker logs: `docker compose logs gpu-worker`

## Next Steps (Phase 4)

- [ ] Add Prometheus metrics for monitoring
- [ ] Build React/Next.js dashboard
- [ ] Implement KEDA autoscaling for workers
- [ ] Add support for more cloud providers (Replicate, RunPod)
- [ ] Implement job result caching
- [ ] Add WebSocket support for real-time job updates

## Configuration Reference

### App Registry Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | Yes | Unique app identifier |
| `name` | string | Yes | Human-readable name |
| `type` | string | Yes | `local` or `modal` |
| `queue` | string | If type=local | Redis stream name |
| `endpoint` | string | If type=modal | HTTP endpoint URL |
| `timeout_seconds` | integer | No | Request timeout (default: 300) |
| `gpu_vram_gb` | integer | No | Required GPU VRAM |
| `parameters` | array | No | Parameter definitions |

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_REGISTRY_PATH` | `./config/apps.yaml` | Path to app registry |
| `REDIS_URL` | `localhost:6379` | Redis connection string |
| `POSTGRES_HOST` | `localhost` | PostgreSQL host |
| `ETCD_ENDPOINT` | `localhost:2379` | etcd endpoint |

## Summary

Phase 3 successfully implements:
✅ YAML-based app registry
✅ Multi-backend routing (local + cloud)
✅ Modal integration with panorama processor
✅ HTTP proxy for cloud endpoints
✅ Backward compatibility with Phase 2

The system now supports hybrid workloads, allowing you to optimize costs by running lighter workloads locally while offloading heavy processing to cloud GPUs.
