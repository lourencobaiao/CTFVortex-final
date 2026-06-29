"""Message protocol and tag parser.

Agents communicate in natural language with embedded machine-parseable
tags. The mailbox carries ``Message`` objects; the coordinator parses
tags out of agent output (so agents can emit them inline without
breaking the existing "one command per turn" contract).

Tag forms recognised:

    <MSG to="agent-1">hey, try /admin</MSG>
    <MSG to="*team:red">found a login form</MSG>
    <MSG to="*broadcast">stopping for now</MSG>
    <FINDING key="endpoint" value="/admin.php"/>
    <ACTION target="server" verb="patch"/>      (reserved for Phase 3)

The ``<MSG>`` block is stripped before the agent's command is executed
by the terminal (messages are free — they do not cost a step's
command slot unless the scenario opts in).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


CHANNEL_DIRECT = "direct"
CHANNEL_TEAM = "team"
CHANNEL_ENV = "env"

RECIPIENT_BROADCAST = "*broadcast"
TEAM_PREFIX = "*team:"


@dataclass
class Message:
    """A single message delivered through the mailbox."""

    sender: str                     # agent_id, or "env" for environment signals
    recipients: List[str]           # agent_ids, or ["*team:<id>"], or ["*broadcast"]
    channel: str                    # CHANNEL_DIRECT | CHANNEL_TEAM | CHANNEL_ENV
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def make(
        cls,
        sender: str,
        recipients: List[str],
        content: str,
        channel: Optional[str] = None,
        tick: Optional[int] = None,
        max_length: Optional[int] = None,
    ) -> "Message":
        """Factory with length truncation and channel auto-detection."""
        if max_length is not None and len(content) > max_length:
            content = content[: max_length - 3] + "..."
        if channel is None:
            channel = cls._infer_channel(recipients)
        metadata = {"timestamp": datetime.utcnow().isoformat()}
        if tick is not None:
            metadata["tick"] = tick
        return cls(sender=sender, recipients=list(recipients),
                   channel=channel, content=content, metadata=metadata)

    @staticmethod
    def _infer_channel(recipients: List[str]) -> str:
        if any(r == RECIPIENT_BROADCAST for r in recipients):
            return CHANNEL_TEAM  # broadcast is a team-channel with wildcard scope
        if any(r.startswith(TEAM_PREFIX) for r in recipients):
            return CHANNEL_TEAM
        return CHANNEL_DIRECT

    def format_for_inbox(self) -> str:
        """Render this message for inclusion in an agent's next prompt."""
        scope = ", ".join(self.recipients) if self.recipients else "(nobody)"
        return f"[from {self.sender} -> {scope}] {self.content}"


# ----------------------------------------------------------------------
# Tag parser
# ----------------------------------------------------------------------

_MSG_TAG = re.compile(
    r"<MSG\s+to=\"(?P<to>[^\"]+)\"\s*>(?P<body>.*?)</MSG>",
    re.DOTALL | re.IGNORECASE,
)
_FINDING_TAG = re.compile(
    r"<FINDING\s+key=\"(?P<key>[^\"]+)\"\s+value=\"(?P<value>[^\"]*)\"\s*/?>",
    re.IGNORECASE,
)


@dataclass
class ParsedOutput:
    """Result of parsing an agent's raw LLM output.

    ``command`` is what should be executed on the terminal (all tags
    stripped). ``messages`` and ``findings`` are the extracted intents.
    """

    command: str
    messages: List[Tuple[List[str], str]] = field(default_factory=list)  # (recipients, body)
    findings: List[Tuple[str, str]] = field(default_factory=list)        # (key, value)


def parse_agent_output(raw: str) -> ParsedOutput:
    """Extract <MSG>/<FINDING> tags and return the cleaned command."""
    messages: List[Tuple[List[str], str]] = []
    findings: List[Tuple[str, str]] = []

    for m in _MSG_TAG.finditer(raw):
        to_field = m.group("to").strip()
        recipients = [r.strip() for r in to_field.split(",") if r.strip()]
        messages.append((recipients, m.group("body").strip()))

    for m in _FINDING_TAG.finditer(raw):
        findings.append((m.group("key").strip(), m.group("value").strip()))

    cleaned = _MSG_TAG.sub("", raw)
    cleaned = _FINDING_TAG.sub("", cleaned)
    cleaned = cleaned.strip()

    return ParsedOutput(command=cleaned, messages=messages, findings=findings)
