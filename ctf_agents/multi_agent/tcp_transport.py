"""TcpTransport — observe TCP-mode messaging from outside the container.

Architecture recap (see the plan / DIAMOND_VAULT design notes):

* Each agent container runs ``tcp_listener.py`` on port 7000 and offers
  ``agent-send`` / ``agent-inbox`` CLIs to its LLM. The agent issues
  ``agent-send peer "body"`` as a shell command; the listener on the peer
  writes the envelope to ``/var/log/agent-tcp.log``.
* The orchestrator never *sends* or *routes* anything in TCP mode — all of
  that happens inside the comms network between containers. The
  orchestrator's job is to *observe* the wire so the coordinator still
  has an inbox-per-agent view (to feed ``agent.perceive()``) and so
  metrics keep working.

This class is that observer. It exposes the same interface as
``MailboxRegistry`` (see :mod:`ctf_agents.multi_agent.transport`) so the
coordinator code is identical for both transports:

* ``send`` is a no-op — the agent's CLI already wrote to the wire by the
  time we'd be called. Per-tick caps are enforced at the scenario level
  (briefing) rather than here, since we can't prevent a send after the
  fact.
* ``drain(agent_id)`` tails ``/var/log/agent-tcp.log`` on that agent's
  container starting from the byte offset we last saw, parses each new
  JSON envelope, advances the offset, and returns the messages as
  ``Message`` objects.
* ``metrics_snapshot`` reports totals derived from those envelopes
  (sent_per_agent comes from the ``from`` field; total_sent equals total
  envelopes seen, since the listener only writes on successful receipt).

A ``tail_fn`` is injected at construction so this class is testable
without paramiko/SSH (tests pass a fake; production passes the default
paramiko-backed implementation).
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from ctf_agents.multi_agent.protocol import (
    CHANNEL_DIRECT,
    Message,
)


# Path on the agent's container that the listener appends every received
# envelope to. Tailed by the orchestrator over SSH for the inbox view.
DEFAULT_AUDIT_LOG_PATH = "/var/log/agent-tcp.log"


# Signature: (agent_id, byte_offset, log_path) -> (new_bytes, new_offset)
TailFn = Callable[[str, int, str], Tuple[bytes, int]]


@dataclass
class _AgentSlot:
    """Per-agent bookkeeping kept entirely in the orchestrator process."""

    agent_id: str
    team_id: Optional[str]
    offset: int = 0
    inbox_pending: List[Message] = None  # buffered between drains if needed

    def __post_init__(self):
        if self.inbox_pending is None:
            self.inbox_pending = []


class TcpTransport:
    """Out-of-process transport — reads per-container audit logs.

    Parameters
    ----------
    tail_fn:
        Callable that fetches new bytes from an agent's audit log,
        given the agent id, last byte offset, and log path.  Returns
        ``(new_bytes, new_offset)``.  Real implementations use SSH;
        tests can inject any function.
    log_path:
        Where the listener writes its audit log inside each container.
        Defaults to ``/var/log/agent-tcp.log`` to match
        ``tcp_listener.py``.
    """

    def __init__(
        self,
        tail_fn: TailFn,
        log_path: str = DEFAULT_AUDIT_LOG_PATH,
    ):
        self._tail = tail_fn
        self._log_path = log_path
        self._slots: Dict[str, _AgentSlot] = {}
        # Counters surfaced via metrics_snapshot(). Kept in the same
        # shape as MailboxRegistry's so the scoring engine doesn't care
        # which transport produced them.
        self.total_sent: int = 0
        self.total_delivered: int = 0
        self.total_dropped_over_cap: int = 0  # not enforced in TCP mode
        self.sent_per_agent_total: Dict[str, int] = defaultdict(int)

    # ------------------------------------------------------------------
    # Transport protocol
    # ------------------------------------------------------------------

    def register_agent(self, agent_id: str, team_id: Optional[str] = None) -> None:
        if agent_id not in self._slots:
            self._slots[agent_id] = _AgentSlot(agent_id=agent_id, team_id=team_id)

    def send(self, message: Message, per_agent_cap: Optional[int] = None) -> int:
        """No-op for TCP mode.

        The agent already pushed the message via the in-container
        ``agent-send`` CLI. This method exists only to satisfy the
        ``MessageTransport`` interface for code paths that call it
        unconditionally. Returns 0 so the coordinator's "delivered count"
        bookkeeping doesn't double-count what we'll re-discover when we
        tail the receiver's log next tick.
        """
        return 0

    def drain(self, agent_id: str) -> List[Message]:
        """Return new messages received by ``agent_id`` since the last drain."""
        slot = self._slots.get(agent_id)
        if slot is None:
            return []

        # Any messages buffered from a previous drain (e.g., received
        # multiple between two drains) are flushed first; then we pull
        # fresh ones from the log.
        out: List[Message] = list(slot.inbox_pending)
        slot.inbox_pending = []

        new_bytes, new_offset = self._tail(agent_id, slot.offset, self._log_path)
        slot.offset = new_offset

        for envelope in _parse_envelopes(new_bytes):
            msg = _envelope_to_message(envelope)
            out.append(msg)
            self.total_sent += 1
            self.total_delivered += 1
            sender = msg.sender
            if sender and sender != "env":
                self.sent_per_agent_total[sender] += 1

        return out

    def peek(self, agent_id: str) -> List[Message]:
        """Non-destructive peek — returns what's buffered (does not tail)."""
        slot = self._slots.get(agent_id)
        if slot is None:
            return []
        return list(slot.inbox_pending)

    def metrics_snapshot(self) -> Dict[str, object]:
        return {
            "total_sent": self.total_sent,
            "total_delivered": self.total_delivered,
            "total_dropped_over_cap": self.total_dropped_over_cap,
            "sent_per_agent": dict(self.sent_per_agent_total),
        }

    def reset_per_tick_counters(self) -> None:
        """No per-tick caps enforced in TCP mode — nothing to reset."""
        return


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _parse_envelopes(raw: bytes) -> List[Dict[str, object]]:
    """Parse newline-delimited JSON envelopes; skip malformed lines."""
    if not raw:
        return []
    out: List[Dict[str, object]] = []
    for line in raw.decode("utf-8", errors="replace").split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            env = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(env, dict) and "from" in env and "to" in env and "body" in env:
            out.append(env)
    return out


