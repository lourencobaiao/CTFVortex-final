import subprocess
import time
import os
import json
from typing import Optional


class DockerEnvironmentManager:
    """Manages Docker container lifecycle for CTF testing"""

    def __init__(self, container_name: str, image_name: str,
                 ssh_port: int = 2222,
                 dockerfile_path: Optional[str] = None):
        self.container_name = container_name
        self.image_name = image_name
        self.ssh_port = ssh_port
        self.dockerfile_path = dockerfile_path

    def build_image(self) -> bool:
        """Build Docker image from Dockerfile"""
        if not self.dockerfile_path or not os.path.exists(self.dockerfile_path):
            print(f"⚠️  Dockerfile not found at {self.dockerfile_path}")
            return False

        print(f"🔨 Building Docker image: {self.image_name}")
        try:
            result = subprocess.run(
                ["docker", "build", "-t", self.image_name,
                 "-f", self.dockerfile_path, "."],
                capture_output=True,
                text=True,
                check=True
            )
            print(f"✅ Image built successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to build image: {e.stderr}")
            return False

    def container_exists(self) -> bool:
        """Check if container exists (running or stopped)"""
        result = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name={self.container_name}",
             "--format", "{{.Names}}"],
            capture_output=True,
            text=True
        )
        return self.container_name in result.stdout

    def is_container_running(self) -> bool:
        """Check if container is currently running"""
        result = subprocess.run(
            ["docker", "ps", "--filter", f"name={self.container_name}",
             "--format", "{{.Names}}"],
            capture_output=True,
            text=True
        )
        return self.container_name in result.stdout

    def stop_container(self) -> bool:
        """Stop the container if running"""
        if not self.is_container_running():
            print(f"ℹ️  Container {self.container_name} is not running")
            return True

        print(f" Stopping container: {self.container_name}")
        try:
            subprocess.run(
                ["docker", "stop", self.container_name],
                capture_output=True,
                check=True,
                timeout=10
            )
            print(f"✅ Container stopped")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to stop container: {e}")
            return False

    def remove_container(self) -> bool:
        """Remove the container"""
        if not self.container_exists():
            print(f"ℹ️  Container {self.container_name} does not exist")
            return True

        # Stop first if running
        self.stop_container()

        print(f" Removing container: {self.container_name}")
        try:
            subprocess.run(
                ["docker", "rm", "-f", self.container_name],
                capture_output=True,
                check=True
            )
            print(f"✅ Container removed")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to remove container: {e}")
            return False

    def start_container(self, detached: bool = True) -> bool:
        """Start a fresh container from the image"""
        print(f" Starting fresh container: {self.container_name}")

        cmd = [
            "docker", "run",
            "--name", self.container_name,
            "-p", f"{self.ssh_port}:22",
        ]

        if detached:
            cmd.append("-d")

        cmd.append(self.image_name)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True
            )

            # Wait for SSH to be ready
            print(f" Waiting for SSH service to start...")
            time.sleep(3)

            print(f" Container started successfully")
            print(f" SSH available at localhost:{self.ssh_port}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to start container: {e.stderr}")
            return False

    def reset_environment(self) -> bool:
        """Complete reset: stop, remove, and start fresh container"""
        print("\n" + "="*70)
        print("🔄 RESETTING DOCKER ENVIRONMENT")
        print("="*70)

        success = True

        # Stop and remove existing container
        if not self.remove_container():
            success = False

        # Start fresh container
        if not self.start_container():
            success = False

        if success:
            print("="*70)
            print("✅ Environment reset complete!")
            print("="*70 + "\n")
        else:
            print("="*70)
            print("❌ Environment reset failed")
            print("="*70 + "\n")

        return success

    def cleanup(self) -> bool:
        """Clean up: stop and remove container"""
        print("\n Cleaning up Docker environment...")
        return self.remove_container()

    def get_container_info(self) -> dict:
        """Get information about the container"""
        if not self.container_exists():
            return {"status": "not_found"}

        result = subprocess.run(
            ["docker", "inspect", self.container_name],
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            info = json.loads(result.stdout)[0]
            return {
                "status": info["State"]["Status"],
                "running": info["State"]["Running"],
                "pid": info["State"]["Pid"],
                "started_at": info["State"]["StartedAt"],
                "ports": info["NetworkSettings"]["Ports"]
            }
        return {"status": "error"}


def run_ctf_with_reset(agent_class, config: dict, docker_config: dict,
                       reset_before: bool = True, reset_after: bool = True):
    """
    Run CTF challenge with automatic Docker environment reset

    Args:
        agent_class: The CTF agent class to instantiate
        config: Configuration dictionary for the agent
        docker_config: Docker configuration
        reset_before: Reset environment before running
        reset_after: Reset environment after running
    """
    from ctf_agents.core.providers import make_default_provider
    from ctf_agents.core.connections import SSHTerminal

    # Create Docker manager
    docker_manager = DockerEnvironmentManager(
        container_name=docker_config["container_name"],
        image_name=docker_config["image_name"],
        ssh_port=docker_config["ssh_port"],
        dockerfile_path=docker_config.get("dockerfile_path")
    )

    try:
        # Reset environment before test
        if reset_before:
            if not docker_manager.reset_environment():
                print("❌ Failed to reset environment. Aborting.")
                return None

        # Create CTF components (Groq primary → OpenRouter fallback)
        ai_provider = make_default_provider(
            api_key=config["api_key"],
            model=config.get("model", None)
        )

        terminal = SSHTerminal(
            host=config["ssh_host"],
            port=config["ssh_port"],
            username=config["ssh_user"],
            password=config["ssh_pass"]
        )

        agent = agent_class(
            ai_provider=ai_provider,
            terminal=terminal,
            challenge_name=config["challenge_name"],
            challenge_description=config.get("challenge_description", ""),
            flag_format=config.get("flag_format", r"flag\{[^\}]+\}")
        )

        # Run the challenge
        result = agent.run(max_iterations=config.get("max_iterations", 30))

        return result

    finally:
        # Reset environment after test
        if reset_after:
            docker_manager.reset_environment()
