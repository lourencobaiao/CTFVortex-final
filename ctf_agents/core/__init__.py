"""Core module: AI providers, terminal connections, and agent base classes."""

from ctf_agents.core.providers import (
    AIProvider, OpenRouterProvider, GroqProvider, FallbackProvider,
    make_default_provider,
)
from ctf_agents.core.connections import TerminalConnection, SSHTerminal
from ctf_agents.core.agents import CTFAgent
from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
from ctf_agents.core.web_agent import WebCTFAgent
from ctf_agents.core.enums import AgentRole
from ctf_agents.core.primitives import SharedState, EventBus

__all__ = [
    "AIProvider",
    "OpenRouterProvider",
    "GroqProvider",
    "FallbackProvider",
    "make_default_provider",
    "TerminalConnection",
    "SSHTerminal",
    "CTFAgent",
    "JeopardyCTFAgent",
    "WebCTFAgent",
    "AgentRole",
    "SharedState",
    "EventBus",
]