def _envelope_to_message(env: Dict[str, object]) -> Message:
    metadata: Dict[str, object] = {}
    if "ts" in env:
        metadata["timestamp"] = env["ts"]
    return Message(
        sender=str(env["from"]),
        recipients=[str(env["to"])],
        channel=CHANNEL_DIRECT,
        content=str(env.get("body", "")),
        metadata=metadata,
    )


# ----------------------------------------------------------------------
# Default paramiko-backed tail (real production path)
# ----------------------------------------------------------------------


def make_paramiko_tail(
    ssh_targets: Dict[str, Dict[str, object]],
) -> TailFn:
    """Build a ``tail_fn`` that reads remote logs over SSH (paramiko).

    Parameters
    ----------
    ssh_targets:
        Map ``{agent_id: {"host": str, "port": int, "username": str,
        "password": str}}`` — one entry per container the transport
        needs to observe. (The orchestrator already has this info from
        the per-agent env specs.)

    The returned callable opens a transient SSH connection per call.
    For the tiny payloads we expect (one short JSON line per tick at
    most) the connection overhead is negligible. If profiling shows it
    matters we can cache a paramiko ``SSHClient`` per agent.
    """
    import paramiko  # local import; only needed in real runs

    def _tail(agent_id: str, offset: int, log_path: str) -> Tuple[bytes, int]:
        target = ssh_targets.get(agent_id)
        if target is None:
            return b"", offset
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=str(target["host"]),
                port=int(target["port"]),
                username=str(target["username"]),
                password=str(target["password"]),
                timeout=5,
                allow_agent=False,
                look_for_keys=False,
            )
            # Inline Python avoids depending on a specific tail/dd flag
            # variant; reads from the requested byte offset to EOF.
            remote_cmd = (
                "python3 -c "
                "'import sys; "
                f"f=open(\"{log_path}\",\"rb\"); "
                f"f.seek({offset}); "
                "data=f.read(); "
                "sys.stdout.buffer.write(data)'"
            )
            _stdin, stdout, _stderr = client.exec_command(remote_cmd, timeout=5)
            data = stdout.read()
            return data, offset + len(data)
        except Exception:
            # Best-effort: missing log file, transient SSH error, etc.
            # Don't crash the coordinator; just yield nothing this drain.
            return b"", offset
        finally:
            try:
                client.close()
            except Exception:
                pass

    return _tail
