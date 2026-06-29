"""Enumerations used across the CTF agents framework."""

from enum import Enum


class AgentRole(Enum):
    """Roles an agent can assume in a CTF scenario."""
    ATTACKER = "attacker"
    DEFENDER = "defender"
    OBSERVER = "observer"
