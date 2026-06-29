"""MultiAgentOrchestrator — drives a roster of agents on Docker container(s).

Two execution modes, selected by ``scenario.metadata['per_agent_environments']``:

* **Shared container** (default) — all agents SSH into one container that hosts
  a generated CTF website. Mirrors ``SingleAgentOrchestrator._run_web`` for
  challenge setup.
* **Per-agent environments** — each agent gets its own Docker container on its
  own isolated Docker network, with no route to peers. Containers are built
  from a single parameterized Dockerfile with per-env build-args. Used by the
  Diamond Vault scenario.

Single-agent and tournament orchestrators are intentionally untouched.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ctf_agents.orchestration.base import Orchestrator
from ctf_agents.config import (
    DEFAULT_MODEL, FLAG_FORMAT, SSH_HOST, SSH_USER, SSH_PASS,
)
from ctf_agents.core.primitives import EventBus, SharedState
from ctf_agents.multi_agent.scenarios import AgentSpec, Scenario
from ctf_agents.multi_agent.coordinator import MultiAgentCoordinator, RunSummary


class MultiAgentOrchestrator(Orchestrator):
    """Orchestrates N cooperating/competing agents on a shared container.

    Only web mode is wired up initially — jeopardy mode can be added
    later by mirroring the single-agent jeopardy path.
    """

    def __init__(
        self,
        api_key: str,
        scenario: Scenario,
        default_model: Optional[str] = None,
        docker_config: Optional[Dict[str, Any]] = None,
        challenge_type: Optional[str] = None,
    ):
        self.api_key = api_key
        self.scenario = scenario
        self.default_model = default_model or DEFAULT_MODEL
        self.challenge_type = challenge_type

        default_docker = {
            "container_name": "multi-ctf-sandbox",
            "image_name": "webctf:latest",
            "ssh_port": 2222,
            "web_port": 8080,
        }
        self.docker_config = {**default_docker, **(docker_config or {})}

        # Shared communication substrate (passed to every agent).
        # Even in Phase 1 without messaging, they cost nothing to create
        # and guarantee the Phase 2 wiring lands without touching this file.
        self.event_bus = EventBus()
        self.shared_state = SharedState()

        # Lazily initialised infrastructure
        self._docker_manager = None
        self._staging_dir: Optional[Path] = None
        self._agents: List = []
        self._coordinator: Optional[MultiAgentCoordinator] = None
        self._challenge_metadata: Optional[Dict[str, Any]] = None
        # Per-agent-environments bookkeeping (used only when
        # scenario.metadata['per_agent_environments'] is True).
        self._per_env_containers: List[str] = []
        self._per_env_networks: List[str] = []
        # Extra network for TCP transport (shared comms subnet between
        # agent containers). Tracked separately so it tears down after
        # both per-agent networks during cleanup.
        self._comms_network: Optional[str] = None

    # ------------------------------------------------------------------
    # Orchestrator interface
    # ------------------------------------------------------------------

    def run(self, **kwargs) -> Dict[str, Any]:
        if self.scenario.metadata.get("per_agent_environments"):
            return self._run_per_agent_environments()
        return self._run_shared_container()

    def _run_shared_container(self) -> Dict[str, Any]:
        from ctf_agents.infra.docker_manager import DockerEnvironmentManager
        from ctf_agents.pcg.website_generator import CTFWebsiteGenerator

        self._docker_manager = DockerEnvironmentManager(
            container_name=self.docker_config["container_name"],
            image_name=self.docker_config["image_name"],
            ssh_port=self.docker_config["ssh_port"],
        )

        print("\n" + "=" * 70)
        print(" MULTI-AGENT CTF — MultiAgentOrchestrator")
        print(f" Scenario: {self.scenario.name}")
        print(f" Roster:   {len(self.scenario.roster)} agent(s)")
        print(f" Max ticks: {self.scenario.max_ticks}")
        print("=" * 70)

        # Step 1: Generate challenge
        print("\n Step 1: Generating CTF website...")
        generator = CTFWebsiteGenerator(output_dir="./ctf_websites")
        self._challenge_metadata = generator.generate_website(self.challenge_type)
        website_path = Path(self._challenge_metadata["website_path"])
        print(f" Generated: {self._challenge_metadata['challenge_type']}")
        print(f" Flag: {self._challenge_metadata['flag']}")
        print(f" Hint: {self._challenge_metadata['hint']}")

        # Step 2: Clean up previous container
        print("\n Step 2: Cleaning up previous containers...")
        self._docker_manager.remove_container()

        # Step 3: Deploy website in Docker
        print("\n Step 3: Deploying website in Docker...")
        if not self._start_container_with_website(website_path):
            print("❌ Failed to deploy website")
            return {}

        try:
            # Step 4: Materialize agents + run coordinator
            print("\n Step 4: Instantiating agents...")
            self._agents = self._build_agents()
            for agent in self._agents:
                print(f"   - {agent.agent_id} ({agent.role.value}"
                      + (f", team={agent.team_id}" if agent.team_id else "")
                      + f", model={getattr(agent.ai_provider, 'model', '?')})")

            # Create the coordinator first so agents receive their mailbox /
            # per-tick caps *before* start() seeds the initial prompt.
            self._coordinator = MultiAgentCoordinator(
                agents=self._agents,
                scenario=self.scenario,
                event_bus=self.event_bus,
                shared_state=self.shared_state,
            )

            # Start all agents (connect SSH, seed prompts)
            for agent in self._agents:
                try:
                    agent.start()
                except Exception as exc:
                    print(f"[{agent.agent_id}] start() failed: {exc!r}")
                    raise

            print("\n Step 5: Running multi-agent loop...")
            time.sleep(2)
            summary = self._coordinator.run()

            # Stop all agents
            for agent in self._agents:
                try:
                    agent.stop()
                except Exception:
                    pass

            # Step 6: Aggregate report
            print("\n Step 6: Aggregating reports...")
            result = self._build_aggregate_report(summary)
            self._print_summary(summary, result)
            return result

        finally:
            print("\n Cleaning up...")
            self.cleanup()

    def cleanup(self):
        if self._docker_manager is not None:
            self._docker_manager.remove_container()

        if self._staging_dir is not None and self._staging_dir.exists():
            shutil.rmtree(self._staging_dir, ignore_errors=True)
            self._staging_dir = None

        self._cleanup_per_env()

    # ------------------------------------------------------------------
    # Agent construction
    # ------------------------------------------------------------------

    def _build_agents(self) -> List:
        from ctf_agents.core.providers import make_default_provider
        from ctf_agents.core.connections import SSHTerminal
        from ctf_agents.core.web_agent import WebCTFAgent
        from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
        from ctf_agents.core.enums import AgentRole

        agents = []
        for spec in self.scenario.roster:
            model = spec.model or self.default_model
            provider = make_default_provider(api_key=self.api_key, model=model)

            terminal = SSHTerminal(
                host=SSH_HOST,
                port=self.docker_config["ssh_port"],
                username=SSH_USER,
                password=SSH_PASS,
            )

            try:
                role = AgentRole(spec.role)
            except ValueError:
                role = AgentRole.ATTACKER

            if spec.agent_class == "web":
                agent = WebCTFAgent(
                    ai_provider=provider,
                    terminal=terminal,
                    challenge_name=self._challenge_metadata["challenge_type"]
                        .replace("_", " ").title(),
                    challenge_description=self._challenge_metadata["hint"],
                    web_url="http://localhost",
                    flag_format=FLAG_FORMAT,
                )
            elif spec.agent_class == "jeopardy":
                agent = JeopardyCTFAgent(
                    ai_provider=provider,
                    terminal=terminal,
                    challenge_name=self._challenge_metadata["challenge_type"]
                        .replace("_", " ").title(),
                    challenge_description=self._challenge_metadata["hint"],
                    flag_format=FLAG_FORMAT,
                )
            else:
                raise ValueError(
                    f"Unknown agent_class {spec.agent_class!r} for {spec.agent_id}. "
                    "Supported: 'web', 'jeopardy'."
                )

            # Retro-fit multi-agent fields that the subclass constructors
            # don't forward via super().__init__.
            agent.agent_id = spec.agent_id
            agent.role = role
            agent.team_id = spec.team_id
            agent.event_bus = self.event_bus
            agent.shared_state = self.shared_state
            brief = spec.extra.get("scenario_brief") if spec.extra else None
            if brief:
                agent.scenario_brief = brief

            agents.append(agent)

        return agents

    # ------------------------------------------------------------------
    # Container setup (ported from SingleAgentOrchestrator._run_web)
    # ------------------------------------------------------------------

    def _start_container_with_website(self, website_path: Path) -> bool:
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
            print(f" SSH:        localhost:{self.docker_config['ssh_port']} (shared by {len(self.scenario.roster)} agent(s))")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ Failed to start container: {e}")
            return False

    # ------------------------------------------------------------------
    # Aggregate report
    # ------------------------------------------------------------------

    def _build_aggregate_report(self, summary: RunSummary) -> Dict[str, Any]:
        """Combine per-agent reports with a top-level multi_agent_summary."""
        from ctf_agents.scoring.engine import MultiAgentScoringEngine

        per_agent_reports = [agent.generate_report() for agent in self._agents]

        all_flags: List[str] = []
        for r in per_agent_reports:
            for f in r.get("flags_found", []):
                if f not in all_flags:
                    all_flags.append(f)

        expected_flag = self._challenge_metadata.get("flag", "") if self._challenge_metadata else ""
        challenge_type = self._challenge_metadata.get("challenge_type", "") if self._challenge_metadata else ""
        difficulty = self._challenge_metadata.get("difficulty", "") if self._challenge_metadata else ""

        coord_metrics = self._coordinator.metrics() if self._coordinator is not None else {}
        summary_block = MultiAgentScoringEngine().summarize(
            scenario_name=self.scenario.name,
            per_agent_reports=per_agent_reports,
            coordinator_metrics=coord_metrics,
            stop_reason=summary.stop_reason,
            win_condition_result=summary.win_condition_result,
        )
        summary_block["records"] = [
            {
                "tick": r.tick,
                "agent_id": r.agent_id,
                "iteration": r.iteration,
                "command": r.command,
                "solved": r.solved,
                "error": r.error,
            }
            for r in summary.records
        ]

        return {
            "mode": "multi_agent",
            "scenario": self.scenario.name,
            "challenge_type": challenge_type,
            "difficulty": difficulty,
            "expected_flag": expected_flag,
            "solved": summary_block["any_agent_solved"],
            "flags_found": all_flags,
            "multi_agent_summary": summary_block,
            "agents": per_agent_reports,
        }

    # ------------------------------------------------------------------
    # Per-agent isolated environments (Diamond Vault and similar)
    # ------------------------------------------------------------------

    def _run_per_agent_environments(self) -> Dict[str, Any]:
        envs = self.scenario.metadata["environments"]
        dockerfile_path = self.scenario.metadata["dockerfile_path"]

        print("\n" + "=" * 70)
        print(" MULTI-AGENT CTF — per-agent isolated environments")
        print(f" Scenario: {self.scenario.name}")
        print(f" Roster:   {len(self.scenario.roster)} agent(s) on {len(envs)} container(s)")
        print(f" Max ticks: {self.scenario.max_ticks}")
        print("=" * 70)

        # 1. Build images (one per env, build-args differ).
        print("\n Step 1: Building per-environment images...")
        for agent_id, env in envs.items():
            if not self._build_per_env_image(env, dockerfile_path):
                print(f"❌ Failed to build image for {agent_id}")
                return {}

        # 2. Wipe stale containers + networks before recreating.
        print("\n Step 2: Removing any stale containers and networks...")
        for env in envs.values():
            self._destroy_container_if_exists(env["container_name"])
        for env in envs.values():
            self._destroy_network_if_exists(env["network_name"])

        # 3. Create isolated networks (one per env) and start containers.
        print("\n Step 3: Creating isolated networks and starting containers...")
        for agent_id, env in envs.items():
            if not self._ensure_network(env["network_name"]):
                return {}
            self._per_env_networks.append(env["network_name"])
            if not self._start_per_env_container(env):
                return {}
            self._per_env_containers.append(env["container_name"])

        # 4. Wait for sshd in every container before agents try to connect.
        if not self._wait_for_per_env_sshd(envs):
            print("❌ One or more containers failed to expose SSH in time")
            return {}

        # 4b. TCP-transport setup: comms network, peer map, listener probe.
        # All conditional on the scenario opting in. With "inproc" the rest
        # of this method is byte-identical to the legacy path.
        transport_obj = None
        use_tcp = (
            self.scenario.messaging.transport == "tcp"
            and self.scenario.metadata.get("comms_network") is not None
        )
        if use_tcp:
            comms = self.scenario.metadata["comms_network"]
            print("\n Step 3b: Setting up TCP comms network...")
            if not self._setup_comms_network(envs, comms):
                return {}
            if not self._write_peer_map(envs, comms):
                return {}
            if not self._wait_for_listeners(envs, comms):
                print("❌ One or more containers failed to bring up the TCP listener")
                return {}
            transport_obj = self._build_tcp_transport(envs)

        try:
            # 5. Build agents with per-env SSH terminals and scenario briefs.
            print("\n Step 4: Instantiating agents...")
            self._agents = self._build_agents_per_env(envs)
            for agent in self._agents:
                print(f"   - {agent.agent_id} ({agent.role.value}"
                      + (f", team={agent.team_id}" if agent.team_id else "")
                      + f", model={getattr(agent.ai_provider, 'model', '?')})")

            self._coordinator = MultiAgentCoordinator(
                agents=self._agents,
                scenario=self.scenario,
                event_bus=self.event_bus,
                shared_state=self.shared_state,
                transport=transport_obj,
            )

            for agent in self._agents:
                try:
                    agent.start()
                except Exception as exc:
                    print(f"[{agent.agent_id}] start() failed: {exc!r}")
                    raise

            print("\n Step 5: Running multi-agent loop...")
            summary = self._coordinator.run()

            for agent in self._agents:
                try:
                    agent.stop()
                except Exception:
                    pass

            print("\n Step 6: Aggregating reports...")
            result = self._build_per_env_report(summary)
            self._print_summary(summary, result)
            return result

        finally:
            print("\n Cleaning up...")
            self.cleanup()

    def _build_per_env_image(self, env: Dict[str, Any], dockerfile_path: str) -> bool:
        # AGENT_ID is baked into /etc/agent-id inside the image so that
        # the `agent-send` CLI knows the sender even from non-interactive
        # SSH sessions (which don't inherit container env vars).
        cmd = [
            "docker", "build",
            "-t", env["image_name"],
            "-f", dockerfile_path,
            "--build-arg", f"OWN_KEY={env['own_key']}",
            "--build-arg", f"PEER_KEY={env['peer_key']}",
            "--build-arg", f"FLAG={env['flag']}",
            "--build-arg", f"AGENT_ID={env['agent_id']}",
            ".",
        ]
        print(f"  - Building {env['image_name']} (OWN_KEY={env['own_key'][:6]}…, PEER_KEY={env['peer_key'][:6]}…)")
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ docker build failed for {env['image_name']}: {e.stderr.decode(errors='ignore')}")
            return False

    def _ensure_network(self, network_name: str) -> bool:
        try:
            subprocess.run(
                ["docker", "network", "create", network_name],
                check=True, capture_output=True,
            )
            print(f"  - Created network {network_name}")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ docker network create failed: {e.stderr.decode(errors='ignore')}")
            return False

    def _start_per_env_container(self, env: Dict[str, Any]) -> bool:
        # --network attaches ONLY to this network (no default bridge),
        # which is exactly the cross-env isolation we need.
        cmd = [
            "docker", "run",
            "--name", env["container_name"],
            "--network", env["network_name"],
            "-p", f"{env['ssh_port']}:22",
            "-d",
            env["image_name"],
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  - Started {env['container_name']} on {env['network_name']} (ssh→{env['ssh_port']})")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ docker run failed: {e.stderr.decode(errors='ignore')}")
            return False

    def _wait_for_per_env_sshd(self, envs: Dict[str, Dict[str, Any]], timeout_s: int = 30) -> bool:
        """Poll each container's SSH port from the host until reachable."""
        import socket

        deadline = time.time() + timeout_s
        pending = {env["ssh_port"]: env["container_name"] for env in envs.values()}
        while pending and time.time() < deadline:
            for port in list(pending.keys()):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1):
                        print(f"  - SSH ready on {pending[port]}:{port}")
                        del pending[port]
                except (OSError, ConnectionRefusedError):
                    pass
            if pending:
                time.sleep(0.5)
        # SSH banner exchange + paramiko handshake — a small extra buffer.
        time.sleep(1)
        return not pending

    # ------------------------------------------------------------------
    # TCP transport: comms network + listener readiness + peer map
    # ------------------------------------------------------------------

    def _setup_comms_network(
        self,
        envs: Dict[str, Dict[str, Any]],
        comms: Dict[str, Any],
    ) -> bool:
        """Create the shared comms network and connect each agent container.

        Diamond Vault's per-agent isolated networks (``diamond-a``,
        ``diamond-b``) stay in place — the comms network is a *second*
        interface used only for the TCP messaging socket. The puzzle is
        unchanged because the vault check is local to each container.

        ``comms`` shape (from ``scenario.metadata['comms_network']``)::

            {
              "name":   "ctf-net-diamond-comms",
              "subnet": "10.55.0.0/24",
              "port":   7000,
              "peers":  {agent_id: {"ip": "10.55.0.10", "hostname": "agent-a"}, ...}
            }
        """
        name = comms["name"]
        subnet = comms["subnet"]
        # Stale comms net from a previous run is fine to drop.
        self._destroy_network_if_exists(name)
        try:
            subprocess.run(
                ["docker", "network", "create", "--subnet", subnet, name],
                check=True, capture_output=True,
            )
            print(f"  - Created comms network {name} ({subnet})")
        except subprocess.CalledProcessError as e:
            print(f"❌ docker network create (comms) failed: {e.stderr.decode(errors='ignore')}")
            return False
        self._comms_network = name

        for agent_id, env in envs.items():
            peer_info = comms["peers"].get(agent_id)
            if peer_info is None:
                print(f"❌ no comms peer entry for {agent_id}")
                return False
            cmd = [
                "docker", "network", "connect",
                "--ip", peer_info["ip"],
                "--alias", peer_info["hostname"],
                name, env["container_name"],
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                print(
                    f"  - {env['container_name']} joined {name} "
                    f"as {peer_info['hostname']} ({peer_info['ip']})"
                )
            except subprocess.CalledProcessError as e:
                print(f"❌ docker network connect failed for {agent_id}: "
                      f"{e.stderr.decode(errors='ignore')}")
                return False
        return True

    def _write_peer_map(
        self,
        envs: Dict[str, Dict[str, Any]],
        comms: Dict[str, Any],
    ) -> bool:
        """Write /etc/agent-peers into each container via ``docker exec``.

        The file format is ``<peer_id> <ip>:<port>`` per line; consumed
        by ``agent-send`` to resolve the destination.
        """
        port = comms["port"]
        peers = comms["peers"]
        for agent_id, env in envs.items():
            lines = [
                f"{other_id} {info['ip']}:{port}"
                for other_id, info in peers.items()
                if other_id != agent_id
            ]
            content = "\n".join(lines) + "\n"
            # ``sh -c "cat > file"`` lets us push arbitrary text without
            # worrying about shell quoting of newlines on the docker exec
            # command line.
            cmd = [
                "docker", "exec", "-i", env["container_name"],
                "sh", "-c", f"cat > /etc/agent-peers && chmod 0644 /etc/agent-peers",
            ]
            try:
                subprocess.run(cmd, check=True, input=content.encode("utf-8"),
                               capture_output=True)
            except subprocess.CalledProcessError as e:
                print(f"❌ writing /etc/agent-peers in {env['container_name']} failed: "
                      f"{e.stderr.decode(errors='ignore')}")
                return False
        print(f"  - Wrote /etc/agent-peers into {len(envs)} container(s)")
        return True

    def _wait_for_listeners(
        self,
        envs: Dict[str, Dict[str, Any]],
        comms: Dict[str, Any],
        timeout_s: int = 15,
    ) -> bool:
        """Poll the listener port inside each container via ``docker exec``.

        The listener binds inside the container on the comms network's
        interface, so probing it from the host requires going through
        the container's namespace. ``nc -z`` is preinstalled (the base
        Dockerfile installs ``netcat``).
        """
        port = comms["port"]
        deadline = time.time() + timeout_s
        pending = set(envs.keys())
        while pending and time.time() < deadline:
            for agent_id in list(pending):
                env = envs[agent_id]
                probe = subprocess.run(
                    ["docker", "exec", env["container_name"],
                     "nc", "-z", "127.0.0.1", str(port)],
                    capture_output=True,
                )
                if probe.returncode == 0:
                    print(f"  - listener ready in {env['container_name']}:{port}")
                    pending.discard(agent_id)
            if pending:
                time.sleep(0.3)
        return not pending

    def _build_tcp_transport(
        self,
        envs: Dict[str, Dict[str, Any]],
    ):
        """Construct a TcpTransport wired to paramiko-tail each container."""
        from ctf_agents.multi_agent.tcp_transport import (
            TcpTransport, make_paramiko_tail,
        )
        ssh_targets = {
            env["agent_id"]: {
                "host": SSH_HOST,
                "port": env["ssh_port"],
                "username": SSH_USER,
                "password": SSH_PASS,
            }
            for env in envs.values()
        }
        return TcpTransport(tail_fn=make_paramiko_tail(ssh_targets))

    def _build_agents_per_env(self, envs: Dict[str, Dict[str, Any]]) -> List:
        from ctf_agents.core.providers import make_default_provider
        from ctf_agents.core.connections import SSHTerminal
        from ctf_agents.core.web_agent import WebCTFAgent
        from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
        from ctf_agents.core.enums import AgentRole

        challenge_name = self.scenario.metadata.get("challenge_name", self.scenario.name)
        agents = []
        for spec in self.scenario.roster:
            env = envs[spec.agent_id]
            model = spec.model or self.default_model
            provider = make_default_provider(api_key=self.api_key, model=model)
            terminal = SSHTerminal(
                host=SSH_HOST,
                port=env["ssh_port"],
                username=SSH_USER,
                password=SSH_PASS,
            )
            try:
                role = AgentRole(spec.role)
            except ValueError:
                role = AgentRole.ATTACKER

            if spec.agent_class == "jeopardy":
                agent = JeopardyCTFAgent(
                    ai_provider=provider,
                    terminal=terminal,
                    challenge_name=challenge_name,
                    challenge_description="",
                    flag_format=FLAG_FORMAT,
                )
            elif spec.agent_class == "web":
                agent = WebCTFAgent(
                    ai_provider=provider,
                    terminal=terminal,
                    challenge_name=challenge_name,
                    challenge_description="",
                    web_url="http://localhost",
                    flag_format=FLAG_FORMAT,
                )
            else:
                raise ValueError(
                    f"Unknown agent_class {spec.agent_class!r} for {spec.agent_id}"
                )

            agent.agent_id = spec.agent_id
            agent.role = role
            agent.team_id = spec.team_id
            agent.event_bus = self.event_bus
            agent.shared_state = self.shared_state
            brief = spec.extra.get("scenario_brief") if spec.extra else None
            if brief:
                agent.scenario_brief = brief

            agents.append(agent)
        return agents

    def _build_per_env_report(self, summary: RunSummary) -> Dict[str, Any]:
        from ctf_agents.scoring.engine import MultiAgentScoringEngine

        per_agent_reports = [agent.generate_report() for agent in self._agents]

        all_flags: List[str] = []
        for r in per_agent_reports:
            for f in r.get("flags_found", []):
                if f not in all_flags:
                    all_flags.append(f)

        expected_flag = self.scenario.metadata.get("expected_flag", "")
        coord_metrics = self._coordinator.metrics() if self._coordinator is not None else {}
        summary_block = MultiAgentScoringEngine().summarize(
            scenario_name=self.scenario.name,
            per_agent_reports=per_agent_reports,
            coordinator_metrics=coord_metrics,
            stop_reason=summary.stop_reason,
            win_condition_result=summary.win_condition_result,
        )
        summary_block["records"] = [
            {
                "tick": r.tick,
                "agent_id": r.agent_id,
                "iteration": r.iteration,
                "command": r.command,
                "solved": r.solved,
                "error": r.error,
            }
            for r in summary.records
        ]
        return {
            "mode": "multi_agent_per_env",
            "scenario": self.scenario.name,
            "expected_flag": expected_flag,
            "solved": summary_block["any_agent_solved"],
            "flags_found": all_flags,
            "multi_agent_summary": summary_block,
            "agents": per_agent_reports,
        }

    @staticmethod
    def _destroy_container_if_exists(container_name: str) -> None:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True,
        )

    @staticmethod
    def _destroy_network_if_exists(network_name: str) -> None:
        subprocess.run(
            ["docker", "network", "rm", network_name],
            capture_output=True,
        )

    def _cleanup_per_env(self) -> None:
        # Containers must come down before networks can be removed.
        for container in self._per_env_containers:
            self._destroy_container_if_exists(container)
        for network in self._per_env_networks:
            self._destroy_network_if_exists(network)
        # The comms network (TCP transport) goes last: it has no
        # containers attached at this point but is tracked separately so
        # we never leak it across runs.
        if self._comms_network is not None:
            self._destroy_network_if_exists(self._comms_network)
            self._comms_network = None
        self._per_env_containers = []
        self._per_env_networks = []

    @staticmethod
    def _print_summary(summary: RunSummary, result: Dict[str, Any]) -> None:
        print("\n" + "=" * 70)
        print(" MULTI-AGENT SUMMARY")
        print("=" * 70)
        print(f" Scenario:    {result['scenario']}")
        print(f" Stop reason: {summary.stop_reason}")
        print(f" Ticks:       {summary.ticks_elapsed}")
        print(f" Any solved:  {result['solved']}")
        print(f" Flags found: {result['flags_found']}")
        if result.get("expected_flag"):
            print(f" Expected:    {result['expected_flag']}")
            correct = result['expected_flag'] in result['flags_found']
            print(f" Correct:     {'YES' if correct else 'NO'}")
        print("=" * 70)

        print("\n Per-agent results:")
        for agent_report in result["agents"]:
            print(f"   - {agent_report['agent_id']} ({agent_report['role']}): "
                  f"solved={agent_report['solved']}, "
                  f"attempts={agent_report['total_attempts']}, "
                  f"flags={agent_report['flags_found']}")
        print("=" * 70 + "\n")
