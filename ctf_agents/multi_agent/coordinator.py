"""MultiAgentCoordinator — round-robin turn scheduler for N agents.

The coordinator owns the tick loop. Each tick, every agent in the
fixed roster order gets one call to ``step()``. Between ticks the
coordinator evaluates the scenario's win condition.

In Phase 1, agents act independently on a shared container — no
messaging. In Phase 2 the coordinator will drain each agent's
mailbox and inject it via ``perceive()`` before calling ``step()``;
the hook points for that already exist here as no-ops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from ctf_agents.core.primitives import EventBus, SharedState
from ctf_agents.multi_agent.mailbox import MailboxRegistry

if TYPE_CHECKING:
    from ctf_agents.core.agents import CTFAgent
    from ctf_agents.multi_agent.scenarios import Scenario
    from ctf_agents.multi_agent.transport import MessageTransport


@dataclass
class TickRecord:
    """Audit record for one agent's action in one tick."""

    tick: int
    agent_id: str
    iteration: int
    command: Optional[str]
    solved: bool
    error: Optional[str]


@dataclass
class RunSummary:
    """Terminal state of a multi-agent run."""

    ticks_elapsed: int
    stop_reason: str                       # "win_condition" | "max_ticks" | "all_stopped"
    win_condition_result: Optional[Dict[str, Any]]
    records: List[TickRecord] = field(default_factory=list)


