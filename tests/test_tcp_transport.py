"""Unit tests for ``ctf_agents.multi_agent.tcp_transport.TcpTransport``.

These tests don't touch SSH, sockets, or Docker — they inject a fake
``tail_fn`` and assert the transport correctly:

* turns audit-log envelopes into ``Message`` objects,
* advances the per-agent byte offset between drains,
* reports metrics in the same shape as the in-process
  ``MailboxRegistry``,
* tolerates malformed envelopes without crashing the coordinator.

Run with::

    python tests/test_tcp_transport.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ctf_agents.multi_agent.protocol import Message  # noqa: E402
from ctf_agents.multi_agent.tcp_transport import TcpTransport  # noqa: E402


# ----------------------------------------------------------------------
# Fake tail backend
# ----------------------------------------------------------------------


class FakeAuditLogs:
    """In-memory stand-in for per-container /var/log/agent-tcp.log."""

    def __init__(self) -> None:
        self._content: Dict[str, bytes] = {}

    def append(self, agent_id: str, envelope: Dict[str, object]) -> None:
        line = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
        self._content[agent_id] = self._content.get(agent_id, b"") + line

    def append_raw(self, agent_id: str, raw: bytes) -> None:
        self._content[agent_id] = self._content.get(agent_id, b"") + raw

    def tail(self, agent_id: str, offset: int, _path: str) -> Tuple[bytes, int]:
        buf = self._content.get(agent_id, b"")
        new = buf[offset:]
        return new, len(buf)


# ----------------------------------------------------------------------
# Assertions helpers
# ----------------------------------------------------------------------


def _assert(cond: bool, msg: str, errors: List[str]) -> None:
    if not cond:
        errors.append(msg)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_drain_returns_envelopes_in_order(errors: List[str]) -> None:
    """Two envelopes arrive at agent-b; one drain returns both in order."""
    logs = FakeAuditLogs()
    t = TcpTransport(tail_fn=logs.tail)
    t.register_agent("agent-a", team_id="coop")
    t.register_agent("agent-b", team_id="coop")

    logs.append("agent-b", {"from": "agent-a", "to": "agent-b",
                            "ts": "2026-05-23T00:00:00Z", "body": "first"})
    logs.append("agent-b", {"from": "agent-a", "to": "agent-b",
                            "ts": "2026-05-23T00:00:01Z", "body": "second"})

    msgs = t.drain("agent-b")
    _assert(len(msgs) == 2, f"expected 2 messages, got {len(msgs)}", errors)
    _assert(msgs[0].sender == "agent-a", f"msg0 sender={msgs[0].sender!r}", errors)
    _assert(msgs[0].content == "first", f"msg0 body={msgs[0].content!r}", errors)
    _assert(msgs[1].content == "second", f"msg1 body={msgs[1].content!r}", errors)
    _assert(msgs[0].recipients == ["agent-b"],
            f"msg0 recipients={msgs[0].recipients!r}", errors)


def test_offset_advances_between_drains(errors: List[str]) -> None:
    """A second drain returns only what arrived after the first."""
    logs = FakeAuditLogs()
    t = TcpTransport(tail_fn=logs.tail)
    t.register_agent("agent-a")

    logs.append("agent-a", {"from": "agent-b", "to": "agent-a",
                            "ts": "t0", "body": "old"})
    first = t.drain("agent-a")
    _assert(len(first) == 1, f"first drain: expected 1, got {len(first)}", errors)

    logs.append("agent-a", {"from": "agent-b", "to": "agent-a",
                            "ts": "t1", "body": "new"})
    second = t.drain("agent-a")
    _assert(len(second) == 1, f"second drain: expected 1 (only new), got {len(second)}", errors)
    _assert(second[0].content == "new", f"second drain body={second[0].content!r}", errors)

    third = t.drain("agent-a")
    _assert(third == [], f"third drain should be empty, got {third}", errors)


def test_metrics_shape_matches_mailbox(errors: List[str]) -> None:
    """metrics_snapshot returns the same keys MailboxRegistry does."""
    logs = FakeAuditLogs()
    t = TcpTransport(tail_fn=logs.tail)
    t.register_agent("agent-a")
    t.register_agent("agent-b")

    logs.append("agent-b", {"from": "agent-a", "to": "agent-b", "body": "x"})
    logs.append("agent-b", {"from": "agent-a", "to": "agent-b", "body": "y"})
    logs.append("agent-a", {"from": "agent-b", "to": "agent-a", "body": "z"})
    t.drain("agent-a")
    t.drain("agent-b")

    m = t.metrics_snapshot()
    for key in ("total_sent", "total_delivered", "total_dropped_over_cap",
                "sent_per_agent"):
        _assert(key in m, f"metrics missing key {key!r}", errors)
    _assert(m["total_sent"] == 3, f"total_sent={m['total_sent']}", errors)
    _assert(m["total_delivered"] == 3, f"total_delivered={m['total_delivered']}", errors)
    _assert(m["sent_per_agent"].get("agent-a") == 2,
            f"sent_per_agent[agent-a]={m['sent_per_agent'].get('agent-a')}", errors)
    _assert(m["sent_per_agent"].get("agent-b") == 1,
            f"sent_per_agent[agent-b]={m['sent_per_agent'].get('agent-b')}", errors)


def test_send_is_noop(errors: List[str]) -> None:
    """send() returns 0 and doesn't touch the fake log."""
    logs = FakeAuditLogs()
    t = TcpTransport(tail_fn=logs.tail)
    t.register_agent("agent-a")
    t.register_agent("agent-b")

    ret = t.send(Message(sender="agent-a", recipients=["agent-b"],
                         channel="direct", content="hi"))
    _assert(ret == 0, f"send() expected 0, got {ret}", errors)
    _assert(logs.tail("agent-b", 0, "/var/log/agent-tcp.log")[0] == b"",
            "send() must not write to the audit log", errors)


