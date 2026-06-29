"""Scenario specification for multi-agent runs.

A Scenario is a role-agnostic description of *what* the coordinator
should run: which agents participate, how long to run, when to stop,
and what messaging constraints apply. It does not know about attackers
vs defenders — cooperative and adversarial scenarios both use the same
dataclass, differentiated only by roster composition and win_condition.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional


# Win-condition signature:
#   (coordinator) -> Optional[dict]
# Returns None if the run should continue, or a dict describing the
# terminal state (reason, winner_ids, etc.) if the run should stop.
WinCondition = Callable[[Any], Optional[Dict[str, Any]]]


@dataclass
class AgentSpec:
    """Declarative description of one agent in a scenario roster.

    The orchestrator materializes these into concrete CTFAgent
    instances (wiring in AI provider, terminal, event_bus, shared_state).
    """

    agent_id: str
    agent_class: str               # "web" | "jeopardy" | "defender" (future)
    role: str = "attacker"         # matches AgentRole values
    team_id: Optional[str] = None
    model: Optional[str] = None    # falls back to scenario default
    extra: Dict[str, Any] = field(default_factory=dict)


def any_agent_solved(coordinator) -> Optional[Dict[str, Any]]:
    """Default win condition: stop as soon as any agent finds a flag."""
    for agent in coordinator.agents:
        if agent.solved:
            return {
                "reason": "flag_captured",
                "winner_ids": [agent.agent_id],
                "winner_flags": list(agent.discovered_flags),
            }
    return None


def all_agents_solved(coordinator) -> Optional[Dict[str, Any]]:
    """Alternative win condition: stop only when every agent has solved."""
    if all(agent.solved for agent in coordinator.agents):
        return {
            "reason": "all_solved",
            "winner_ids": [a.agent_id for a in coordinator.agents],
        }
    return None


@dataclass
class MessagingConfig:
    """Scenario-level constraints on the messaging layer.

    Everything here is enforced by the coordinator / mailbox, not by
    agents themselves. Deception is emergent from prompting and lives
    outside this config.
    """

    max_message_length: int = 2000
    max_messages_per_tick: int = 5
    count_messages_against_iterations: bool = False
    # Phase 4 knobs — scaffolding only, default off:
    delivery_delay_ticks: int = 0
    drop_probability: float = 0.0
    # Messaging transport. "tcp" runs each agent's container with the
    # tcp_listener daemon and exposes `agent-send` / `agent-inbox` CLIs
    # to the LLM; the orchestrator observes the wire by tailing each
    # container's /var/log/agent-tcp.log. "inproc" keeps the legacy
    # in-process MailboxRegistry where `<MSG>` tags are parsed out of
    # LLM output. Default is "tcp" since GNS3 (Phase 4) needs a real
    # network transport.
    transport: Literal["tcp", "inproc"] = "tcp"


@dataclass
class Scenario:
    """Full scenario specification.

    Role-agnostic: a cooperative 2-attacker run and a red-vs-blue run
    are both expressed as Scenarios, distinguished only by their
    roster and win_condition.
    """

    name: str
    roster: List[AgentSpec]
    max_ticks: int = 30
    win_condition: WinCondition = any_agent_solved
    messaging: MessagingConfig = field(default_factory=MessagingConfig)
    # Optional hook fired at the end of each tick; receives the
    # coordinator instance and the just-completed tick number.
    on_tick: Optional[Callable[[Any, int], None]] = None
    # Free-form scenario metadata (challenge type, description, etc.).
    metadata: Dict[str, Any] = field(default_factory=dict)
