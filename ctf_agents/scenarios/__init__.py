"""Scenario builders.

Scenarios are role-agnostic descriptions of *what* a run should do
(roster, win condition, topology, metadata). They are intentionally
decoupled from the orchestrator and infrastructure backend — the same
``Scenario`` could in principle be run on Docker today or on GNS3
tomorrow once the matching :class:`~ctf_agents.infra.base.InfrastructureProvider`
exists.

Diamond Vault still lives under ``ctf_agents/multi_agent/`` for historical
reasons; new scenarios land here. Don't move Diamond Vault — its import
path is part of the public surface.
"""

from ctf_agents.scenarios.docker_pivot import (
    DMZ_CREDS,
    FLAG,
    INTERNAL_CREDS,
    INTERNAL_IP,
    PivotTopologySpec,
    build_docker_pivot_scenario,
)

__all__ = [
    "DMZ_CREDS",
    "FLAG",
    "INTERNAL_CREDS",
    "INTERNAL_IP",
    "PivotTopologySpec",
    "build_docker_pivot_scenario",
]
