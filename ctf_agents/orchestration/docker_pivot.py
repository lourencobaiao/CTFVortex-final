"""DockerPivotOrchestrator — single-agent lateral-movement run.

Sibling to :class:`~ctf_agents.orchestration.single_agent.SingleAgentOrchestrator`.
Kept as its own class rather than a third mode on ``SingleAgentOrchestrator``
because the latter is tightly bound to ``DockerEnvironmentManager`` (one
container, one network); adding a third branch would force every
existing call site to thread pivot-specific config.

The orchestrator only knows about the :class:`InfrastructureProvider`
contract — switching to a future GNS3 backend later is one constructor
argument away.
"""

from __future__ import annotations

import types
from typing import Any, Dict, Optional

from ctf_agents.config import DEFAULT_MODEL, FLAG_FORMAT
from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
from ctf_agents.core.providers import make_default_provider
from ctf_agents.infra.base import Environment, InfrastructureProvider
from ctf_agents.infra.docker_pivot_provider import DockerPivotInfraProvider
from ctf_agents.orchestration.base import Orchestrator
from ctf_agents.scenarios.docker_pivot_planter import plant_pivot_artifacts


# Per-tick context cap. Free-tier LLMs (Llama 3.1 8B) have ~8k-token
# windows; a single `ls -la /etc` blows past that. Capping each command
# output at this many characters keeps the conversation tractable even
# across 20+ iterations of recon. Picked empirically: large enough to
# fit a 50-line directory listing or a /etc/passwd, small enough that
# 10 such turns fit comfortably in 8k tokens.
_MAX_OUTPUT_CHARS = 1500


def _truncating_output_context(self, output: str, iteration: int) -> str:
    """Drop-in replacement for CTFAgent._build_output_context.

    Bound onto the agent instance by the orchestrator so this behavior
    is scoped to the pivot scenario only — Diamond Vault and other
    JeopardyCTFAgent users keep the unbounded default.
    """
    if len(output) > _MAX_OUTPUT_CHARS:
        head = output[: _MAX_OUTPUT_CHARS - 200]
        tail = output[-150:]
        return (
            "Command output (truncated to fit context — "
            f"{len(output)} chars total, showing head + tail):\n"
            f"{head}\n"
            f"... [{len(output) - _MAX_OUTPUT_CHARS} chars elided] ...\n"
            f"{tail}"
        )
    return f"Command output:\n{output}"


class DockerPivotOrchestrator(Orchestrator):
    """Run one attacker agent against a 2-node Docker pivot topology."""

    def __init__(
        self,
        api_key: str,
        scenario,
        *,
        model: Optional[str] = None,
        provider: Optional[InfrastructureProvider] = None,
    ):
        self.api_key = api_key
        self.scenario = scenario
        self.model = model or DEFAULT_MODEL
        # Default infrastructure backend is Docker; tests / future GNS3
        # plug in via this parameter.
        self.provider: InfrastructureProvider = provider or DockerPivotInfraProvider()
        self._last_env: Optional[Environment] = None

    # ── Orchestrator interface ───────────────────────────────────────

    def run(self, **kwargs) -> Dict[str, Any]:
        keep_on_failure = bool(kwargs.get("keep_on_failure", False))
        max_iterations = int(
            kwargs.get(
                "max_iterations",
                self.scenario.metadata.get("max_iterations", 30),
            )
        )

        env = self.provider.deploy(self.scenario)
        self._last_env = env
        agent_failed = False
        try:
            print("\n Step 7: Planting artifacts (flag, creds, hosts)...")
            plant_pivot_artifacts(env, self.scenario)

            entry_node = self.scenario.metadata.get("entry_node", "dmz")
            terminal = self.provider.connection_for(env, entry_node)

            ai_provider = make_default_provider(api_key=self.api_key, model=self.model)
            agent = JeopardyCTFAgent(
                ai_provider=ai_provider,
                terminal=terminal,
                challenge_name=self.scenario.metadata.get(
                    "challenge_name", "Docker Pivot"
                ),
                challenge_description=self.scenario.metadata.get("agent_brief", ""),
                flag_format=FLAG_FORMAT,
            )
            # Apply the scenario brief so the LLM gets the topology
            # context appended to its base prompt (see CTFAgent.multi_agent_prompt_suffix).
            agent.scenario_brief = self.scenario.metadata.get("agent_brief")
            # Bind output truncation onto THIS agent only — keeps free-tier
            # LLMs from blowing past 8k-token context windows.
            agent._build_output_context = types.MethodType(
                _truncating_output_context, agent
            )

            print("\n Step 8: Running attacker agent...")
            result = agent.run(max_iterations=max_iterations) or {}

            # Annotate the report with scenario-level expectations the
            # report viewer / scoring engine may want.
            result.setdefault("expected_flag", self.scenario.metadata.get("expected_flag"))
            result.setdefault("scenario_name", self.scenario.name)
            return result

        except Exception:
            agent_failed = True
            raise
        finally:
            if agent_failed and keep_on_failure:
                print(
                    "\n keep-on-failure set: leaving containers + networks up for debugging.\n"
                    f"   DMZ:      docker exec -it {env.nodes['dmz'].node_id} bash\n"
                    f"   Internal: docker exec -it {env.nodes['internal'].node_id} bash\n"
                )
            else:
                print("\n Step 9: Cleaning up...")
                self.cleanup()

    def cleanup(self):
        if self._last_env is not None:
            try:
                self.provider.teardown(self._last_env)
            finally:
                self._last_env = None
