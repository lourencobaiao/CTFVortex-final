"""TournamentOrchestrator — runs the same challenge across multiple models
and produces a comparative report."""

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ctf_agents.orchestration.base import Orchestrator
from ctf_agents.config import (
    DEFAULT_MODEL,
    FALLBACK_MODELS,
    SSH_HOST,
    SSH_PORT,
    SSH_USER,
    SSH_PASS,
    DOCKER_CONTAINER,
    DOCKER_IMAGE,
    MAX_ITERATIONS,
    FLAG_FORMAT,
)


class TournamentOrchestrator(Orchestrator):
    """Runs the same CTF challenge across multiple model configurations
    sequentially, producing a comparison report.

    Each contestant (model) gets its own ``OpenRouterProvider`` with
    fallback disabled so that only the intended model is tested.

    Supports two modes:

    * **jeopardy** — Linux system challenges.
    * **web** — Web challenges with procedural generation (website
      generated once and reused for all contestants).
    """

    JEOPARDY = "jeopardy"
    WEB = "web"

    def __init__(
        self,
        mode: str,
        api_key: str,
        models: Optional[List[str]] = None,
        docker_config: Optional[Dict[str, Any]] = None,
        challenge_config: Optional[Dict[str, Any]] = None,
        ssh_config: Optional[Dict[str, Any]] = None,
        challenge_type: Optional[str] = None,
    ):
        if mode not in (self.JEOPARDY, self.WEB):
            raise ValueError(f"Unknown mode {mode!r}. Use 'jeopardy' or 'web'.")

        self.mode = mode
        self.api_key = api_key
        self.models = models or [DEFAULT_MODEL] + FALLBACK_MODELS

        # Docker configuration with sensible defaults per mode
        if mode == self.WEB:
            default_docker = {
                "container_name": "web-ctf-tournament",
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

        self.challenge_type = challenge_type

        # Internal state
        self._docker_manager = None
        self._staging_dir: Optional[Path] = None
        self._challenge_metadata: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Orchestrator interface
    # ------------------------------------------------------------------

    def run(self, **kwargs) -> Dict[str, Any]:
        """Run each model sequentially against the same challenge."""
        print("\n" + "=" * 70)
        print(" CTF TOURNAMENT")
        print("=" * 70)
        print(f" Mode: {self.mode}")
        print(f" Models: {len(self.models)} contestants")
        for i, model in enumerate(self.models, 1):
            print(f"   {i}. {model}")
        print("=" * 70)

        # For web mode, generate the challenge once up front
        if self.mode == self.WEB:
            self._generate_challenge()

        results: List[Dict[str, Any]] = []
        for i, model in enumerate(self.models, 1):
            print(f"\n{'#' * 70}")
            print(f" CONTESTANT {i}/{len(self.models)}: {model}")
            print(f"{'#' * 70}")

            try:
                result = self._run_contestant(model, i, **kwargs)
                result["model"] = model
                results.append(result)
            except Exception as exc:
                print(f" Contestant {model} failed: {exc}")
                results.append({
                    "model": model,
                    "solved": False,
                    "flags_found": [],
                    "time_elapsed": 0,
                    "total_attempts": 0,
                    "successful_commands": 0,
                    "error": str(exc),
                })
            finally:
                self._cleanup_container()

        report = self._generate_comparison_report(results)
        self._print_comparison_report(report)
        return report

    def cleanup(self):
        """Release any remaining resources."""
        self._cleanup_container()
        if self._staging_dir is not None and self._staging_dir.exists():
            shutil.rmtree(self._staging_dir, ignore_errors=True)
            self._staging_dir = None

    # ------------------------------------------------------------------
    # Internal: challenge generation (web mode only)
    # ------------------------------------------------------------------

    def _generate_challenge(self):
        """Generate a web challenge once to reuse across all contestants."""
        from ctf_agents.pcg.website_generator import CTFWebsiteGenerator

        print("\n Generating shared CTF challenge...")
        generator = CTFWebsiteGenerator(output_dir="./ctf_websites")
        self._challenge_metadata = generator.generate_website(self.challenge_type)
        print(f" Type: {self._challenge_metadata['challenge_type']}")
        print(f" Flag: {self._challenge_metadata['flag']}")
        print(f" Hint: {self._challenge_metadata['hint']}")

    # ------------------------------------------------------------------
    # Internal: per-contestant run
    # ------------------------------------------------------------------

    def _run_contestant(self, model: str, index: int, **kwargs) -> Dict[str, Any]:
        """Run a single contestant against the challenge."""
        if self.mode == self.JEOPARDY:
            return self._run_jeopardy_contestant(model, index, **kwargs)
        return self._run_web_contestant(model, index, **kwargs)

    def _run_jeopardy_contestant(self, model: str, index: int, **kwargs) -> Dict[str, Any]:
        from ctf_agents.infra.docker_manager import DockerEnvironmentManager
        from ctf_agents.core.providers import OpenRouterProvider
        from ctf_agents.core.connections import SSHTerminal
        from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent

        self._docker_manager = DockerEnvironmentManager(
            container_name=self.docker_config["container_name"],
            image_name=self.docker_config["image_name"],
            ssh_port=self.docker_config["ssh_port"],
        )

        if not self._docker_manager.reset_environment():
            raise RuntimeError("Failed to reset Docker environment")

        ai_provider = OpenRouterProvider(
            api_key=self.api_key, model=model, fallback_models=[],
        )
        terminal = SSHTerminal(
            host=self.ssh_config["host"],
            port=self.ssh_config["port"],
            username=self.ssh_config["username"],
            password=self.ssh_config["password"],
        )

        model_short = model.split("/")[-1].split(":")[0]
        agent = JeopardyCTFAgent(
            ai_provider=ai_provider,
            terminal=terminal,
            challenge_name=self.challenge_config["challenge_name"],
            challenge_description=self.challenge_config.get("challenge_description", ""),
            flag_format=self.challenge_config.get("flag_format", FLAG_FORMAT),
            agent_id=f"contestant-{index}-{model_short}",
        )

        max_iter = kwargs.get(
            "max_iterations",
            self.challenge_config.get("max_iterations", MAX_ITERATIONS),
        )
        return agent.run(max_iterations=max_iter)

    def _run_web_contestant(self, model: str, index: int, **kwargs) -> Dict[str, Any]:
        from ctf_agents.infra.docker_manager import DockerEnvironmentManager
        from ctf_agents.core.providers import OpenRouterProvider
        from ctf_agents.core.connections import SSHTerminal
        from ctf_agents.core.web_agent import WebCTFAgent
        from ctf_agents.scoring.engine import ScoringEngine

        metadata = self._challenge_metadata
        website_path = Path(metadata["website_path"])

        # Deploy website in a fresh container
        self._docker_manager = DockerEnvironmentManager(
            container_name=self.docker_config["container_name"],
            image_name=self.docker_config["image_name"],
            ssh_port=self.docker_config["ssh_port"],
        )
        self._docker_manager.remove_container()

        if not self._start_container_with_website(website_path):
            raise RuntimeError("Failed to deploy website in Docker")

        time.sleep(2)

        ai_provider = OpenRouterProvider(
            api_key=self.api_key, model=model, fallback_models=[],
        )
        terminal = SSHTerminal(
            host="localhost",
            port=self.docker_config["ssh_port"],
            username=self.ssh_config["username"],
            password=self.ssh_config["password"],
        )

        model_short = model.split("/")[-1].split(":")[0]
        agent = WebCTFAgent(
            ai_provider=ai_provider,
            terminal=terminal,
            challenge_name=metadata["challenge_type"].replace("_", " ").title(),
            challenge_description=metadata["hint"],
            web_url="http://localhost",
            flag_format=r"flag\{[^\}]+\}|CTF\{[^\}]+\}|[A-Z0-9]{32}",
            agent_id=f"contestant-{index}-{model_short}",
        )

        max_iter = kwargs.get(
            "max_iterations",
            self.challenge_config.get("max_iterations", 25),
        )
        result = agent.run(max_iterations=max_iter)
        result["expected_flag"] = metadata["flag"]
        result["challenge_type"] = metadata["challenge_type"]
        result["difficulty"] = metadata["difficulty"]

        # Score the result
        score_breakdown = ScoringEngine().score(result, metadata)
        result["score"] = {
            "base_points": score_breakdown.base_points,
            "time_bonus": score_breakdown.time_bonus,
            "efficiency_score": score_breakdown.efficiency_score,
            "partial_credit": score_breakdown.partial_credit,
            "difficulty_multiplier": score_breakdown.difficulty_multiplier,
            "total_score": score_breakdown.total_score,
            "details": score_breakdown.details,
        }
        return result

    # ------------------------------------------------------------------
    # Internal: container management
    # ------------------------------------------------------------------

    def _cleanup_container(self):
        """Stop and remove the current container."""
        if self._docker_manager is not None:
            self._docker_manager.remove_container()
            self._docker_manager = None

    def _start_container_with_website(self, website_path: Path) -> bool:
        """Start container with the website files volume-mounted."""
        import os
        import stat

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
            print(f" Container started for contestant")
            return True
        except subprocess.CalledProcessError as e:
            print(f" Failed to start container: {e}")
            return False

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def _generate_comparison_report(self, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Build the tournament comparison report."""
        contestants = []
        for r in results:
            entry = {
                "model": r.get("model", "unknown"),
                "solved": bool(r.get("flags_found")),
                "flags_found": r.get("flags_found", []),
                "time_elapsed": r.get("time_elapsed", 0),
                "total_attempts": r.get("total_attempts", 0),
                "successful_commands": r.get("successful_commands", 0),
                "agent_id": r.get("agent_id", ""),
            }
            if "score" in r:
                entry["score"] = r["score"]
            if "error" in r:
                entry["error"] = r["error"]
            contestants.append(entry)

        # Build ranking
        if self.mode == self.WEB:
            # Sort by: solved desc, total_score desc, fewer attempts
            ranking_key = lambda c: (
                c["solved"],
                c.get("score", {}).get("total_score", 0),
                -c["total_attempts"],
            )
        else:
            # Sort by: solved desc, time_elapsed asc, fewer attempts
            ranking_key = lambda c: (
                c["solved"],
                -c["time_elapsed"],  # negate so lower time ranks higher
                -c["total_attempts"],
            )

        sorted_contestants = sorted(contestants, key=ranking_key, reverse=True)
        ranking = []
        for rank, c in enumerate(sorted_contestants, 1):
            ranking.append({
                "rank": rank,
                "model": c["model"],
                "solved": c["solved"],
                "total_score": c.get("score", {}).get("total_score", 0),
                "time_elapsed": c["time_elapsed"],
                "total_attempts": c["total_attempts"],
            })

        report = {
            "tournament": True,
            "mode": self.mode,
            "total_contestants": len(contestants),
            "contestants": contestants,
            "ranking": ranking,
        }

        if self._challenge_metadata:
            report["challenge_type"] = self._challenge_metadata.get("challenge_type", "")
            report["difficulty"] = self._challenge_metadata.get("difficulty", "")
            report["expected_flag"] = self._challenge_metadata.get("flag", "")

        return report

    def _print_comparison_report(self, report: Dict[str, Any]):
        """Print a formatted comparison table to the console."""
        print("\n" + "=" * 70)
        print(" TOURNAMENT RESULTS")
        print("=" * 70)

        if "challenge_type" in report:
            print(f" Challenge: {report['challenge_type']}  "
                  f"Difficulty: {report.get('difficulty', 'N/A')}")

        print(f" Contestants: {report['total_contestants']}")
        print()

        # Results table
        print(f"{'Rank':<6}{'Model':<45}{'Solved':<8}{'Time':>8}{'Cmds':>6}"
              f"{'Score':>8}")
        print("-" * 81)

        for entry in report["ranking"]:
            model_short = entry["model"]
            if len(model_short) > 43:
                model_short = model_short[:40] + "..."
            solved_str = "YES" if entry["solved"] else "no"
            time_str = f"{entry['time_elapsed']:.1f}s"
            score_str = f"{entry['total_score']:.1f}" if entry["total_score"] else "-"
            print(f"{entry['rank']:<6}{model_short:<45}{solved_str:<8}"
                  f"{time_str:>8}{entry['total_attempts']:>6}{score_str:>8}")

        print("=" * 70)

        # Winner
        if report["ranking"]:
            winner = report["ranking"][0]
            if winner["solved"]:
                print(f"\n Winner: {winner['model']}")
                print(f"   Score: {winner['total_score']:.1f}  "
                      f"Time: {winner['time_elapsed']:.1f}s  "
                      f"Commands: {winner['total_attempts']}")
            else:
                print("\n No contestant solved the challenge.")
        print()
