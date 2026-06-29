"""Deterministic mock test for the Diamond Vault scenario.

Drives two ``JeopardyCTFAgent`` instances through the real
``MultiAgentCoordinator`` + mailbox + protocol, but with:

* a ``ScriptedProvider`` instead of a real LLM (responses are fixed)
* a ``FakeEnv`` instead of a real SSH terminal (simulates each env's
  filesystem: ``cat ~/public_key.txt`` returns the own_key, ``vault <k>``
  returns the flag iff ``k`` matches the peer_key)

This is a substrate test — it verifies the messaging layer, win
condition, and coordinator wiring all work end-to-end without needing
Docker or an LLM. Run with:

    python tests/test_diamond_vault_mock.py
"""

from __future__ import annotations

import os
import sys
from typing import List

# Make ``ctf_agents`` importable when the file is executed directly.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ctf_agents.core.connections import TerminalConnection
from ctf_agents.core.enums import AgentRole
from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
from ctf_agents.core.providers import AIProvider
from ctf_agents.multi_agent.coordinator import MultiAgentCoordinator
from ctf_agents.multi_agent.diamond_vault import (
    FLAG,
    KEY_A,
    KEY_B,
    build_diamond_vault_scenario,
)


class ScriptedProvider(AIProvider):
    """Returns pre-baked responses in order. Falls back to a safe no-op."""

    def __init__(self, responses: List[str], name: str = "scripted"):
        self.responses = list(responses)
        self._name = name
        self.model = name

    def generate_command(self, conversation_history) -> str:
        if self.responses:
            return self.responses.pop(0)
        return "echo idle"

    def get_provider_name(self) -> str:
        return self._name


class FakeEnv(TerminalConnection):
    """Simulates the filesystem of one diamond-vault container.

    Only the two commands the scenario actually needs are modelled:
    reading ``public_key.txt`` and invoking ``vault <key>``.
    """

    def __init__(self, own_key: str, peer_key: str, flag: str):
        self.own_key = own_key
        self.peer_key = peer_key
        self.flag = flag
        self._connected = False

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def execute_command(self, command: str) -> str:
        cmd = command.strip()
        if cmd.startswith("cat") and "public_key.txt" in cmd:
            return self.own_key
        # Accept "vault X", "/usr/local/bin/vault X", "sudo vault X",
        # "sudo /usr/local/bin/vault X" — modeling real-world invocation.
        tokens = cmd.split()
        if tokens and tokens[0] == "sudo":
            tokens = tokens[1:]
        if tokens and (tokens[0] == "vault" or tokens[0] == "/usr/local/bin/vault"):
            if len(tokens) >= 2:
                arg = tokens[1]
                if arg == self.peer_key:
                    return self.flag
                return "403: invalid vault key"
            return "usage: vault <key>"
        if cmd in {"exit", "quit"}:
            return ""
        return ""


def _make_agent(agent_id: str, peer_id: str, own_key: str, peer_key: str, responses: List[str]) -> JeopardyCTFAgent:
    agent = JeopardyCTFAgent(
        ai_provider=ScriptedProvider(responses, name=f"scripted-{agent_id}"),
        terminal=FakeEnv(own_key, peer_key, FLAG),
        challenge_name="Diamond Vault",
        challenge_description="",
        flag_format=r"flag\{[^\}]+\}",
    )
    agent.agent_id = agent_id
    agent.role = AgentRole.ATTACKER
    agent.team_id = "coop"
    agent.scenario_brief = f"test mode: peer is {peer_id}"
    return agent


def main() -> int:
    scenario = build_diamond_vault_scenario(max_ticks=5)

    agent_a = _make_agent(
        agent_id="agent-a",
        peer_id="agent-b",
        own_key=KEY_A,
        peer_key=KEY_B,
        responses=[
            f'<MSG to="agent-b">{KEY_A}</MSG>\ncat /home/sandboxuser/public_key.txt',
            f"sudo /usr/local/bin/vault {KEY_B}",
        ],
    )
    agent_b = _make_agent(
        agent_id="agent-b",
        peer_id="agent-a",
        own_key=KEY_B,
        peer_key=KEY_A,
        responses=[
            f'<MSG to="agent-a">{KEY_B}</MSG>\ncat /home/sandboxuser/public_key.txt',
            f"sudo /usr/local/bin/vault {KEY_A}",
        ],
    )

    coord = MultiAgentCoordinator(agents=[agent_a, agent_b], scenario=scenario)
    for agent in [agent_a, agent_b]:
        agent.start()

    summary = coord.run()

    for agent in [agent_a, agent_b]:
        agent.stop()

    errors: List[str] = []
    if not agent_a.solved:
        errors.append(f"agent-a not solved (discovered_flags={agent_a.discovered_flags})")
    if not agent_b.solved:
        errors.append(f"agent-b not solved (discovered_flags={agent_b.discovered_flags})")
    if FLAG not in agent_a.discovered_flags:
        errors.append(f"agent-a missing FLAG (got {agent_a.discovered_flags})")
    if FLAG not in agent_b.discovered_flags:
        errors.append(f"agent-b missing FLAG (got {agent_b.discovered_flags})")
    if summary.ticks_elapsed > 2:
        errors.append(f"took {summary.ticks_elapsed} ticks, expected ≤2")
    if summary.stop_reason != "win_condition":
        errors.append(f"stop_reason={summary.stop_reason!r}, expected 'win_condition'")
    if agent_a.messages_sent < 1:
        errors.append(f"agent-a sent {agent_a.messages_sent} messages, expected ≥1")
    if agent_b.messages_sent < 1:
        errors.append(f"agent-b sent {agent_b.messages_sent} messages, expected ≥1")

    if errors:
        print("\n=== FAIL ===")
        for e in errors:
            print(f"  - {e}")
        return 1

    print("\n=== PASS ===")
    print(f"  ticks_elapsed = {summary.ticks_elapsed}")
    print(f"  agent-a messages_sent = {agent_a.messages_sent}, received = {agent_a.messages_received}")
    print(f"  agent-b messages_sent = {agent_b.messages_sent}, received = {agent_b.messages_received}")
    print(f"  both solved, both captured {FLAG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
