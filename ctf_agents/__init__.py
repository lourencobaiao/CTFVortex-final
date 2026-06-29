"""
CTF AI Agents Framework
=======================

A framework for AI-powered CTF challenge solving with support for
multiple LLM providers, Docker/GNS3 infrastructure, and procedural
challenge generation.

Package structure:
    ctf_agents.core        - AI providers, terminal connections, agent classes
    ctf_agents.infra       - Docker and infrastructure management
    ctf_agents.pcg         - Procedural Content Generation for CTF challenges
    ctf_agents.runners     - End-to-end workflow orchestrators
    ctf_agents.reporting   - Report generation and formatting
"""

# Core: Providers
from ctf_agents.core.providers import (
    AIProvider, OpenRouterProvider, GroqProvider, FallbackProvider,
    make_default_provider,
)

# Core: Connections
from ctf_agents.core.connections import TerminalConnection, SSHTerminal

# Core: Agents
from ctf_agents.core.agents import CTFAgent
from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
from ctf_agents.core.web_agent import WebCTFAgent

# Infrastructure
from ctf_agents.infra.docker_manager import DockerEnvironmentManager, run_ctf_with_reset

# PCG
from ctf_agents.pcg.website_generator import CTFWebsiteGenerator

# Runners
from ctf_agents.runners.web_runner import WebCTFRunner

# Orchestration
from ctf_agents.orchestration.base import Orchestrator
from ctf_agents.orchestration.single_agent import SingleAgentOrchestrator
from ctf_agents.orchestration.tournament import TournamentOrchestrator

# Scoring
from ctf_agents.scoring.engine import ScoringEngine, ScoreBreakdown

# Reporting
from ctf_agents.reporting.engine import generate_base_report, print_report, save_report

# Enums & Primitives
from ctf_agents.core.enums import AgentRole
from ctf_agents.core.primitives import SharedState, EventBus

__all__ = [
    # Providers
    "AIProvider",
    "OpenRouterProvider",
    "GroqProvider",
    "FallbackProvider",
    "make_default_provider",
    # Connections
    "TerminalConnection",
    "SSHTerminal",
    # Agents
    "CTFAgent",
    "JeopardyCTFAgent",
    "WebCTFAgent",
    # Infrastructure
    "DockerEnvironmentManager",
    "run_ctf_with_reset",
    # PCG
    "CTFWebsiteGenerator",
    # Runners
    "WebCTFRunner",
    # Orchestration
    "Orchestrator",
    "SingleAgentOrchestrator",
    "TournamentOrchestrator",
    # Scoring
    "ScoringEngine",
    "ScoreBreakdown",
    # Reporting
    "generate_base_report",
    "print_report",
    "save_report",
    # Enums & Primitives
    "AgentRole",
    "SharedState",
    "EventBus",
]
