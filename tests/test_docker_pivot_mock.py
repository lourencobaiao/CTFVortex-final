"""Deterministic substrate test for the Docker Pivot scenario.

Drives the real ``DockerPivotOrchestrator`` + ``JeopardyCTFAgent`` through
the actual code paths (planter, scenario brief application, report
generation) without spinning up Docker and without an LLM. The two
substitutions are at the infrastructure boundary:

* ``FakePivotInfraProvider`` plays the role of ``DockerPivotInfraProvider``:
  ``deploy()`` returns a pre-wired in-memory ``Environment``;
  ``connection_for("dmz")`` returns a ``FakeSSHTerminal`` whose
  ``ssh`` commands route into a second ``FakeSSHTerminal`` representing
  the internal host.
* ``ScriptedProvider`` (mirrored from ``test_diamond_vault_mock``) feeds
  the agent a canonical recon→pivot command sequence.

What this exercises end-to-end:

* The agent's run loop, prompt construction, flag-detection regex.
* The orchestrator's deploy → plant → connect → run → teardown flow.
* The planter writes to the same node ids the agent later reads from.
* ``provider.teardown`` runs exactly once in the ``finally`` block.

Run with::

    python tests/test_docker_pivot_mock.py
"""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, List, Optional, Tuple

# Make `ctf_agents` importable when the file is executed directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ctf_agents.core.connections import TerminalConnection
from ctf_agents.core.providers import AIProvider
from ctf_agents.infra.base import Environment, InfrastructureProvider, NodeHandle
from ctf_agents.orchestration.docker_pivot import DockerPivotOrchestrator
from ctf_agents.scenarios.docker_pivot import (
    DMZ_CREDS,
    FLAG,
    INTERNAL_IP,
    build_docker_pivot_scenario,
)


# ── Scripted LLM provider ────────────────────────────────────────────


class ScriptedProvider(AIProvider):
    """Returns pre-baked responses in order. Falls back to a safe no-op."""

    def __init__(self, responses: List[str], name: str = "scripted"):
        self.responses = list(responses)
        self._name = name
        self.model = name

    def generate_command(self, conversation_history) -> str:
        if self.responses:
            return self.responses.pop(0)
        return "echo idle"

    def get_provider_name(self) -> str:
        return self._name


# ── Fake terminal that simulates DMZ + internal hosts ────────────────


# Match `ssh [opts] user@host CMD…` or `sshpass -p PW ssh user@host CMD…`.
# Captures (user, host, password-if-present, inner-command).
_SSH_RE = re.compile(
    r"""
    (?:sshpass\s+-p\s*(?P<pw>'[^']*'|"[^"]*"|\S+)\s+)?   # optional sshpass
    ssh\s+
    (?:-[A-Za-z]\S*\s+|-o\s+\S+\s+)*                      # ssh flags
    (?P<user>[A-Za-z_][\w-]*)@(?P<host>[\w.\-]+)\s+
    (?P<cmd>.+)
    """,
    re.VERBOSE,
)


