"""Unit tests for the coordinator's transcript log, final-drain, and
``messages_sent_per_agent`` source.

These tests bypass the LLM and Docker entirely. They construct a
minimal ``MultiAgentCoordinator`` with a fake transport and fake
``CTFAgent``-shaped objects, drive a few ticks, and assert:

* every drained message lands in the transcript with the expected
  fields (tick / sender / recipients / content / observed_by);
* messages that arrive in the audit log *after* the main loop exits
  (e.g. sent in the winning tick by an agent whose peer had already
  stepped) are picked up by the post-loop ``_final_drain``;
* ``messages_sent_per_agent`` in ``coordinator.metrics()`` is sourced
  from the transport's ``sent_per_agent`` (wire truth), not from the
  agent's ``<MSG>``-tag self-report — which is what was undercounting
  TCP-mode runs.

Run with::

    python tests/test_coordinator_transcript.py
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ctf_agents.multi_agent.coordinator import MultiAgentCoordinator  # noqa: E402
from ctf_agents.multi_agent.scenarios import (  # noqa: E402
    MessagingConfig,
    Scenario,
    all_agents_solved,
)
from ctf_agents.multi_agent.tcp_transport import TcpTransport  # noqa: E402


# ----------------------------------------------------------------------
# Fakes
# ----------------------------------------------------------------------


class FakeAuditLogs:
    """In-memory stand-in for per-container /var/log/agent-tcp.log."""

    def __init__(self) -> None:
        self._content: Dict[str, bytes] = {}

    def append(self, agent_id: str, envelope: Dict[str, object]) -> None:
        line = (json.dumps(envelope, ensure_ascii=False) + "\n").encode("utf-8")
        self._content[agent_id] = self._content.get(agent_id, b"") + line

    def tail(self, agent_id: str, offset: int, _path: str) -> Tuple[bytes, int]:
        buf = self._content.get(agent_id, b"")
        return buf[offset:], len(buf)


class FakeAgent:
    """Minimal CTF-agent shape the coordinator interacts with.

    ``script`` is a list of (action, payload) tuples evaluated in order
    on each ``step()``:
        ("send",  body)  → writes an envelope to the peer's audit log
        ("solve", _)     → mark this agent as solved
        ("idle",  _)     → no-op
    """

    def __init__(self, agent_id: str, peer_id: str, team_id: Optional[str],
                 logs: FakeAuditLogs, script: List[Tuple[str, Any]]):
        self.agent_id = agent_id
        self.peer_id = peer_id
        self.team_id = team_id
        self.solved = False
        self.discovered_flags: List[str] = []
        self.event_bus = None
        self.shared_state = None
        self.mailbox = None
        self._msg_cap_per_tick = None
        self._msg_max_length = None
        self._logs = logs
        self._script = list(script)
        self._iter = 0
        self.perceived: List[List[Any]] = []  # for assertions

    # Coordinator interface ------------------------------------------------

    def perceive(self, inbox, snapshot) -> None:  # noqa: D401
        self.perceived.append(list(inbox))

    def step(self) -> Dict[str, Any]:
        self._iter += 1
        action, payload = ("idle", None)
        if self._script:
            action, payload = self._script.pop(0)

        command = f"# {action}"
        if action == "send":
            envelope = {
                "from": self.agent_id,
                "to": self.peer_id,
                "ts": f"t{self._iter}",
                "body": payload,
            }
            self._logs.append(self.peer_id, envelope)
            command = f"agent-send {self.peer_id} {payload!r}"
        elif action == "solve":
            self.solved = True
            self.discovered_flags.append("flag{test}")
            command = "sudo /usr/local/bin/vault peer-key"

        return {
            "command": command,
            "raw_output": command,
            "output": "",
            "solved": self.solved,
            "iteration": self._iter,
            "error": None,
            "retry": False,
            "messages_sent": 0,
            "findings": [],
        }


def _make_scenario(max_ticks: int = 5) -> Scenario:
    return Scenario(
        name="transcript-test",
        roster=[],  # the coordinator never re-derives agents from roster
        max_ticks=max_ticks,
        win_condition=all_agents_solved,
        messaging=MessagingConfig(transport="tcp"),
        metadata={},
    )


# ----------------------------------------------------------------------
# Assertion helper
# ----------------------------------------------------------------------


def _assert(cond: bool, msg: str, errors: List[str]) -> None:
    if not cond:
        errors.append(msg)


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


def test_transcript_captures_drained_messages(errors: List[str]) -> None:
    """Each drained Message lands in the transcript with the right fields."""
    logs = FakeAuditLogs()
    transport = TcpTransport(tail_fn=logs.tail)

    agent_a = FakeAgent("agent-a", "agent-b", "coop", logs, script=[
        ("send", "alpha-key"),  # tick 1: a sends to b
        ("solve", None),        # tick 2: a solves (after receiving b's key)
    ])
    agent_b = FakeAgent("agent-b", "agent-a", "coop", logs, script=[
        ("send", "beta-key"),   # tick 1: b sends to a (after b drained a's msg)
        ("solve", None),        # tick 2: b solves
    ])

    coord = MultiAgentCoordinator(
        agents=[agent_a, agent_b],
        scenario=_make_scenario(),
        transport=transport,
    )
    summary = coord.run()

    transcript = coord.metrics()["transcript"]
    _assert(summary.stop_reason == "win_condition",
            f"stop_reason={summary.stop_reason!r}", errors)
    # Two cross-deliveries (a→b, b→a). Each is observed exactly once
    # because direct-channel messages go to a single inbox.
    _assert(len(transcript) == 2,
            f"expected 2 transcript entries, got {len(transcript)}: {transcript}",
            errors)

    a_to_b = [e for e in transcript if e["sender"] == "agent-a"]
    b_to_a = [e for e in transcript if e["sender"] == "agent-b"]
    _assert(len(a_to_b) == 1 and a_to_b[0]["content"] == "alpha-key"
            and a_to_b[0]["observed_by"] == "agent-b"
            and a_to_b[0]["recipients"] == ["agent-b"]
            and a_to_b[0]["channel"] == "direct",
            f"a→b entry malformed: {a_to_b}", errors)
    _assert(len(b_to_a) == 1 and b_to_a[0]["content"] == "beta-key"
            and b_to_a[0]["observed_by"] == "agent-a",
            f"b→a entry malformed: {b_to_a}", errors)


def test_final_drain_catches_tail_messages(errors: List[str]) -> None:
    """A message sent in the winning tick — by the second-acting agent to
    the first-acting agent — is recorded by the post-loop final drain.

    Sequence (in one tick):
        drain agent-a → empty
        agent-a sends to agent-b
        drain agent-b → sees agent-a's message
        agent-b sends to agent-a, then solves
        drain agent-a happens here (tick over) — without final-drain
        this would be missed entirely.
    Both solve → win condition fires after tick 1.
    """
    logs = FakeAuditLogs()
    transport = TcpTransport(tail_fn=logs.tail)

    agent_a = FakeAgent("agent-a", "agent-b", "coop", logs, script=[
        ("send", "alpha-key"),  # then a's only turn ends — a will solve in tick 2 if there was one
        ("solve", None),
    ])
    # agent-b sends + solves in the SAME tick (after a). So a never gets
    # another step to drain b's message inside the main loop.
    agent_b = FakeAgent("agent-b", "agent-a", "coop", logs, script=[
        ("send", "beta-key"),
        ("solve", None),
    ])

    # Use max_ticks=2 to let both agents solve, but b's solve happens in
    # tick 2's b-step (after sending) so a's tick-2 drain catches it.
    # To actually exercise final-drain, we force the win after agent-b's
    # send by giving agent-a a "solve" first-thing in tick 2 and agent-b
    # a "send,solve" in the same tick.
    # Build a slightly different script: tick 1 each just sends; tick 2
    # each solves, but agent-b also sends a *second* message right before
    # solving — that second message is the tail.
    agent_a._script = [("send", "alpha-key"), ("solve", None)]
    agent_b._script = [("send", "beta-1"), ("send-and-solve", "beta-2")]

    # Extend FakeAgent on the fly to support the combo action used only
    # here — keeps the standard tests above simple.
    original_step = agent_b.step

    def step_with_combo() -> Dict[str, Any]:
        if agent_b._script and agent_b._script[0][0] == "send-and-solve":
            _action, payload = agent_b._script.pop(0)
            agent_b._iter += 1
            envelope = {"from": "agent-b", "to": "agent-a",
                        "ts": f"t{agent_b._iter}", "body": payload}
            logs.append("agent-a", envelope)
            agent_b.solved = True
            return {
                "command": f"agent-send agent-a {payload!r}; sudo vault ...",
                "raw_output": "", "output": "", "solved": True,
                "iteration": agent_b._iter, "error": None, "retry": False,
                "messages_sent": 0, "findings": [],
            }
        return original_step()

    agent_b.step = step_with_combo  # type: ignore[assignment]

    coord = MultiAgentCoordinator(
        agents=[agent_a, agent_b],
        scenario=_make_scenario(max_ticks=3),
        transport=transport,
    )
    summary = coord.run()

    transcript = coord.metrics()["transcript"]
    sent = coord.metrics()["messages_sent_per_agent"]

    _assert(summary.stop_reason == "win_condition",
            f"stop_reason={summary.stop_reason!r}", errors)

    # Two messages from b → a: "beta-1" (tick-2 mid-loop, drained by a's
    # tick-3 step never happened because win fired) and "beta-2" (sent in
    # the same step as solve). Both must appear via final_drain.
    contents = sorted(e["content"] for e in transcript
                      if e["sender"] == "agent-b")
    _assert(contents == ["beta-1", "beta-2"],
            f"agent-b transcript contents={contents}, expected ['beta-1','beta-2']",
            errors)

    # Wire-truth: agent-b sent 2 messages, agent-a sent 1.
    _assert(sent.get("agent-b") == 2 and sent.get("agent-a") == 1,
            f"messages_sent_per_agent={sent} expected a:1 b:2", errors)


def test_messages_sent_per_agent_uses_transport_not_tag_count(errors: List[str]) -> None:
    """Even if the agent self-reports zero <MSG> tags (the TCP-mode case),
    the per-agent count must reflect what the transport observed."""
    logs = FakeAuditLogs()
    transport = TcpTransport(tail_fn=logs.tail)

    agent_a = FakeAgent("agent-a", "agent-b", "coop", logs, script=[
        ("send", "k1"), ("send", "k2"), ("solve", None),
    ])
    agent_b = FakeAgent("agent-b", "agent-a", "coop", logs, script=[
        ("idle", None), ("idle", None), ("solve", None),
    ])

    coord = MultiAgentCoordinator(
        agents=[agent_a, agent_b],
        scenario=_make_scenario(max_ticks=5),
        transport=transport,
    )
    coord.run()

    metrics = coord.metrics()
    # FakeAgent always reports messages_sent=0, so the old
    # implementation would put 0 here. The new code reads from
    # transport.sent_per_agent.
    _assert(metrics["messages_sent_per_agent"]["agent-a"] == 2,
            f"agent-a count={metrics['messages_sent_per_agent']}", errors)
    _assert(metrics["messages_sent_per_agent"]["agent-b"] == 0,
            f"agent-b count={metrics['messages_sent_per_agent']}", errors)
    # message_to_action_ratio is derived from the transport count too.
    # 2 messages over 6 steps (3 per agent × 2 agents) = 0.333...
    _assert(abs(metrics["message_to_action_ratio"] - 2 / 6) < 1e-6,
            f"ratio={metrics['message_to_action_ratio']}", errors)


def test_transcript_present_for_inproc_mailbox(errors: List[str]) -> None:
    """The transcript is transport-agnostic — the in-process mailbox
    path also fills it, so legacy single-host scenarios get the same
    visibility."""
    from ctf_agents.multi_agent.mailbox import MailboxRegistry
    from ctf_agents.multi_agent.protocol import Message
    from ctf_agents.core.primitives import EventBus

    bus = EventBus()
    mailbox = MailboxRegistry(bus)

    class InprocAgent(FakeAgent):
        def step(self) -> Dict[str, Any]:
            self._iter += 1
            if self._script:
                action, payload = self._script.pop(0)
                if action == "send":
                    self.mailbox.send(Message.make(
                        sender=self.agent_id,
                        recipients=[self.peer_id],
                        content=payload,
                    ))
                elif action == "solve":
                    self.solved = True
            return {
                "command": "", "raw_output": "", "output": "",
                "solved": self.solved, "iteration": self._iter,
                "error": None, "retry": False,
                "messages_sent": 0, "findings": [],
            }

    a = InprocAgent("a", "b", None, FakeAuditLogs(),
                    script=[("send", "hi"), ("solve", None)])
    b = InprocAgent("b", "a", None, FakeAuditLogs(),
                    script=[("send", "yo"), ("solve", None)])

    coord = MultiAgentCoordinator(
        agents=[a, b],
        scenario=_make_scenario(),
        transport=mailbox,
    )
    coord.run()

    transcript = coord.metrics()["transcript"]
    senders = sorted(e["sender"] for e in transcript)
    _assert(senders == ["a", "b"],
            f"inproc transcript senders={senders}, expected ['a','b']", errors)


def main() -> int:
    errors: List[str] = []
    tests = [
        test_transcript_captures_drained_messages,
        test_final_drain_catches_tail_messages,
        test_messages_sent_per_agent_uses_transport_not_tag_count,
        test_transcript_present_for_inproc_mailbox,
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
