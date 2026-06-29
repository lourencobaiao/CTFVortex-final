"""Infrastructure provider abstraction.

The framework's first multi-host scenario (Diamond Vault) provisioned
Docker directly inside ``MultiAgentOrchestrator``. As soon as a *second*
scenario needs multi-host topology (the Docker Pivot challenge — and
later a real GNS3 backend) the orchestrator would otherwise have to grow
an ``if scenario.metadata['infra'] == ...`` branch per backend.

The ``InfrastructureProvider`` ABC factors that out. A scenario declares
*what* topology it needs; a provider knows *how* to spin it up. The
orchestrator only ever sees an ``Environment`` and asks the provider for
``TerminalConnection`` handles to its nodes.

Existing Docker code in ``ctf_agents/orchestration/multi_agent.py`` is
deliberately left alone for now — migrating it is a chore for when a
third backend appears. This ABC is shaped to make that migration easy
when it happens.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:  # pragma: no cover
    from ctf_agents.core.connections import TerminalConnection
    from ctf_agents.multi_agent.scenarios import Scenario


@dataclass
class NodeHandle:
    """One reachable node inside an :class:`Environment`.

    ``node_id`` is provider-opaque (Docker container name, GNS3 node UUID,
    EC2 instance id, …). ``name`` is the scenario-level logical label that
    the orchestrator / scenario builder uses ("dmz", "internal").

    ``connection_info`` is a flat dict tagged by ``"type"``; the default
    :meth:`InfrastructureProvider.connection_for` dispatches on it. Today
    only ``"ssh"`` is wired; ``"gns3_console"`` is the planned next type.
    """

    node_id: str
    name: str
    connection_info: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Environment:
    """A live, deployed set of nodes. Returned by :meth:`InfrastructureProvider.deploy`.

    ``extra`` carries backend-specific bookkeeping the provider itself
    needs at teardown time (network names for Docker, project id for
    GNS3, …). The orchestrator should treat it as opaque.
    """

    env_id: str
    nodes: Dict[str, NodeHandle] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)


class InfrastructureProvider(ABC):
    """Pluggable backend for deploying multi-host scenarios."""

    @abstractmethod
    def deploy(self, scenario: "Scenario") -> Environment:
        """Provision the topology described by ``scenario.metadata`` and
        return a live :class:`Environment`. Implementations must be
        idempotent at the level of stale leftovers from previous runs
        (the Docker provider drops stale containers + networks with the
        same names before creating fresh ones)."""

    @abstractmethod
    def teardown(self, env: Environment) -> None:
        """Release every resource described by ``env``. Must be idempotent
        — safe to call after a partial deploy, or twice in a row."""

    @abstractmethod
    def is_ready(self, env: Environment) -> bool:
        """Lightweight liveness check (TCP socket probe, REST ping, …).
        Should not block longer than a couple of seconds; long readiness
        waits belong inside :meth:`deploy`."""

    def connection_for(self, env: Environment, node_name: str) -> "TerminalConnection":
        """Build a :class:`TerminalConnection` for one of ``env``'s nodes.

        The default impl dispatches on ``connection_info['type']``;
        subclasses can override entirely for custom transports. Today only
        ``"ssh"`` is recognized — GNS3 console transport will add a second
        case here when that backend lands.
        """
        if node_name not in env.nodes:
            raise KeyError(f"node {node_name!r} not in environment (have: {list(env.nodes)})")
        info = env.nodes[node_name].connection_info
        kind = info.get("type")
        if kind == "ssh":
            from ctf_agents.core.connections import SSHTerminal  # lazy import: paramiko
            return SSHTerminal(
                host=info["host"],
                port=info["port"],
                username=info["user"],
                password=info["password"],
            )
        raise ValueError(
            f"InfrastructureProvider.connection_for: unsupported connection "
            f"type {kind!r} for node {node_name!r}"
        )
