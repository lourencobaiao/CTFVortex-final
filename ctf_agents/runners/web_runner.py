"""End-to-end orchestrator for web-based CTF challenges."""

import os
import time
import shutil
import stat
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional

from ctf_agents.config import DEFAULT_MODEL
from ctf_agents.pcg.website_generator import CTFWebsiteGenerator
from ctf_agents.infra.docker_manager import DockerEnvironmentManager
from ctf_agents.core.providers import make_default_provider
from ctf_agents.core.connections import SSHTerminal
from ctf_agents.core.web_agent import WebCTFAgent
from ctf_agents.reporting.engine import save_report


class WebCTFRunner:
    """Orchestrates the complete web CTF workflow:
    generate challenge → deploy in Docker → run AI agent → analyse results."""

    def __init__(self, api_key: str, model: str = None):
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.website_gen = CTFWebsiteGenerator(output_dir="./ctf_websites")

        self.docker_config = {
            "container_name": "web-ctf-sandbox",
            "image_name": "webctf:latest",
            "ssh_port": 2222,
            "web_port": 8080,
        }

        self.docker_manager = DockerEnvironmentManager(
            container_name=self.docker_config["container_name"],
            image_name=self.docker_config["image_name"],
            ssh_port=self.docker_config["ssh_port"],
        )

    # ------------------------------------------------------------------
    # Docker image
    # ------------------------------------------------------------------

    def build_docker_image(self) -> bool:
        """Build the Docker image for web CTF challenges."""
        print(" Building Docker image for web CTF...")
        self._create_docker_files()

        try:
            subprocess.run(
                ["docker", "build", "-t", self.docker_config["image_name"],
                 "-f", "Dockerfile.webctf", "."],
                capture_output=True, text=True, check=True,
            )
            print(" Docker image built successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to build Docker image: {e.stderr}")
            return False

    def _create_docker_files(self):
        print(" Creating Dockerfile...")
        dockerfile = """FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \\
    openssh-server nginx curl wget grep vim nano python3 net-tools \\
    && apt-get clean && rm -rf /var/lib/apt/lists/*
RUN useradd -m -s /bin/bash sandboxuser && \\
    echo 'sandboxuser:sandboxpass' | chpasswd
RUN mkdir -p /var/run/sshd && \\
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config
RUN rm -f /etc/nginx/sites-enabled/default
RUN echo 'server { \\n\\
    listen 80 default_server; \\n\\
    root /var/www/ctf; \\n\\
    index index.html; \\n\\
    server_name _; \\n\\
    location / { try_files $uri $uri/ =404; } \\n\\
    add_header X-CTF-Challenge "Active"; \\n\\
}' > /etc/nginx/sites-available/ctf && \\
    ln -s /etc/nginx/sites-available/ctf /etc/nginx/sites-enabled/ctf
RUN mkdir -p /var/www/ctf && chown -R www-data:www-data /var/www/ctf && \\
    echo '<h1>CTF Challenge</h1>' > /var/www/ctf/index.html
EXPOSE 22 80
CMD service nginx start && service ssh start && tail -f /var/log/nginx/access.log"""

        Path("Dockerfile.webctf").write_text(dockerfile)
        print(f"  ✓ Dockerfile.webctf")
        print(" Dockerfile created (no external scripts needed)\n")

    # ------------------------------------------------------------------
    # Deployment
    # ------------------------------------------------------------------

    def deploy_website_to_container(self, website_path: Path) -> bool:
        """Deploy generated website files to the running Docker container."""
        print(f"Deploying website from {website_path}")
        try:
            for file in website_path.iterdir():
                if file.name != "challenge.json":
                    subprocess.run(
                        ["docker", "cp", str(file),
                         f"{self.docker_config['container_name']}:/var/www/ctf/{file.name}"],
                        check=True, capture_output=True,
                    )
            subprocess.run(
                ["docker", "exec", self.docker_config["container_name"],
                 "nginx", "-s", "reload"],
                check=True, capture_output=True,
            )
            print(" Website deployed successfully")
            return True
        except subprocess.CalledProcessError as e:
            print(f" Failed to deploy website: {e}")
            return False

    def start_container_with_website(self, website_path: Path) -> bool:
        """Start container with the website files volume-mounted."""
        print(f" Starting container with website...")

        staging_dir = Path("./website_staging")
        if staging_dir.exists():
            try:
                def _handle_readonly(func, path, exc):
                    os.chmod(path, stat.S_IWRITE)
                    func(path)
                shutil.rmtree(staging_dir, onerror=_handle_readonly)
            except Exception:
                import random
                staging_dir = Path(f"./website_staging_{random.randint(1000, 9999)}")

        staging_dir.mkdir(exist_ok=True)
        for file in website_path.iterdir():
            if file.name != "challenge.json":
                shutil.copy(file, staging_dir / file.name)

        staging_path = str(staging_dir.absolute()).replace("\\", "/")
        if len(staging_path) > 1 and staging_path[1] == ":":
            staging_path = f"//{staging_path[0].lower()}{staging_path[2:]}"

        cmd = [
            "docker", "run",
            "--name", self.docker_config["container_name"],
            "-p", f"{self.docker_config['ssh_port']}:22",
            "-p", f"{self.docker_config['web_port']}:80",
            "-v", f"{staging_path}:/var/www/ctf",
            "-d",
            self.docker_config["image_name"],
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            time.sleep(3)
            print(" Container started successfully")
            print(f" Web server: http://localhost:{self.docker_config['web_port']}")
            print(f" SSH: localhost:{self.docker_config['ssh_port']}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to start container: {e}")
            return False

    # ------------------------------------------------------------------
    # Agent execution
    # ------------------------------------------------------------------

    def run_agent_on_challenge(self, challenge_metadata: dict,
                               max_iterations: int = 25) -> dict:
        """Run the AI agent to solve the web challenge."""
        print("\n" + "=" * 70)
        print(" STARTING AI AGENT")
        print("=" * 70)

        ai_provider = make_default_provider(api_key=self.api_key, model=self.model)
        terminal = SSHTerminal(
            host="localhost",
            port=self.docker_config["ssh_port"],
            username="sandboxuser",
            password="sandboxpass",
        )

        agent = WebCTFAgent(
            ai_provider=ai_provider,
            terminal=terminal,
            challenge_name=challenge_metadata["challenge_type"].replace("_", " ").title(),
            challenge_description=challenge_metadata["hint"],
            web_url="http://localhost",
            flag_format=r"flag\{[^\}]+\}|CTF\{[^\}]+\}|[A-Z0-9]{32}",
        )

        result = agent.run(max_iterations=max_iterations)
        result["expected_flag"] = challenge_metadata["flag"]
        result["challenge_type"] = challenge_metadata["challenge_type"]
        result["difficulty"] = challenge_metadata["difficulty"]
        return result

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    def run_single_challenge(self, challenge_type: Optional[str] = None,
                             max_iterations: int = 25) -> Optional[dict]:
        """Complete workflow: generate, deploy, and solve a single challenge."""
        print("\n" + "=" * 70)
        print(" WEB CTF CHALLENGE - COMPLETE WORKFLOW")
        print("=" * 70)

        print("\n Step 1: Generating CTF website...")
        challenge_metadata = self.website_gen.generate_website(challenge_type)
        website_path = Path(challenge_metadata["website_path"])
        print(f" Generated: {challenge_metadata['challenge_type']}")
        print(f" Flag: {challenge_metadata['flag']}")
        print(f" Hint: {challenge_metadata['hint']}")

        print("\nStep 2: Cleaning up previous containers...")
        self.docker_manager.remove_container()

        print("\n Step 3: Deploying website in Docker...")
        if not self.start_container_with_website(website_path):
            print("❌ Failed to deploy website")
            return None

        print("\n Step 4: Running AI agent to solve challenge...")
        time.sleep(2)

        try:
            result = self.run_agent_on_challenge(challenge_metadata, max_iterations)
            print("\n Step 5: Analyzing results...")
            self._print_analysis(result, challenge_metadata)
            return result
        finally:
            print("\n🧹 Cleaning up...")
            self.docker_manager.remove_container()

    def run_batch_challenges(self, count: int = 3,
                             max_iterations: int = 25) -> list:
        """Run multiple challenges in sequence."""
        print("\n" + "=" * 70)
        print(f" RUNNING BATCH: {count} WEB CTF CHALLENGES")
        print("=" * 70)

        results = []
        for i in range(count):
            print(f"\n{'=' * 70}")
            print(f" Challenge {i + 1}/{count}")
            print("=" * 70)
            result = self.run_single_challenge(max_iterations=max_iterations)
            if result:
                results.append(result)
            time.sleep(2)

        self._print_batch_summary(results)
        self._save_batch_results(results)
        return results

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------

    def _print_analysis(self, result: dict, expected: dict):
        print("\n" + "=" * 70)
        print(" CHALLENGE ANALYSIS")
        print("=" * 70)
        print(f"Challenge Type: {expected['challenge_type']}")
        print(f"Difficulty: {expected['difficulty']}")
        print(f"\nExpected Flag: {expected['flag']}")
        print(f"Found Flags: {result.get('flags_found', [])}")
        correct = expected["flag"] in result.get("flags_found", [])
        print(f"\n{'CORRECT FLAG FOUND!' if correct else ' FLAG NOT FOUND'}")
        print(f"\nAttempts: {result['total_attempts']}")
        print(f"Time: {result['time_elapsed']:.2f}s")
        print(f"Success Rate: {result['successful_commands']}/{result['total_attempts']}")
        print("=" * 70)

    def _print_batch_summary(self, results: list):
        print("\n" + "=" * 70)
        print(" BATCH SUMMARY")
        print("=" * 70)
        total = len(results)
        solved = sum(1 for r in results if r.get("solved", False))
        print(f"Total Challenges: {total}")
        print(f"Solved: {solved}")
        print(f"Success Rate: {(solved / total * 100):.1f}%" if total else "N/A")
        print("\nPer-Challenge Results:")
        for i, result in enumerate(results, 1):
            print(f"  {i}.{result['challenge_type']} - "
                  f"{result['time_elapsed']:.1f}s, "
                  f"{result['total_attempts']} attempts")
        print("=" * 70)

    def _save_batch_results(self, results: list):
        import json
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"reports/web_ctf_batch_results_{timestamp}.json"
        Path("reports").mkdir(exist_ok=True)
        with open(filename, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\n Results saved to: {filename}")