class MultiAgentCoordinator:
    """Round-robin tick scheduler.

    Parameters:
        agents:       roster, already instantiated and pre-``start()``ed
                      (or to be ``start()``ed by the orchestrator before
                      the first tick)
        scenario:     Scenario spec providing max_ticks, win_condition,
                      messaging config, on_tick hook
        event_bus:    shared EventBus (optional; required for Phase 2 comms)
        shared_state: shared SharedState (optional; required for Phase 2)
    """

    def __init__(
        self,
        agents: List["CTFAgent"],
        scenario: "Scenario",
        event_bus: Optional[EventBus] = None,
        shared_state: Optional[SharedState] = None,
        transport: Optional["MessageTransport"] = None,
    ):
        if not agents:
            raise ValueError("MultiAgentCoordinator requires at least one agent")
        self.agents = agents
        self.scenario = scenario
        self.event_bus = event_bus or EventBus()
        self.shared_state = shared_state or SharedState()
        # ``self.mailbox`` is any MessageTransport. Default is the legacy
        # in-process MailboxRegistry so older callers (and single-host
        # tests) keep working without code changes; the orchestrator
        # injects a TcpTransport for scenarios with messaging.transport
        # == "tcp".
        self.mailbox = transport if transport is not None else MailboxRegistry(self.event_bus)
        self.current_tick = 0
        self.records: List[TickRecord] = []
        self._stopped_agents: set = set()
        self._messages_per_tick: List[int] = []
        # Per-agent totals for the summary. messages_sent_per_agent
        # is derived from the transport's wire-level counters in
        # metrics(), so we don't track it separately here.
        self._steps_per_agent: Dict[str, int] = {a.agent_id: 0 for a in agents}
        # Ordered log of every message observed by any agent's inbox. Each
        # entry: {tick, sender, recipients, channel, content, timestamp,
        # observed_by}. observed_by is the agent whose drain surfaced the
        # message — for broadcast/team channels the same message appears
        # once per receiver (with the same content but different observed_by).
        self._transcript: List[Dict[str, Any]] = []

        # Register agents with the mailbox and hand out mailbox refs + caps.
        cap = self.scenario.messaging.max_messages_per_tick
        max_len = self.scenario.messaging.max_message_length
        for agent in agents:
            self.mailbox.register_agent(agent.agent_id, agent.team_id)
            agent.event_bus = self.event_bus
            agent.shared_state = self.shared_state
            agent.mailbox = self.mailbox
            agent._msg_cap_per_tick = cap
            agent._msg_max_length = max_len

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> RunSummary:
        """Run until a win condition fires, max_ticks, or all agents stop."""
        stop_reason = "max_ticks"
        win_result: Optional[Dict[str, Any]] = None

        for tick in range(1, self.scenario.max_ticks + 1):
            self.current_tick = tick
            self._print_tick_header(tick)

            tick_msgs_before = self.mailbox.total_sent
            self.tick()
            self._messages_per_tick.append(self.mailbox.total_sent - tick_msgs_before)
            self.mailbox.reset_per_tick_counters()

            # Post-tick hooks
            if self.scenario.on_tick is not None:
                try:
                    self.scenario.on_tick(self, tick)
                except Exception as exc:
                    print(f"[coordinator] on_tick raised {exc!r}")

            win_result = self.scenario.win_condition(self)
            if win_result is not None:
                stop_reason = "win_condition"
                self._print_win(win_result)
                break

            if len(self._stopped_agents) == len(self.agents):
                stop_reason = "all_stopped"
                break

        # Capture any tail-end messages so the transcript + per-agent send
        # counts include traffic from the winning/final tick.
        self._final_drain()

        return RunSummary(
            ticks_elapsed=self.current_tick,
            stop_reason=stop_reason,
            win_condition_result=win_result,
            records=self.records,
        )

    def tick(self) -> None:
        """Execute one full tick: every agent steps once, in roster order."""
        for agent in self.agents:
            if agent.agent_id in self._stopped_agents:
                continue
            self._run_one_agent(agent)

    # ------------------------------------------------------------------
    # Per-agent step
    # ------------------------------------------------------------------

    def _run_one_agent(self, agent: "CTFAgent") -> None:
        # Phase 2 hook: drain mailbox + inject via perceive().
        # Left as a no-op for Phase 1; wired in Phase 2 once Mailbox exists.
        self._deliver_perceptions(agent)

        try:
            result = agent.step()
        except Exception as exc:
            print(f"[{agent.agent_id}] step raised {exc!r}")
            self._stopped_agents.add(agent.agent_id)
            return

        self._record(agent, result)
        self._print_agent_result(agent, result)

        # Honour the agent's own retry signal: hard error + no retry → stop this agent.
        if result["error"] is not None and not result["retry"]:
            self._stopped_agents.add(agent.agent_id)

    def _deliver_perceptions(self, agent: "CTFAgent") -> None:
        """Drain the agent's mailbox and feed perception to the agent.

        Runs before each ``step()``. The SharedState snapshot is passed
        through as-is — the agent decides how much of it to surface to
        its LLM in ``_format_perception``.
        """
        inbox = self.mailbox.drain(agent.agent_id)
        if inbox:
            self._record_transcript(agent.agent_id, inbox)
        snapshot = self.shared_state.snapshot()
        if inbox or snapshot:
            agent.perceive(inbox, snapshot)

    def _record_transcript(self, observed_by: str, messages: List) -> None:
        """Append drained messages to the run-wide transcript log."""
        for msg in messages:
            self._transcript.append({
                "tick": self.current_tick,
                "sender": msg.sender,
                "recipients": list(msg.recipients),
                "channel": msg.channel,
                "content": msg.content,
                "timestamp": msg.metadata.get("timestamp"),
                "observed_by": observed_by,
            })

    def _final_drain(self) -> None:
        """Drain every agent's inbox one more time after the main loop.

        Without this, messages sent in the tick that triggers the win
        condition (and addressed to an agent that had already taken its
        turn in that tick) would never be drained — so they'd be absent
        from the transcript and uncounted by transports that derive
        ``sent_per_agent`` from receiver-side drains (TCP mode).
        """
        for agent in self.agents:
            tail = self.mailbox.drain(agent.agent_id)
            if tail:
                self._record_transcript(agent.agent_id, tail)

    def _record(self, agent: "CTFAgent", result: Dict[str, Any]) -> None:
        record = TickRecord(
            tick=self.current_tick,
            agent_id=agent.agent_id,
            iteration=result["iteration"],
            command=result["command"],
            solved=result["solved"],
            error=result["error"],
        )
        self.records.append(record)
        self._steps_per_agent[agent.agent_id] = self._steps_per_agent.get(agent.agent_id, 0) + 1

    # ------------------------------------------------------------------
    # Metrics surface for the orchestrator
    # ------------------------------------------------------------------

    def metrics(self) -> Dict[str, Any]:
        """Substrate-only metrics for the final report.

        ``messages_sent_per_agent`` is taken from the transport's
        ``sent_per_agent`` map (wire truth) rather than the agent
        self-reported ``<MSG>``-tag count. The two disagree under TCP
        mode, where messaging goes through the in-container
        ``agent-send`` CLI — those don't pass through the agent's tag
        parser, so the self-reported count is always 0. The transport's
        map is observed from the audit log and is correct for both
        in-process and TCP transports.
        """
        mailbox_m = self.mailbox.metrics_snapshot()
        total_steps = sum(self._steps_per_agent.values())
        sent_per_agent = {
            a.agent_id: int(mailbox_m.get("sent_per_agent", {}).get(a.agent_id, 0))
            for a in self.agents
        }
        total_msgs = sum(sent_per_agent.values())
        return {
            "total_ticks": self.current_tick,
            "total_steps_across_agents": total_steps,
            "messages_sent_total": mailbox_m["total_sent"],
            "messages_delivered_total": mailbox_m["total_delivered"],
            "messages_dropped_over_cap": mailbox_m["total_dropped_over_cap"],
            "messages_per_tick": list(self._messages_per_tick),
            "messages_sent_per_agent": sent_per_agent,
            "steps_per_agent": dict(self._steps_per_agent),
            "message_to_action_ratio": (
                total_msgs / total_steps if total_steps else 0.0
            ),
            "transcript": list(self._transcript),
        }

    # ------------------------------------------------------------------
    # Printing helpers
    # ------------------------------------------------------------------

    def _print_tick_header(self, tick: int) -> None:
        print(f"\n{'=' * 70}")
        print(f"  TICK {tick}/{self.scenario.max_ticks}  ({len(self.agents) - len(self._stopped_agents)} active agent(s))")
        print("=" * 70)

    def _print_agent_result(self, agent: "CTFAgent", result: Dict[str, Any]) -> None:
        prefix = f"[{agent.agent_id}]"
        if result["error"] is not None:
            print(f"{prefix} ERROR (iter {result['iteration']}): {result['error']}")
            return
        cmd = (result["command"] or "").strip().replace("\n", " ⏎ ")
        if len(cmd) > 120:
            cmd = cmd[:117] + "..."
        solved_tag = "  <-- SOLVED" if result["solved"] else ""
        print(f"{prefix} iter {result['iteration']}: {cmd}{solved_tag}")

    def _print_win(self, result: Dict[str, Any]) -> None:
        print(f"\n{'=' * 70}")
        print(f"  WIN CONDITION: {result.get('reason', 'unknown')}")
        if "winner_ids" in result:
            print(f"  Winners: {', '.join(result['winner_ids'])}")
        print("=" * 70)