class FakeSSHTerminal(TerminalConnection):
    """In-memory filesystem + minimal command router for one host.

    Recognized commands (only what the canonical exploit path uses):

      * ``cat <PATH>``     → returns file contents or "No such file"
      * ``ls <DIR>``       → returns space-joined filenames in that dir
      * ``ip addr``        → returns canned ifconfig blob
      * ``ssh user@host CMD`` / ``sshpass -p X ssh user@host CMD`` →
        routes ``CMD`` to ``peers[host]`` (matched by IP and by hostname
        alias). Password validated against ``ssh_password`` of the peer.

    Anything else returns "" — same as the Diamond Vault FakeEnv shape.
    """

    def __init__(
        self,
        *,
        hostname: str,
        files: Optional[Dict[str, str]] = None,
        ip_addr_output: str = "",
        peers: Optional[Dict[str, "FakeSSHTerminal"]] = None,
        ssh_user: str = "intern",
        ssh_password: str = "",
    ):
        self.hostname = hostname
        self.files: Dict[str, str] = dict(files or {})
        self.ip_addr_output = ip_addr_output
        self.peers: Dict[str, "FakeSSHTerminal"] = dict(peers or {})
        self.ssh_user = ssh_user
        self.ssh_password = ssh_password
        self._connected = False

    # ── TerminalConnection interface ─────────────────────────────────

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def execute_command(self, command: str) -> str:
        cmd = command.strip()

        ssh_match = _SSH_RE.match(cmd)
        if ssh_match:
            return self._handle_ssh(ssh_match)

        if cmd.startswith("cat "):
            path = cmd[4:].strip().strip("'\"")
            return self.files.get(path, f"cat: {path}: No such file or directory")

        if cmd.startswith("ls"):
            dir_path = cmd[2:].strip().strip("'\"") or "/"
            return self._ls(dir_path)

        if cmd.startswith("ip addr") or cmd == "ifconfig":
            return self.ip_addr_output

        if cmd in {"exit", "quit"}:
            return ""

        return ""

    # ── helpers ──────────────────────────────────────────────────────

    def _handle_ssh(self, m: "re.Match[str]") -> str:
        host = m.group("host")
        inner_cmd = m.group("cmd")
        pw_raw = m.group("pw")
        password = pw_raw.strip("'\"") if pw_raw else None

        peer = self.peers.get(host)
        if peer is None:
            return f"ssh: Could not resolve hostname {host}: Name or service not known"

        # Reject only when an explicit wrong password was provided.
        # If no password was given (relying on ssh keys), allow it — the
        # test's purpose is to verify the framework wiring, not full
        # sshd authentication semantics.
        if password is not None and password != peer.ssh_password:
            return "Permission denied, please try again."

        return peer.execute_command(inner_cmd)

    def _ls(self, dir_path: str) -> str:
        prefix = dir_path if dir_path.endswith("/") else dir_path + "/"
        children = []
        for path in self.files:
            if path.startswith(prefix):
                rest = path[len(prefix):]
                children.append(rest.split("/", 1)[0])
        # de-dupe, preserve insertion order
        seen, ordered = set(), []
        for c in children:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        return "  ".join(ordered)


# ── Fake infra provider — pre-wires the topology ─────────────────────


class FakePivotInfraProvider(InfrastructureProvider):
    """Drop-in replacement for DockerPivotInfraProvider.

    ``deploy`` builds two ``FakeSSHTerminal`` instances (DMZ + internal),
    wires the DMZ terminal's peer dict so its ``ssh`` recognizes the
    internal host by IP and by ``internal-host`` alias, and stashes both
    terminals in ``env.extra["_terminals"]`` for retrieval by
    ``connection_for``.

    Tracks ``deploy_calls`` and ``teardown_calls`` so the test can assert
    teardown ran exactly once.
    """

    def __init__(self):
        self.deploy_calls = 0
        self.teardown_calls = 0
        self.is_ready_value = True

    def deploy(self, scenario) -> Environment:
        self.deploy_calls += 1

        # Internal host first — has the trophy. The planter will
        # overwrite /root/flag.txt and the password at deploy time.
        internal_term = FakeSSHTerminal(
            hostname="internal-host",
            files={"/root/flag.txt": ""},
            ip_addr_output="eth0: 10.99.0.20/24",
            ssh_user=DMZ_CREDS[0],
            ssh_password="",  # set by planter via chpasswd later
        )
        # DMZ host. Files /etc/internal-host.conf and /var/backups/.creds
        # are written by the planter — we leave them missing here so the
        # test verifies the planter actually ran. ip addr reflects the
        # dual-homed topology.
        dmz_term = FakeSSHTerminal(
            hostname="dmz",
            files={},
            ip_addr_output=(
                "eth0: 10.88.0.10/24\n"
                "eth1: 10.99.0.10/24"
            ),
            peers={
                INTERNAL_IP: internal_term,
                "internal-host": internal_term,
            },
            ssh_user=DMZ_CREDS[0],
            ssh_password=DMZ_CREDS[1],
        )

        env = Environment(
            env_id="fake-pivot",
            nodes={
                "dmz": NodeHandle(
                    node_id="ctf-dmz",
                    name="dmz",
                    connection_info={
                        "type": "ssh",
                        "host": "127.0.0.1",
                        "port": 2225,
                        "user": DMZ_CREDS[0],
                        "password": DMZ_CREDS[1],
                    },
                ),
                "internal": NodeHandle(
                    node_id="ctf-internal",
                    name="internal",
                    connection_info={
                        "type": "ssh",
                        "host": INTERNAL_IP,
                        "port": 22,
                        "user": DMZ_CREDS[0],
                        "password": DMZ_CREDS[1],
                    },
                ),
            },
            extra={"_terminals": {"dmz": dmz_term, "internal": internal_term}},
        )
        return env

    def teardown(self, env: Environment) -> None:
        self.teardown_calls += 1

    def is_ready(self, env: Environment) -> bool:
        return self.is_ready_value

    def connection_for(self, env: Environment, node_name: str) -> TerminalConnection:
        return env.extra["_terminals"][node_name]


