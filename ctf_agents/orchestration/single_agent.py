"""SingleAgentOrchestrator — reproduces the existing run_ctf_with_reset and
WebCTFRunner workflows behind the unified Orchestrator interface."""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from ctf_agents.orchestration.base import Orchestrator
from ctf_agents.config import (
    DEFAULT_MODEL,
    SSH_HOST,
    SSH_PORT,
    SSH_USER,
    SSH_PASS,
    DOCKER_CONTAINER,
    DOCKER_IMAGE,
    MAX_ITERATIONS,
    FLAG_FORMAT,
)


class SingleAgentOrchestrator(Orchestrator):
    """Orchestrates a single AI agent solving a single CTF challenge.

    Supports two modes:

    * **jeopardy** — Linux system challenges (mirrors ``run_ctf_with_reset``).
    * **web** — Web challenges with procedural generation (mirrors
      ``WebCTFRunner.run_single_challenge``).
    """

    # Accepted modes
    JEOPARDY = "jeopardy"
    WEB = "web"

    def __init__(
        self,
        mode: str,
        api_key: str,
        model: Optional[str] = None,
        docker_config: Optional[Dict[str, Any]] = None,
        challenge_config: Optional[Dict[str, Any]] = None,
        ssh_config: Optional[Dict[str, Any]] = None,
        challenge_type: Optional[str] = None,
    ):
        if mode not in (self.JEOPARDY, self.WEB):
            raise ValueError(f"Unknown mode {mode!r}. Use 'jeopardy' or 'web'.")

        self.mode = mode
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL

        # Docker configuration with sensible defaults per mode
        if mode == self.WEB:
            default_docker = {
                "container_name": "web-ctf-sandbox",
                "image_name": "webctf:latest",
                "ssh_port": 2222,
                "web_port": 8080,
            }
        else:
            default_docker = {
                "container_name": DOCKER_CONTAINER,
                "image_name": DOCKER_IMAGE,
                "ssh_port": SSH_PORT,
            }
        self.docker_config = {**default_docker, **(docker_config or {})}

        # SSH configuration
        default_ssh = {
            "host": SSH_HOST,
            "port": self.docker_config["ssh_port"],
            "username": SSH_USER,
            "password": SSH_PASS,
        }
        self.ssh_config = {**default_ssh, **(ssh_config or {})}

        # Challenge configuration
        default_challenge = {
            "challenge_name": "CTF Challenge",
            "challenge_description": "",
            "flag_format": FLAG_FORMAT,
            "max_iterations": MAX_ITERATIONS,
        }
        self.challenge_config = {**default_challenge, **(challenge_config or {})}

        # Web-only: specific challenge type (None → random)
        self.challenge_type = challenge_type

        # Lazily initialised infrastructure
        self._docker_manager = None
        self._staging_dir: Optional[Path] = None

    # ------------------------------------------------------------------
    # Orchestrator interface
    # ------------------------------------------------------------------

    def run(self, **kwargs) -> Dict[str, Any]:
        """Execute the full challenge workflow for the configured mode."""
        if self.mode == self.JEOPARDY:
            return self._run_jeopardy(**kwargs)
        return self._run_web(**kwargs)

    def cleanup(self):
        """Stop/remove the Docker container and any staging files."""
        if self._docker_manager is not None:
            self._docker_manager.remove_container()

        if self._staging_dir is not None and self._staging_dir.exists():
            shutil.rmtree(self._staging_dir, ignore_errors=True)
            self._staging_dir = None

    # ------------------------------------------------------------------
    # Jeopardy workflow (mirrors run_ctf_with_reset)
    # ------------------------------------------------------------------

    def _run_jeopardy(self, **kwargs) -> Dict[str, Any]:
        from ctf_agents.infra.docker_manager import DockerEnvironmentManager
        from ctf_agents.core.providers import make_default_provider
        from ctf_agents.core.connections import SSHTerminal
        from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent

        self._docker_manager = DockerEnvironmentManager(
            container_name=self.docker_config["container_name"],
            image_name=self.docker_config["image_name"],
            ssh_port=self.docker_config["ssh_port"],
            dockerfile_path=self.docker_config.get("dockerfile_path"),
        )

        try:
            # Reset environment before run
            if not self._docker_manager.reset_environment():
                print("❌ Failed to reset environment. Aborting.")
                return {}

            ai_provider = make_default_provider(
                api_key=self.api_key, model=self.model,
            )
            terminal = SSHTerminal(
                host=self.ssh_config["host"],
                port=self.ssh_config["port"],
                username=self.ssh_config["username"],
                password=self.ssh_config["password"],
            )
            agent = JeopardyCTFAgent(
                ai_provider=ai_provider,
                terminal=terminal,
                challenge_name=self.challenge_config["challenge_name"],
                challenge_description=self.challenge_config.get("challenge_description", ""),
                flag_format=self.challenge_config.get("flag_format", FLAG_FORMAT),
            )

            max_iter = kwargs.get(
                "max_iterations",
                self.challenge_config.get("max_iterations", MAX_ITERATIONS),
            )
            result = agent.run(max_iterations=max_iter)
            return result or {}

        finally:
            self._docker_manager.reset_environment()

    # ------------------------------------------------------------------
    # Web workflow (mirrors WebCTFRunner.run_single_challenge)
    # ------------------------------------------------------------------

    def _run_web(self, **kwargs) -> Dict[str, Any]:
        from ctf_agents.infra.docker_manager import DockerEnvironmentManager
        from ctf_agents.core.providers import make_default_provider
        from ctf_agents.core.connections import SSHTerminal
        from ctf_agents.core.web_agent import WebCTFAgent
        from ctf_agents.pcg.website_generator import CTFWebsiteGenerator

        self._docker_manager = DockerEnvironmentManager(
            container_name=self.docker_config["container_name"],
            image_name=self.docker_config["image_name"],
            ssh_port=self.docker_config["ssh_port"],
        )

        print("\n" + "=" * 70)
        print(" WEB CTF CHALLENGE — SingleAgentOrchestrator")
        print("=" * 70)

        # Step 1: Generate challenge
        print("\n Step 1: Generating CTF website...")
        generator = CTFWebsiteGenerator(output_dir="./ctf_websites")
        challenge_metadata = generator.generate_website(self.challenge_type)
        website_path = Path(challenge_metadata["website_path"])
        print(f" Generated: {challenge_metadata['challenge_type']}")
        print(f" Flag: {challenge_metadata['flag']}")
        print(f" Hint: {challenge_metadata['hint']}")

        # Step 2: Clean up previous container
        print("\n Step 2: Cleaning up previous containers...")
        self._docker_manager.remove_container()

        # Step 3: Deploy website in Docker
        print("\n Step 3: Deploying website in Docker...")
        if not self._start_container_with_website(website_path):
            print("❌ Failed to deploy website")
            return {}

        # Step 4: Run AI agent
        print("\n Step 4: Running AI agent to solve challenge...")
        time.sleep(2)

        try:
            max_iter = kwargs.get(
                "max_iterations",
                self.challenge_config.get("max_iterations", 25),
            )

            ai_provider = make_default_provider(
                api_key=self.api_key, model=self.model,
            )
            terminal = SSHTerminal(
                host="localhost",
                port=self.docker_config["ssh_port"],
                username=self.ssh_config["username"],
                password=self.ssh_config["password"],
            )
            agent = WebCTFAgent(
                ai_provider=ai_provider,
                terminal=terminal,
                challenge_name=challenge_metadata["challenge_type"].replace("_", " ").title(),
                challenge_description=challenge_metadata["hint"],
                web_url="http://localhost",
                flag_format=r"flag\{[^\}]+\}|CTF\{[^\}]+\}|[A-Z0-9]{32}",
            )

            result = agent.run(max_iterations=max_iter)
            result["expected_flag"] = challenge_metadata["flag"]
            result["challenge_type"] = challenge_metadata["challenge_type"]
            result["difficulty"] = challenge_metadata["difficulty"]

            # Step 5: Scoring & Analysis
            print("\n Step 5: Scoring & analyzing results...")
            from ctf_agents.scoring.engine import ScoringEngine
            score_breakdown = ScoringEngine().score(result, challenge_metadata)
            result["score"] = {
                "base_points": score_breakdown.base_points,
                "time_bonus": score_breakdown.time_bonus,
                "efficiency_score": score_breakdown.efficiency_score,
                "partial_credit": score_breakdown.partial_credit,
                "difficulty_multiplier": score_breakdown.difficulty_multiplier,
                "total_score": score_breakdown.total_score,
                "details": score_breakdown.details,
            }

            self._print_analysis(result, challenge_metadata, score_breakdown)

            return result

        finally:
            print("\n Cleaning up...")
            self.cleanup()

    # ------------------------------------------------------------------
    # Web helpers (ported from WebCTFRunner)
    # ------------------------------------------------------------------

    def _start_container_with_website(self, website_path: Path) -> bool:
        """Start container with the website files volume-mounted."""
        import os
        import stat

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
        self._staging_dir = staging_dir

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
            "-p", f"{self.docker_config.get('web_port', 8080)}:80",
            "-v", f"{staging_path}:/var/www/ctf",
            "-d",
            self.docker_config["image_name"],
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            time.sleep(3)
            print(" Container started successfully")
            print(f" Web server: http://localhost:{self.docker_config.get('web_port', 8080)}")
            print(f" SSH: localhost:{self.docker_config['ssh_port']}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to start container: {e}")
            return False

    @staticmethod
    def _print_analysis(result: dict, expected: dict, score_breakdown=None):
        print("\n" + "=" * 70)
        print(" CHALLENGE ANALYSIS")
        print("=" * 70)
        print(f"Challenge Type: {expected['challenge_type']}")
        print(f"Difficulty: {expected['difficulty']}")
        print(f"\nExpected Flag: {expected['flag']}")
        print(f"Found Flags: {result.get('flags_found', [])}")
        correct = expected["flag"] in result.get("flags_found", [])
        print(f"\n{'CORRECT FLAG FOUND!' if correct else 'FLAG NOT FOUND'}")
        print(f"\nAttempts: {result['total_attempts']}")
        print(f"Time: {result['time_elapsed']:.2f}s")
        print(f"Success Rate: {result['successful_commands']}/{result['total_attempts']}")

        if score_breakdown is not None:
            print(f"\n{'─' * 35}")
            print(" SCORE")
            print(f"{'─' * 35}")
            print(score_breakdown)

        print("=" * 70)