def test_malformed_lines_are_skipped(errors: List[str]) -> None:
    """Garbled or partial JSON in the log doesn't crash drain."""
    logs = FakeAuditLogs()
    t = TcpTransport(tail_fn=logs.tail)
    t.register_agent("agent-b")

    logs.append_raw("agent-b", b'{"this is not": "json"\n')  # missing brace
    logs.append("agent-b", {"from": "agent-a", "to": "agent-b", "body": "ok"})
    logs.append_raw("agent-b", b'{"missing": "fields"}\n')  # no from/to/body

    msgs = t.drain("agent-b")
    _assert(len(msgs) == 1,
            f"expected 1 valid message, got {len(msgs)} (msgs={msgs})", errors)
    _assert(msgs[0].content == "ok", f"surviving msg body={msgs[0].content!r}", errors)


def test_unknown_agent_drain_is_empty(errors: List[str]) -> None:
    """drain() on an unregistered agent returns [] (not a crash)."""
    t = TcpTransport(tail_fn=FakeAuditLogs().tail)
    _assert(t.drain("nobody") == [], "unknown agent drain should be []", errors)
    _assert(t.peek("nobody") == [], "unknown agent peek should be []", errors)


def main() -> int:
    errors: List[str] = []
    tests = [
        test_drain_returns_envelopes_in_order,
        test_offset_advances_between_drains,
        test_metrics_shape_matches_mailbox,
        test_send_is_noop,
        test_malformed_lines_are_skipped,
        test_unknown_agent_drain_is_empty,
    ]
    for tfn in tests:
        before = len(errors)
        try:
            tfn(errors)
        except Exception as exc:
            errors.append(f"{tfn.__name__} raised {exc!r}")
        status = "OK" if len(errors) == before else "FAIL"
        print(f"  [{status}] {tfn.__name__}")

    if errors:
        print("\n=== FAIL ===")
        for e in errors:
            print(f"  - {e}")
        return 1
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
