"""MessageTransport — interface contract between the coordinator and the
underlying messaging substrate.

There are two implementations:

* ``MailboxRegistry`` (in :mod:`ctf_agents.multi_agent.mailbox`) — in-process
  EventBus + per-agent deques. Fast, single-host, used as the *fallback*.
* ``TcpTransport`` (in :mod:`ctf_agents.multi_agent.tcp_transport`) — sends
  happen out-of-process via the ``agent-send`` CLI running inside the agent's
  container; this class observes the wire by tailing
  ``/var/log/agent-tcp.log`` on each container. Default transport going
  forward; the same primitives carry over to GNS3 in Phase 4.

The coordinator never imports either concrete class — it accepts anything
satisfying ``MessageTransport``. This keeps the agent and coordinator code
transport-agnostic and lets scenarios opt into either mode via
``MessagingConfig.transport``.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Protocol, runtime_checkable

from ctf_agents.multi_agent.protocol import Message


@runtime_checkable
class MessageTransport(Protocol):
    """Methods the coordinator actually calls on the messaging layer.

    A new transport only needs to satisfy this shape — no inheritance
    required. ``MailboxRegistry`` already matches it; ``TcpTransport``
    implements it explicitly.
    """

    def register_agent(self, agent_id: str, team_id: Optional[str] = None) -> None:
        """Make ``agent_id`` known to the transport (creates an empty inbox)."""
        ...

    def send(self, message: Message, per_agent_cap: Optional[int] = None) -> int:
        """Deliver ``message`` to its recipients.

        For TCP-mode transports this is typically a no-op — the agent's
        shell command (``agent-send …``) already wrote to the wire by the
        time this could be called. For in-process transports it publishes
        on the bus. Return value: number of recipients delivered to.
        """
        ...

    def drain(self, agent_id: str) -> List[Message]:
        """Return and clear all pending messages for ``agent_id``.

        Called by the coordinator right before each ``agent.step()``.
        """
        ...

    def peek(self, agent_id: str) -> List[Message]:
        """Non-destructive inspection (for tests / metrics)."""
        ...

    def metrics_snapshot(self) -> Dict[str, object]:
        """Aggregate counters surfaced in the multi-agent summary."""
        ...

    def reset_per_tick_counters(self) -> None:
        """Reset per-tick state at the end of each tick."""
        ...
