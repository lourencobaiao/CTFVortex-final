"""Docker-based infrastructure provider for the pivot scenario.

Provisions two containers on two isolated networks with one dual-homed
pivot host. Mirrors the ``subprocess(["docker", ...])`` style used in
``ctf_agents/orchestration/multi_agent.py:_run_per_agent_environments``
so the operational model (stale cleanup → build → run → readiness probe)
stays familiar.

The dual-home step (``docker network connect`` after ``docker run``) is
the only Docker call that's *not* already used by Diamond Vault — it's
what gives the DMZ container a second NIC on the internal network so
``ssh intern@10.99.0.20`` works from inside it.
"""

from __future__ import annotations

import socket
import subprocess
import time
from typing import TYPE_CHECKING, Iterable, List, Optional

from ctf_agents.core.connections import SSHTerminal, TerminalConnection
from ctf_agents.infra.base import Environment, InfrastructureProvider, NodeHandle

if TYPE_CHECKING:  # pragma: no cover
    from ctf_agents.multi_agent.scenarios import Scenario


class _PatientSSHTerminal(SSHTerminal):
    """SSHTerminal with sentinel-based command completion.

    The stock SSHTerminal polls the channel for ~1.5s and returns
    whatever it received. That works for `ls`/`cat` but breaks on
    **nested SSH** (the pivot's defining command): the inner SSH
    spends 2-3 seconds silently doing TCP handshake + key exchange
    before any bytes flow back to the outer paramiko channel. Any
    timeout-based detector either gives up too early (misses the
    flag) or always blocks for the worst case (slow).

    Sentinel approach: append ``; printf '<MARKER>%s' "$?"`` to the
    command. Read from the channel until the marker appears. Result:
    fast commands return immediately; slow commands wait exactly as
    long as they need; failed/hung commands hit ``MAX_WAIT_S`` and we
    bail.

    A side benefit: the trailing ``$?`` lets us strip the marker line
    cleanly without ambiguity about which line is the prompt.
    """

    MARKER = "__CTFPIVOTDONE__"
    # Regex for the COMPLETED-command signal: marker followed by the
    # numeric exit code from `"$?"`. This pattern only appears in bash's
    # actual output, NOT in the visible echo of the command line we
    # sent (which contains the literal `%s`, not a digit).
    _DONE_RE = None  # set in __init__
    MAX_WAIT_S = 25.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import re
        type(self)._DONE_RE = re.compile(re.escape(self.MARKER) + r"(\d+)")

    def execute_command(self, command: str) -> str:
        if not self.is_connected():
            raise Exception("Not connected to terminal")

        self.command_count += 1
        # Compound: run the real command, then printf the marker + exit
        # code. The terminal echoes the WHOLE line back including the
        # literal `%s`, so the marker appears in the echo without a
        # following digit. The actual run produces "<MARKER><N>" where
        # N is the exit code — that's what _DONE_RE matches.
        wrapped = f"{command}; printf '{self.MARKER}%s\\n' \"$?\"\n"
        self.channel.send(wrapped)

        deadline = time.time() + self.MAX_WAIT_S
        buf = ""
        match = None
        while time.time() < deadline:
            if self.channel.recv_ready():
                buf += self.channel.recv(8192).decode(errors="ignore")
                match = self._DONE_RE.search(buf)
                if match is not None:
                    break
            else:
                time.sleep(0.05)

        if match is None:
            # Hit MAX_WAIT_S without seeing the completion signal —
            # return whatever we have so the LLM at least sees something.
            return buf.strip()

        # Carve: everything from after the FIRST newline (which ends the
        # echoed command line) up to the marker = the real output.
        pre = buf[: match.start()]
        if "\n" in pre:
            pre = pre.split("\n", 1)[1]
        return pre.rstrip()


