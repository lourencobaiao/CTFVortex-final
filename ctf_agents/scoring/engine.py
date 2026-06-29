"""Scoring engine for CTF challenge results."""

from dataclasses import dataclass, field
from statistics import mean
from typing import Any, Dict, List, Optional


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of a challenge score."""

    base_points: int = 0
    time_bonus: float = 0.0
    efficiency_score: float = 0.0
    partial_credit: float = 0.0
    difficulty_multiplier: float = 1.0
    total_score: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        lines = [
            f"Base points:           {self.base_points}",
            f"Time bonus:            {self.time_bonus:+.1f}",
            f"Efficiency score:      {self.efficiency_score:+.1f}",
            f"Partial credit:        {self.partial_credit:+.1f}",
            f"Difficulty multiplier: x{self.difficulty_multiplier:.1f}",
            f"{'─' * 35}",
            f"Total score:           {self.total_score:.1f}",
        ]
        return "\n".join(lines)


# Default configuration constants
DEFAULT_BASE_POINTS = {"easy": 100, "medium": 200, "hard": 300}
DEFAULT_DIFFICULTY_MULTIPLIERS = {"easy": 1.0, "medium": 1.5, "hard": 2.0}
DEFAULT_TIME_THRESHOLDS = {
    "easy": (60, 180, 300),      # full / half / zero bonus (seconds)
    "medium": (120, 300, 600),
    "hard": (180, 480, 900),
}
DEFAULT_TIME_BONUS_PERCENT = 0.25   # max 25% of base
DEFAULT_EFFICIENCY_PERCENT = 0.15   # max 15% of base
DEFAULT_PARTIAL_CREDIT_CAP = 0.50   # max 50% of base


class ScoringEngine:
    """Computes challenge scores from result dicts and challenge metadata.

    All configuration is injected via constructor so the engine is
    easy to tune without code changes.
    """

    def __init__(
        self,
        base_points: Optional[Dict[str, int]] = None,
        difficulty_multipliers: Optional[Dict[str, float]] = None,
        time_thresholds: Optional[Dict[str, tuple]] = None,
        time_bonus_percent: float = DEFAULT_TIME_BONUS_PERCENT,
        efficiency_percent: float = DEFAULT_EFFICIENCY_PERCENT,
        partial_credit_cap: float = DEFAULT_PARTIAL_CREDIT_CAP,
    ):
        self.base_points = base_points or DEFAULT_BASE_POINTS
        self.difficulty_multipliers = difficulty_multipliers or DEFAULT_DIFFICULTY_MULTIPLIERS
        self.time_thresholds = time_thresholds or DEFAULT_TIME_THRESHOLDS
        self.time_bonus_percent = time_bonus_percent
        self.efficiency_percent = efficiency_percent
        self.partial_credit_cap = partial_credit_cap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score(self, result: Dict[str, Any], challenge_metadata: Dict[str, Any]) -> ScoreBreakdown:
        """Compute the full score breakdown for a challenge result.

        Args:
            result: The dict returned by ``CTFAgent.run()`` (enriched by
                the orchestrator with ``expected_flag``, ``challenge_type``,
                ``difficulty``).
            challenge_metadata: The metadata dict from
                ``CTFWebsiteGenerator.generate_website()`` (must contain
                ``flag``, ``difficulty``, and optionally
                ``partial_credit_markers``).
        """
        difficulty = challenge_metadata.get("difficulty", "easy")
        expected_flag = challenge_metadata.get("flag", "")
        flags_found: List[str] = result.get("flags_found", [])
        flag_correct = expected_flag in flags_found

        # 1. Base points
        base = self.base_points.get(difficulty, 100) if flag_correct else 0

        # 2. Time bonus
        time_elapsed = result.get("time_elapsed", 0)
        time_bonus = self._compute_time_bonus(base, time_elapsed, difficulty)

        # 3. Efficiency score
        total_attempts = result.get("total_attempts", 0)
        successful = result.get("successful_commands", 0)
        efficiency = self._compute_efficiency(base, successful, total_attempts)

        # 4. Partial credit
        markers = challenge_metadata.get("partial_credit_markers", {})
        command_history = result.get("command_history", [])
        discovered_endpoints = result.get("discovered_endpoints", [])
        partial, partial_details = self._compute_partial_credit(
            base_ref=self.base_points.get(difficulty, 100),
            markers=markers,
            command_history=command_history,
            discovered_endpoints=discovered_endpoints,
            flag_correct=flag_correct,
        )

        # 5. Difficulty multiplier
        multiplier = self.difficulty_multipliers.get(difficulty, 1.0)

        # Total (floor at 0)
        raw_total = base + time_bonus + efficiency + partial
        total = max(0.0, raw_total * multiplier)

        return ScoreBreakdown(
            base_points=base,
            time_bonus=round(time_bonus, 2),
            efficiency_score=round(efficiency, 2),
            partial_credit=round(partial, 2),
            difficulty_multiplier=multiplier,
            total_score=round(total, 2),
            details={
                "flag_correct": flag_correct,
                "time_elapsed": time_elapsed,
                "time_decay_factor": self._time_decay(time_elapsed, difficulty),
                "efficiency_ratio": successful / total_attempts if total_attempts else 0,
                "partial_credit_hits": partial_details,
                "difficulty": difficulty,
            },
        )

    # ------------------------------------------------------------------
    # Component calculators
    # ------------------------------------------------------------------

    def _compute_time_bonus(self, base: int, elapsed: float, difficulty: str) -> float:
        """Linear-decay time bonus: full → half → zero."""
        if base == 0:
            return 0.0
        decay = self._time_decay(elapsed, difficulty)
        return base * self.time_bonus_percent * decay

    def _time_decay(self, elapsed: float, difficulty: str) -> float:
        """Return a factor in [0.0, 1.0] based on elapsed time."""
        full, half, zero = self.time_thresholds.get(difficulty, (60, 180, 300))
        if elapsed <= full:
            return 1.0
        if elapsed >= zero:
            return 0.0
        if elapsed <= half:
            # Linear decay from 1.0 → 0.5 between full and half
            return 1.0 - 0.5 * ((elapsed - full) / (half - full))
        # Linear decay from 0.5 → 0.0 between half and zero
        return 0.5 * (1.0 - (elapsed - half) / (zero - half))

    def _compute_efficiency(self, base: int, successful: int, total: int) -> float:
        """Efficiency bonus based on successful/total command ratio."""
        if base == 0 or total == 0:
            return 0.0
        ratio = successful / total
        return base * self.efficiency_percent * ratio

    def _compute_partial_credit(
        self,
        base_ref: int,
        markers: Dict[str, str],
        command_history: List[str],
        discovered_endpoints: List[str],
        flag_correct: bool,
    ) -> tuple:
        """Check command history for partial-credit markers.

        Returns (points, hit_details) where hit_details maps marker
        name → bool.  Partial credit is only meaningful when the flag
        was NOT found (it rewards progress short of a full solve).
        When the flag IS found, partial credit is 0 — the base points
        already cover the full solve.
        """
        if not markers or flag_correct:
            return 0.0, {}

        # Build a single searchable text blob from all commands and endpoints
        history_text = "\n".join(command_history).lower()
        endpoints_text = "\n".join(discovered_endpoints).lower()
        search_text = history_text + "\n" + endpoints_text

        per_marker = (base_ref * self.partial_credit_cap) / len(markers) if markers else 0
        total = 0.0
        hits: Dict[str, bool] = {}

        for name, needle in markers.items():
            found = needle.lower() in search_text
            hits[name] = found
            if found:
                total += per_marker

        # Cap at partial_credit_cap * base_ref
        total = min(total, base_ref * self.partial_credit_cap)
        return round(total, 2), hits


class MultiAgentScoringEngine:
    """Aggregate substrate-only metrics for a multi-agent run.

    The scoring engine stays the same for each per-agent report; this
    engine adds a ``multi_agent_summary`` block with communication and
    coordination metrics that only make sense at the run level. Metrics
    that require scenario content (time-to-detect, team success %, etc.)
    are intentionally deferred to Phase 3.
    """

    def summarize(
        self,
        scenario_name: str,
        per_agent_reports: List[Dict[str, Any]],
        coordinator_metrics: Dict[str, Any],
        stop_reason: str,
        win_condition_result: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        total_steps = coordinator_metrics.get("total_steps_across_agents", 0)
        msgs_sent = coordinator_metrics.get("messages_sent_total", 0)
        msgs_delivered = coordinator_metrics.get("messages_delivered_total", 0)
        ticks = coordinator_metrics.get("total_ticks", 0) or 1
        msgs_per_tick = coordinator_metrics.get("messages_per_tick", [])

        any_solved = any(r.get("solved") for r in per_agent_reports)
        flags: List[str] = []
        for r in per_agent_reports:
            for f in r.get("flags_found", []):
                if f not in flags:
                    flags.append(f)

        return {
            "scenario_name": scenario_name,
            "roster_size": len(per_agent_reports),
            "stop_reason": stop_reason,
            "win_condition_result": win_condition_result,
            "ticks_elapsed": coordinator_metrics.get("total_ticks", 0),
            "total_steps_across_agents": total_steps,
            "any_agent_solved": any_solved,
            "all_flags_discovered": flags,
            # Substrate / communication metrics
            "messages_sent_total": msgs_sent,
            "messages_delivered_total": msgs_delivered,
            "messages_dropped_over_cap": coordinator_metrics.get("messages_dropped_over_cap", 0),
            "messages_per_tick_avg": round(mean(msgs_per_tick), 3) if msgs_per_tick else 0.0,
            "messages_per_tick": msgs_per_tick,
            "messages_sent_per_agent": coordinator_metrics.get("messages_sent_per_agent", {}),
            "steps_per_agent": coordinator_metrics.get("steps_per_agent", {}),
            "message_to_action_ratio": round(
                coordinator_metrics.get("message_to_action_ratio", 0.0), 3
            ),
            "inbox_fanout_ratio": round(
                msgs_delivered / msgs_sent if msgs_sent else 0.0, 3
            ),
            "messages_transcript": coordinator_metrics.get("transcript", []),
        }
