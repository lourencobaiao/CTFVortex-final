"""Abstract base class for CTF orchestration strategies."""

from abc import ABC, abstractmethod
from typing import Dict, Any


class Orchestrator(ABC):
    """Common interface for all CTF orchestration strategies.

    Every orchestrator coordinates infrastructure, AI providers, and agents
    to execute one or more CTF challenges.  Concrete subclasses define *how*
    the pieces are wired together (single agent, multi-agent, pipeline, …).
    """

    @abstractmethod
    def run(self, **kwargs) -> Dict[str, Any]:
        """Execute the orchestrated workflow.

        Returns a result dict compatible with ``CTFAgent.run()`` output
        (flags_found, total_attempts, time_elapsed, solved, …).
        """

    @abstractmethod
    def cleanup(self):
        """Release resources and tear down infrastructure."""
