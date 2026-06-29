"""Multi-agent coordination substrate.

Provides the communication backbone (coordinator, mailbox, protocol,
scenarios) that both cooperative and adversarial multi-agent scenarios
sit on top of. Role-specific behaviour lives elsewhere — this package
is intentionally role-agnostic.
"""

from ctf_agents.multi_agent.scenarios import Scenario

__all__ = ["Scenario"]