class DockerPivotInfraProvider(InfrastructureProvider):
    """Docker implementation of the pivot topology.

    Stateless across calls: every method reads everything it needs from
    the ``scenario`` / ``env`` it's handed. That makes it safe to
    instantiate at the start of ``deploy()`` and again at ``teardown()``
    in a ``finally`` block even if the first instance was lost to an
    exception.
    """

    READINESS_TIMEOUT_S = 30

    # ── deploy ───────────────────────────────────────────────────────

    def deploy(self, scenario: "Scenario") -> Environment:
        topo = scenario.metadata.get("topology")
        if not topo:
            raise ValueError(
                "DockerPivotInfraProvider.deploy: scenario.metadata['topology'] missing"
            )

        dmz_container = topo["dmz_container"]
        internal_container = topo["internal_container"]
        dmz_network = topo["dmz_network"]
        internal_network = topo["internal_network"]

        # 1. Stale cleanup — idempotent. Captures leftovers from a
        # previous interrupted run. Network removal must come AFTER
        # container removal (Docker refuses to drop a network with
        # endpoints attached).
        print("\n Step 1: Cleaning up stale containers/networks...")
        self._rm_containers([dmz_container, internal_container])
        self._rm_networks([dmz_network, internal_network])

        # 2. Networks. Using --subnet pins the address space so the
        # planter / brief can refer to literal IPs deterministically.
        print(" Step 2: Creating networks...")
        self._create_network(dmz_network, topo["dmz_subnet"])
        self._create_network(internal_network, topo["internal_subnet"])

        # 3. Images. Always rebuild — small images, lets the user edit
        # the Dockerfiles between runs without remembering to rebuild
        # explicitly. Roughly matches Diamond Vault's behavior.
        print(" Step 3: Building images...")
        self._build_image(topo["dmz_image"], topo["dmz_dockerfile"])
        self._build_image(topo["internal_image"], topo["internal_dockerfile"])

        # 4. Containers. Internal first (no port map, single-homed on
        # internal-net), then DMZ (port-mapped on dmz-net). Static IPs
        # on both so the agent brief's "10.99.0.20" claim holds.
        print(" Step 4: Starting containers...")
        self._run_container(
            name=internal_container,
            network=internal_network,
            ip=topo["internal_ip"],
            image=topo["internal_image"],
            port_map=None,
        )
        self._run_container(
            name=dmz_container,
            network=dmz_network,
            ip=topo["dmz_ip_on_dmz_net"],
            image=topo["dmz_image"],
            port_map=(topo["dmz_ssh_port"], 22),
        )

        # 5. Dual-home the DMZ container onto the internal network. This
        # is what makes the pivot real: from inside ctf-dmz, eth1 has
        # an address in 10.99.0.0/24 and can reach ctf-internal.
        print(" Step 5: Dual-homing DMZ onto internal-net...")
        self._network_connect(
            network=internal_network,
            container=dmz_container,
            ip=topo["dmz_ip_on_internal_net"],
        )

        # 6. SSH readiness probe — host side, before handing the
        # terminal off to the orchestrator/agent.
        print(" Step 6: Waiting for sshd on DMZ...")
        if not self._wait_for_tcp("127.0.0.1", topo["dmz_ssh_port"], self.READINESS_TIMEOUT_S):
            raise RuntimeError(
                f"DMZ sshd never came up on port {topo['dmz_ssh_port']} "
                f"within {self.READINESS_TIMEOUT_S}s"
            )
        # Small extra buffer for SSH banner + paramiko handshake — same
        # pattern as multi_agent.py:_wait_for_per_env_sshd.
        time.sleep(1)

        env = Environment(
            env_id="docker-pivot",
            nodes={
                "dmz": NodeHandle(
                    node_id=dmz_container,
                    name="dmz",
                    connection_info={
                        "type": "ssh",
                        "host": "127.0.0.1",
                        "port": topo["dmz_ssh_port"],
                        "user": topo["dmz_ssh_user"],
                        "password": topo["dmz_ssh_password"],
                    },
                ),
                "internal": NodeHandle(
                    node_id=internal_container,
                    name="internal",
                    connection_info={
                        # No host-side SSH — only reachable via pivot
                        # from the DMZ. Included for completeness so the
                        # planter (and tests) can address it by name.
                        "type": "ssh",
                        "host": topo["internal_ip"],
                        "port": 22,
                        "user": topo["dmz_ssh_user"],
                        "password": topo["dmz_ssh_password"],
                    },
                ),
            },
            extra={
                "networks": [dmz_network, internal_network],
                "containers": [dmz_container, internal_container],
            },
        )
        return env

    # ── teardown ─────────────────────────────────────────────────────

    def teardown(self, env: Environment) -> None:
        """Remove containers, then networks. Idempotent."""
        containers: Iterable[str] = env.extra.get("containers") or [
            n.node_id for n in env.nodes.values()
        ]
        networks: Iterable[str] = env.extra.get("networks") or []
        self._rm_containers(list(containers))
        self._rm_networks(list(networks))

    # ── connection ───────────────────────────────────────────────────

    def connection_for(self, env: Environment, node_name: str) -> TerminalConnection:
        """Use :class:`_PatientSSHTerminal` instead of stock SSHTerminal.

        The base ABC's default dispatches on ``connection_info["type"]``
        and returns a plain ``SSHTerminal``; we override to substitute
        the patient variant so nested SSH (the pivot itself!) returns
        output before the read window closes.
        """
        if node_name not in env.nodes:
            raise KeyError(
                f"node {node_name!r} not in environment (have: {list(env.nodes)})"
            )
        info = env.nodes[node_name].connection_info
        return _PatientSSHTerminal(
            host=info["host"],
            port=info["port"],
            username=info["user"],
            password=info["password"],
        )

    # ── readiness ────────────────────────────────────────────────────

    def is_ready(self, env: Environment) -> bool:
        info = env.nodes.get("dmz")
        if not info:
            return False
        return self._probe_tcp(info.connection_info["host"], info.connection_info["port"])

    # ── helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _rm_containers(names: List[str]) -> None:
        for name in names:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True,  # ignore exit code: stale-cleanup is best-effort
            )

    @staticmethod
    def _rm_networks(names: List[str]) -> None:
        for name in names:
            subprocess.run(
                ["docker", "network", "rm", name],
                capture_output=True,
            )

    @staticmethod
    def _create_network(name: str, subnet: str) -> None:
        cmd = ["docker", "network", "create", "--subnet", subnet, name]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  - network {name} ({subnet})")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"docker network create {name} failed: "
                f"{e.stderr.decode(errors='ignore').strip()}"
            ) from e

    @staticmethod
    def _build_image(tag: str, dockerfile: str) -> None:
        cmd = ["docker", "build", "-t", tag, "-f", dockerfile, "."]
        print(f"  - building {tag} from {dockerfile}")
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"docker build {tag} failed: "
                f"{e.stderr.decode(errors='ignore').strip()}"
            ) from e

    @staticmethod
    def _run_container(
        *,
        name: str,
        network: str,
        ip: str,
        image: str,
        port_map: Optional[tuple],
    ) -> None:
        cmd = ["docker", "run", "-d", "--name", name, "--network", network, "--ip", ip]
        if port_map is not None:
            host_port, container_port = port_map
            cmd += ["-p", f"{host_port}:{container_port}"]
        cmd.append(image)
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            mapping = f", ssh→{port_map[0]}" if port_map else ""
            print(f"  - container {name} on {network} ({ip}){mapping}")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"docker run {name} failed: "
                f"{e.stderr.decode(errors='ignore').strip()}"
            ) from e

    @staticmethod
    def _network_connect(*, network: str, container: str, ip: str) -> None:
        cmd = ["docker", "network", "connect", "--ip", ip, network, container]
        try:
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  - {container} joined {network} ({ip})")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"docker network connect {network}<-{container} failed: "
                f"{e.stderr.decode(errors='ignore').strip()}"
            ) from e

    @classmethod
    def _wait_for_tcp(cls, host: str, port: int, timeout_s: int) -> bool:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if cls._probe_tcp(host, port):
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def _probe_tcp(host: str, port: int) -> bool:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            return False
