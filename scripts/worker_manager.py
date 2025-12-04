#!/usr/bin/env python3
"""
Exclusive Worker Manager
Ensures only one GPU worker is active at a time
"""

import subprocess
import time
import yaml
import sys
import logging
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class ExclusiveWorkerManager:
    def __init__(self, config_path="/app/config/workers.yaml"):
        """Initialize worker manager with configuration"""
        with open(config_path) as f:
            self.config = yaml.safe_load(f)

        self.workers = self.config['workers']
        self.settings = self.config['settings']
        self.state_file = Path("/tmp/gpu_orchestrator_active_worker.txt")
        self.project_name = "gpuorchestrator"
        self.compose_file = "/app/docker-compose.yml"

    def get_active_worker(self):
        """Get currently active worker name from state file"""
        if not self.state_file.exists():
            return None

        worker_name = self.state_file.read_text().strip()

        # Verify worker is actually running
        if self.is_worker_running(worker_name):
            return worker_name
        else:
            # State file is stale, clean it up
            self.state_file.unlink(missing_ok=True)
            return None

    def set_active_worker(self, worker_name):
        """Mark worker as active in state file"""
        if worker_name:
            self.state_file.write_text(worker_name)
        else:
            self.state_file.unlink(missing_ok=True)

    def is_worker_running(self, worker_name):
        """Check if Docker container is running"""
        try:
            result = subprocess.run(
                ['docker', 'compose', '-p', self.project_name, '-f', self.compose_file, 'ps', '-q', worker_name],
                capture_output=True,
                text=True,
                check=True
            )
            return bool(result.stdout.strip())
        except subprocess.CalledProcessError:
            return False

    def stop_worker(self, worker_name):
        """Stop a worker container"""
        if not worker_name:
            return True

        logger.info(f"Stopping {worker_name}...")

        try:
            # Stop the container (stop command doesn't need --no-deps, but using it for consistency)
            subprocess.run(
                ['docker', 'compose', '-p', self.project_name, '-f', self.compose_file, 'stop', worker_name],
                check=True,
                timeout=self.settings['max_shutdown_wait']
            )

            # Wait for graceful shutdown
            worker_config = self.workers.get(worker_name, {})
            shutdown_time = worker_config.get('shutdown_time_seconds', 10)
            time.sleep(shutdown_time)

            logger.info(f"✓ {worker_name} stopped")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"✗ {worker_name} shutdown timeout - forcing stop")
            subprocess.run(['docker', 'compose', '-p', self.project_name, '-f', self.compose_file, 'kill', worker_name])
            return False
        except subprocess.CalledProcessError as e:
            logger.error(f"✗ Failed to stop {worker_name}: {e}")
            return False

    def start_worker(self, worker_name):
        """Start a worker container"""
        logger.info(f"Starting {worker_name}...")

        try:
            # Start the container (--no-deps prevents recreating dependencies)
            subprocess.run(
                ['docker', 'compose', '-p', self.project_name, '-f', self.compose_file, 'up', '-d', '--no-deps', worker_name],
                check=True
            )

            # Wait for startup
            worker_config = self.workers.get(worker_name, {})
            startup_time = worker_config.get('startup_time_seconds', 30)

            logger.info(f"Waiting {startup_time}s for {worker_name} to initialize...")
            time.sleep(startup_time)

            # Verify it's running
            if self.is_worker_running(worker_name):
                logger.info(f"✓ {worker_name} started successfully")
                return True
            else:
                logger.error(f"✗ {worker_name} failed to start")
                return False

        except subprocess.CalledProcessError as e:
            logger.error(f"✗ Failed to start {worker_name}: {e}")
            return False

    def switch_worker(self, target_worker):
        """Switch from current worker to target worker"""
        active_worker = self.get_active_worker()

        # Already running the correct worker
        if active_worker == target_worker:
            logger.info(f"✓ {target_worker} already active")
            return True

        # Stop current worker if any
        if active_worker:
            logger.info(f"Switching from {active_worker} to {target_worker}")
            if not self.stop_worker(active_worker):
                logger.error("Failed to stop current worker")
                return False
            self.set_active_worker(None)
        else:
            logger.info(f"No active worker, starting {target_worker}")

        # Start target worker
        if self.start_worker(target_worker):
            self.set_active_worker(target_worker)
            return True
        else:
            return False

    def stop_all_workers(self):
        """Stop all GPU workers"""
        logger.info("Stopping all workers...")

        for worker_name in self.workers.keys():
            if self.is_worker_running(worker_name):
                self.stop_worker(worker_name)

        self.set_active_worker(None)
        logger.info("✓ All workers stopped")

    def get_worker_for_app(self, app_id):
        """Get worker name for a given app_id"""
        for worker_name, config in self.workers.items():
            if config['app_id'] == app_id:
                return worker_name
        return None

    def status(self):
        """Print current status"""
        active_worker = self.get_active_worker()

        print("\n=== GPU Worker Status ===")
        print(f"Mode: Exclusive (one worker at a time)")
        print(f"Active Worker: {active_worker or 'None'}")
        print("\nConfigured Workers:")

        for worker_name, config in self.workers.items():
            running = self.is_worker_running(worker_name)
            status_icon = "🟢" if running else "⚫"
            active_marker = " ← ACTIVE" if worker_name == active_worker else ""

            print(f"  {status_icon} {worker_name}")
            print(f"     App: {config['app_id']}")
            print(f"     VRAM: {config['vram_required_gb']}GB")
            print(f"     Status: {'Running' if running else 'Stopped'}{active_marker}")

        print()


def main():
    """CLI interface"""
    if len(sys.argv) < 2:
        print("Usage:")
        print("  worker_manager.py switch <worker-name>   - Switch to specific worker")
        print("  worker_manager.py app <app-id>           - Switch to worker for app")
        print("  worker_manager.py stop                   - Stop all workers")
        print("  worker_manager.py status                 - Show current status")
        print("\nExamples:")
        print("  worker_manager.py switch z-image-worker")
        print("  worker_manager.py app sdxl-image-gen")
        print("  worker_manager.py status")
        sys.exit(1)

    manager = ExclusiveWorkerManager()
    command = sys.argv[1]

    if command == "switch":
        if len(sys.argv) < 3:
            print("Error: worker name required")
            sys.exit(1)

        worker_name = sys.argv[2]
        if worker_name not in manager.workers:
            print(f"Error: Unknown worker '{worker_name}'")
            print(f"Available: {', '.join(manager.workers.keys())}")
            sys.exit(1)

        success = manager.switch_worker(worker_name)
        sys.exit(0 if success else 1)

    elif command == "app":
        if len(sys.argv) < 3:
            print("Error: app_id required")
            sys.exit(1)

        app_id = sys.argv[2]
        worker_name = manager.get_worker_for_app(app_id)

        if not worker_name:
            print(f"Error: No worker configured for app '{app_id}'")
            sys.exit(1)

        success = manager.switch_worker(worker_name)
        sys.exit(0 if success else 1)

    elif command == "stop":
        manager.stop_all_workers()
        sys.exit(0)

    elif command == "status":
        manager.status()
        sys.exit(0)

    else:
        print(f"Error: Unknown command '{command}'")
        sys.exit(1)


if __name__ == "__main__":
    main()
