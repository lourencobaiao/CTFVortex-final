"""MailboxRegistry — per-agent inboxes backed by EventBus.

The coordinator owns one ``MailboxRegistry``. Each agent in the roster
is registered with its ``agent_id`` and (optional) ``team_id``. When an
agent publishes a ``Message`` on the bus, every matching inbox buffers
it until the coordinator drains it at the start of that agent's next
turn — this avoids mid-step interrupts.

Three routing rules:
    - direct:      ``recipient == agent_id``
    - team:        ``recipient == "*team:<team_id>"``
    - broadcast:   ``recipient == "*broadcast"``
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional

from ctf_agents.core.primitives import EventBus
from ctf_agents.multi_agent.protocol import (
    CHANNEL_ENV,
    Message,
    RECIPIENT_BROADCAST,
    TEAM_PREFIX,
)


MSG_EVENT = "multi_agent.message"
ENV_EVENT_PREFIX = "env."


class MailboxRegistry:
    """Owns one inbox per agent and routes published messages.

    Thread-safety: relies on the EventBus's internal locking for
    publish/subscribe; the inbox itself is not locked (Phase 1 runs
    single-threaded round-robin, so no contention).
    """

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self._inboxes: Dict[str, Deque[Message]] = defaultdict(deque)
        self._team_membership: Dict[str, Optional[str]] = {}
        # Counters for telemetry / metrics.
        self.total_sent: int = 0
        self.total_delivered: int = 0
        self.total_dropped_over_cap: int = 0
        self.sent_per_agent_total: Dict[str, int] = defaultdict(int)
        # Per-tick counters (reset by coordinator each tick via reset_per_tick_counters).
        self._sent_this_tick: Dict[str, int] = defaultdict(int)
        # Subscribe once; routing happens in _on_message.
        self.event_bus.subscribe(MSG_EVENT, self._on_message)

    def register_agent(self, agent_id: str, team_id: Optional[str] = None) -> None:
        """Make ``agent_id`` known to the registry (creates an empty inbox)."""
        self._inboxes.setdefault(agent_id, deque())
        self._team_membership[agent_id] = team_id

    # ------------------------------------------------------------------
    # Send / deliver
    # ------------------------------------------------------------------

    def send(self, message: Message, per_agent_cap: Optional[int] = None) -> int:
        """Publish a message on the bus.

        Returns the number of recipients the message was delivered to
        (after cap enforcement). Dropping above the cap is counted
        separately in ``total_dropped_over_cap``.
        """
        if per_agent_cap is not None and message.sender != "env":
            if self._sent_this_tick[message.sender] >= per_agent_cap:
                self.total_dropped_over_cap += 1
                return 0

        self.total_sent += 1
        if message.sender != "env":
            self._sent_this_tick[message.sender] += 1
            self.sent_per_agent_total[message.sender] += 1
        self.event_bus.publish(MSG_EVENT, message=message)
        return self._last_delivery_count

    def send_env_signal(self, signal: str, content: str,
                        metadata: Optional[Dict] = None) -> None:
        """Emit an environment event visible to every agent.

        Uses a separate event name (``env.<signal>``) so scenarios can
        subscribe directly if they want — and also delivers into every
        inbox as a CHANNEL_ENV message for uniform perceive() handling.
        """
        env_msg = Message(
            sender="env",
            recipients=[RECIPIENT_BROADCAST],
            channel=CHANNEL_ENV,
            content=content,
            metadata={"signal": signal, **(metadata or {})},
        )
        self.event_bus.publish(ENV_EVENT_PREFIX + signal, message=env_msg)
        self.send(env_msg)

    def drain(self, agent_id: str) -> List[Message]:
        """Return and clear all pending messages for ``agent_id``."""
        inbox = self._inboxes.get(agent_id)
        if not inbox:
            return []
        messages = list(inbox)
        inbox.clear()
        return messages

    def peek(self, agent_id: str) -> List[Message]:
        """Non-destructive inspection (for tests/metrics)."""
        return list(self._inboxes.get(agent_id, ()))

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    _last_delivery_count: int = 0

    def _on_message(self, message: Message) -> None:
        delivered = 0
        for agent_id in self._recipients_for(message):
            if agent_id == message.sender:
                # Don't echo messages back to their sender.
                continue
            self._inboxes[agent_id].append(message)
            delivered += 1
        self.total_delivered += delivered
        self._last_delivery_count = delivered

    def _recipients_for(self, message: Message) -> List[str]:
        resolved: List[str] = []
        for rec in message.recipients:
            if rec == RECIPIENT_BROADCAST:
                resolved.extend(self._inboxes.keys())
            elif rec.startswith(TEAM_PREFIX):
                team = rec[len(TEAM_PREFIX):]
                resolved.extend(
                    aid for aid, t in self._team_membership.items()
                    if t == team
                )
            else:
                if rec in self._inboxes:
                    resolved.append(rec)
        # Dedupe while preserving order.
        seen = set()
        ordered: List[str] = []
        for aid in resolved:
            if aid not in seen:
                seen.add(aid)
                ordered.append(aid)
        return ordered

    # ------------------------------------------------------------------
    # Metrics snapshot
    # ------------------------------------------------------------------

    def metrics_snapshot(self) -> Dict[str, object]:
        return {
            "total_sent": self.total_sent,
            "total_delivered": self.total_delivered,
            "total_dropped_over_cap": self.total_dropped_over_cap,
            "sent_per_agent": dict(self.sent_per_agent_total),
        }

    def reset_per_tick_counters(self) -> None:
        """Reset per-tick sender counters (called by coordinator each tick)."""
        self._sent_this_tick.clear()