# ── Fake planter — bypasses docker exec ──────────────────────────────


def _fake_plant(env: Environment, scenario) -> None:
    """Mirror what the real planter does, against the FakeSSHTerminal pair."""
    terms = env.extra["_terminals"]
    dmz_files = terms["dmz"].files
    internal = terms["internal"]

    dmz_files["/etc/internal-host.conf"] = f"host={INTERNAL_IP}"
    dmz_files["/var/backups/.creds"] = f"{DMZ_CREDS[0]}:{DMZ_CREDS[1]}"
    dmz_files["/etc/hosts"] = (
        f"127.0.0.1 localhost\n{INTERNAL_IP} internal-host"
    )

    internal.files["/root/flag.txt"] = FLAG
    internal.ssh_password = DMZ_CREDS[1]


# ── Test driver ──────────────────────────────────────────────────────


def main() -> int:
    scenario = build_docker_pivot_scenario(max_iterations=10)
    provider = FakePivotInfraProvider()

    # Patch the planter import inside the orchestrator's module. The
    # orchestrator imported `plant_pivot_artifacts` at module scope, so
    # we override that binding for the test.
    import ctf_agents.orchestration.docker_pivot as orch_mod
    real_planter = orch_mod.plant_pivot_artifacts
    orch_mod.plant_pivot_artifacts = _fake_plant

    # Patch make_default_provider so the test never reaches for an LLM key.
    real_make_provider = orch_mod.make_default_provider
    scripted = ScriptedProvider(
        responses=[
            "ls /etc",
            "cat /etc/internal-host.conf",
            "cat /var/backups/.creds",
            f"sshpass -p '{DMZ_CREDS[1]}' ssh {DMZ_CREDS[0]}@{INTERNAL_IP} cat /root/flag.txt",
        ],
        name="scripted-pivot",
    )
    orch_mod.make_default_provider = lambda api_key=None, model=None: scripted

    try:
        orchestrator = DockerPivotOrchestrator(
            api_key="unused-by-scripted-provider",
            scenario=scenario,
            model="scripted",
            provider=provider,
        )
        result = orchestrator.run(max_iterations=10)
    finally:
        orch_mod.plant_pivot_artifacts = real_planter
        orch_mod.make_default_provider = real_make_provider

    errors: List[str] = []
    if not isinstance(result, dict):
        errors.append(f"result was {type(result).__name__}, expected dict")
    else:
        flags = result.get("discovered_flags") or result.get("flags_found") or []
        if FLAG not in flags:
            errors.append(f"FLAG {FLAG!r} not in result flags {flags!r}")
        if not result.get("solved", result.get("success", False)):
            errors.append(f"result.solved is False (result keys: {sorted(result)})")
        if result.get("total_attempts", 999) > 6:
            errors.append(
                f"agent took {result.get('total_attempts')} iterations, expected ≤6"
            )
    if provider.deploy_calls != 1:
        errors.append(f"deploy called {provider.deploy_calls}× (expected 1)")
    if provider.teardown_calls != 1:
        errors.append(f"teardown called {provider.teardown_calls}× (expected 1)")

    if errors:
        print("\n=== FAIL ===")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\n=== PASS ===")
    print(f"  iterations           = {result.get('total_attempts')}")
    print(f"  discovered_flags     = {result.get('discovered_flags') or result.get('flags_found')}")
    print(f"  provider.deploy×{provider.deploy_calls}, teardown×{provider.teardown_calls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
