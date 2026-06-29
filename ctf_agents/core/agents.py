"""Base CTF Agent with Template Method pattern for the run loop."""

from abc import ABC, abstractmethod
import re
from typing import List, Dict, Optional, Any
from datetime import datetime

from ctf_agents.core.providers import AIProvider
from ctf_agents.core.connections import TerminalConnection
from ctf_agents.core.enums import AgentRole
from ctf_agents.core.primitives import EventBus, SharedState
from ctf_agents.reporting.engine import generate_base_report, print_report


# Lazy imports used by perceive/send_message so this module stays usable
# without the multi_agent package present.
def _protocol():
    from ctf_agents.multi_agent import protocol as _p  # local import
    return _p


class CTFAgent(ABC):
    """Abstract base class for CTF challenge solving agents.

    Subclasses must implement ``get_initial_prompt``.
    The default ``run`` method uses the Template Method pattern — override
    the hook methods (``_on_iteration_error``, ``_build_output_context``,
    ``_get_extra_report_fields``, ``_print_extra_report_lines``) to
    customise behaviour without duplicating the main loop.

    For multi-agent execution, the loop is split into ``start()`` +
    repeated ``step()`` + ``stop()``. ``run()`` is a thin wrapper that
    preserves the original single-agent behaviour.
    """

    def __init__(self, ai_provider: AIProvider, terminal: TerminalConnection,
                 challenge_name: str, challenge_description: str = "",
                 flag_format: str = r"flag\{[^\}]+\}|CTF\{[^\}]+\}|[A-Z0-9]{32}",
                 agent_id: str = "agent-0",
                 role: Optional[AgentRole] = None,
                 team_id: Optional[str] = None,
                 event_bus: Optional[EventBus] = None,
                 shared_state: Optional[SharedState] = None):
        self.ai_provider = ai_provider
        self.terminal = terminal
        self.challenge_name = challenge_name
        self.challenge_description = challenge_description
        self.flag_format = flag_format
        self.flag_regex = re.compile(flag_format, re.IGNORECASE)
        self.agent_id = agent_id
        self.role = role if role is not None else AgentRole.ATTACKER
        self.team_id = team_id
        self.event_bus = event_bus
        self.shared_state = shared_state
        self.mailbox = None                     # set by coordinator if messaging is enabled
        self._msg_cap_per_tick: Optional[int] = None
        self._pending_perception: Optional[str] = None
        self.scenario_brief: Optional[str] = None  # set by orchestrator; appended in multi_agent_prompt_suffix
        self.conversation_history: List[Dict[str, str]] = []
        self.discovered_flags: List[str] = []
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self.attempts = 0
        self.successful_commands: List = []
        self.failed_commands: List = []
        self.messages_sent: int = 0
        self.messages_received: int = 0
        self.solved = False

    # ------------------------------------------------------------------
    # Abstract – must be implemented by every subclass
    # ------------------------------------------------------------------

    @abstractmethod
    def get_initial_prompt(self) -> str:
        """Return the system/initial prompt for this challenge type."""

    # ------------------------------------------------------------------
    # Shared flag logic (overridable)
    # ------------------------------------------------------------------

    def extract_flag(self, text: str) -> Optional[str]:
        """Extract CTF flag from *text* using the configured regex."""
        matches = self.flag_regex.findall(text)
        for match in matches:
            if len(match) > 5:
                return match
        return None

    def is_challenge_solved(self, command: str, output: str) -> bool:
        """Return True when the challenge is considered solved."""
        # Explicit marker
        if "FLAG_FOUND:" in output.upper():
            flag_part = output.split("FLAG_FOUND:")[-1].strip()
            flag = self.extract_flag(flag_part) or self.extract_flag(output)
            if flag and flag not in self.discovered_flags:
                self.discovered_flags.append(flag)
                return True

        # Auto-detect
        flag = self.extract_flag(output)
        if flag and flag not in self.discovered_flags:
            self.discovered_flags.append(flag)
            print(f"\n FLAG DETECTED: {flag}")
            return True

        # Exit command
        if command.lower().strip() in self._exit_commands():
            return len(self.discovered_flags) > 0

        return False

    def _exit_commands(self) -> set:
        """Commands that signal the agent wants to stop."""
        return {"exit", "quit"}

    # ------------------------------------------------------------------
    # Command logging (overridable hook)
    # ------------------------------------------------------------------

    def log_command_result(self, command: str, output: str, success: bool):
        """Log command execution result. Override to add extra tracking."""
        if success:
            self.successful_commands.append((command, output))
        else:
            self.failed_commands.append((command, output))

    # ------------------------------------------------------------------
    # Reporting (delegates to reporting.engine)
    # ------------------------------------------------------------------

    def _get_extra_report_fields(self) -> Dict:
        """Return extra fields to merge into the report dict."""
        return {}

    def _print_extra_report_lines(self, report: Dict):
        """Print additional lines inside the formatted report."""

    def generate_report(self) -> Dict[str, any]:
        report = generate_base_report(
            challenge_name=self.challenge_name,
            challenge_description=self.challenge_description,
            discovered_flags=self.discovered_flags,
            attempts=self.attempts,
            successful_commands=self.successful_commands,
            failed_commands=self.failed_commands,
            start_time=self.start_time,
            end_time=self.end_time,
            extra_fields=self._get_extra_report_fields(),
        )
        report["agent_id"] = self.agent_id
        report["role"] = self.role.value
        if self.team_id is not None:
            report["team_id"] = self.team_id
        return report

    def print_final_report(self, report: Dict[str, any]):
        print_report(report, extra_lines_fn=self._print_extra_report_lines)

    # ------------------------------------------------------------------
    # Template Method hooks for the run loop
    # ------------------------------------------------------------------

    def _print_run_header(self, max_iterations: int):
        """Print header at the start of a run. Override for custom banners."""
        print("=" * 70)
        print(f" CTF CHALLENGE SOLVER")
        print("=" * 70)
        print(f" Challenge: {self.challenge_name}")
        print(f" AI Provider: {self.ai_provider.get_provider_name()}")
        print(f" Max Iterations: {max_iterations}")
        print(f" Flag Format: {self.flag_format}")
        print("=" * 70 + "\n")

    def _build_output_context(self, output: str, iteration: int) -> str:
        """Build the user message added to conversation after a command.
        Override to inject extra context (e.g. discovered endpoints)."""
        return f"Command output:\n{output}"

    def _on_iteration_error(self, iteration: int, error: Exception) -> bool:
        """Handle an error during an iteration.
        Return True to continue to the next iteration, False to break."""
        return False

    # ------------------------------------------------------------------
    # Lifecycle (start / step / stop) — used by both run() and the
    # multi-agent coordinator.
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Initialize the agent's session.

        Seeds the conversation with the initial prompt (if empty) and
        connects the terminal. Idempotent: calling twice has no extra
        effect beyond reconnecting the terminal.
        """
        if self.start_time is None:
            self.start_time = datetime.now()
        if not self.conversation_history:
            initial_prompt = self.get_initial_prompt()
            self.conversation_history.append({"role": "user", "content": initial_prompt})
        self.terminal.connect()

    def stop(self) -> None:
        """Tear down the agent's session."""
        if self.end_time is None:
            self.end_time = datetime.now()
        try:
            self.terminal.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Multi-agent messaging hooks
    # ------------------------------------------------------------------

    def multi_agent_prompt_suffix(self) -> str:
        """Return extra prompt text describing the messaging protocol.

        Subclasses should append this to their ``get_initial_prompt``
        output. Returns an empty string when the agent isn't wired into
        a multi-agent run, so the same subclass works single-agent too.
        """
        if self.mailbox is None:
            return ""
        team_line = ""
        if self.team_id is not None:
            team_line = f"\n - Your team_id is \"{self.team_id}\"; teammates can be addressed as *team:{self.team_id}."
        env_line = (
            self.scenario_brief
            if self.scenario_brief
            else "You are running alongside other agents on the same environment."
        )
        return f"""

 === MULTI-AGENT MODE ===
{env_line} Your agent_id is "{self.agent_id}".{team_line}

To coordinate, you may include the following tags INSIDE your response — they are parsed and stripped before the shell command runs:

 - <MSG to="agent_id">free-text message</MSG>         send a direct whisper
 - <MSG to="*team:{self.team_id or 'red'}">...</MSG>         broadcast to your team
 - <MSG to="*broadcast">...</MSG>                     broadcast to everyone
 - <FINDING key="endpoint" value="/admin"/>           record a shared finding

Rules:
 - Messages are free — they do NOT replace your shell command.
 - After any <MSG>/<FINDING> tags, still emit exactly one bash command on its own, or leave the rest blank to spend a turn only messaging.
 - Read the INBOX / SHARED STATE sections you will see at the start of a turn. They contain information your peers shared with you.
"""

    def perceive(self, inbox: List, env_snapshot: Optional[Dict[str, Any]] = None) -> None:
        """Absorb incoming messages and a shared-state snapshot.

        Called by the coordinator *before* each ``step()``. Content is
        stashed and injected into the conversation at the start of the
        next ``step()`` so the LLM sees it before choosing its next
        command. Safe to call with empty inputs.
        """
        if not inbox and not env_snapshot:
            return
        self.messages_received += len(inbox) if inbox else 0
        self._pending_perception = self._format_perception(inbox, env_snapshot)

    def _format_perception(self, inbox: List, env_snapshot: Optional[Dict[str, Any]]) -> str:
        """Render inbox + env snapshot into a single user-message string."""
        parts: List[str] = []
        if inbox:
            parts.append("=== INBOX ===")
            for msg in inbox:
                parts.append(f"  {msg.format_for_inbox()}")
        if env_snapshot:
            parts.append("=== SHARED STATE ===")
            for k, v in env_snapshot.items():
                val_str = repr(v)
                if len(val_str) > 200:
                    val_str = val_str[:197] + "..."
                parts.append(f"  {k} = {val_str}")
        parts.append("(Use <MSG to=\"agent_id\">...</MSG> to reply; "
                     "proceed with your next shell command below.)")
        return "\n".join(parts)

    def send_message(self, recipients: List[str], content: str,
                     channel: Optional[str] = None) -> bool:
        """Publish a message to other agents.

        Returns True on successful publish, False if no mailbox is
        attached or the scenario's per-tick cap has been reached.
        """
        if self.mailbox is None:
            return False
        p = _protocol()
        max_len = getattr(self, "_msg_max_length", None)
        msg = p.Message.make(
            sender=self.agent_id,
            recipients=recipients,
            content=content,
            channel=channel,
            max_length=max_len,
        )
        delivered = self.mailbox.send(msg, per_agent_cap=self._msg_cap_per_tick)
        sent_this_tick = getattr(self.mailbox, "_sent_this_tick", None)
        if delivered >= 0 and sent_this_tick is not None \
                and sent_this_tick.get(self.agent_id, 0) > 0:
            self.messages_sent += 1
        return True

    # ------------------------------------------------------------------
    # Core step
    # ------------------------------------------------------------------

    def step(self) -> Dict[str, Any]:
        """Execute one iteration of the agent loop.

        Returns a dict with keys:
            command:        the (cleaned) command actually executed
            raw_output:     the LLM's raw response (with tags still in it)
            output:         the terminal output (or None on error)
            solved:         whether the challenge is now solved
            iteration:      1-indexed iteration count
            error:          exception message or None
            retry:          on error, whether the agent wants to continue
            messages_sent:  count of <MSG> tags dispatched this step
            findings:       list of (key, value) <FINDING> tags this step
        """
        self.attempts += 1
        iteration = self.attempts
        command: Optional[str] = None
        raw: Optional[str] = None
        messages_dispatched = 0
        findings: List = []

        # Inject any stashed perception before the LLM sees its next turn.
        if self._pending_perception:
            self.conversation_history.append({
                "role": "user",
                "content": self._pending_perception,
            })
            self._pending_perception = None

        try:
            raw = self.ai_provider.generate_command(self.conversation_history)
            # Record the full raw response (tags and all) so the LLM keeps
            # its own past messaging context.
            self.conversation_history.append({"role": "assistant", "content": raw})

            # Parse out <MSG>/<FINDING> tags. The command that actually
            # runs on the terminal is the text with all tags stripped.
            if self.mailbox is not None or self.shared_state is not None:
                parsed = _protocol().parse_agent_output(raw)
                command = parsed.command
                for recipients, body in parsed.messages:
                    if self.send_message(recipients, body):
                        messages_dispatched += 1
                findings = list(parsed.findings)
                if self.shared_state is not None and findings:
                    for key, value in findings:
                        ns_key = f"findings:{self.agent_id}:{key}"
                        self.shared_state.set(ns_key, value)
            else:
                command = raw

            # Some LLMs emit only tags and nothing else; treat that as a
            # no-op "chat turn" and move on without touching the terminal.
            if not command or not command.strip():
                context = self._build_output_context("(no-op: messaging turn)", iteration)
                self.conversation_history.append({"role": "user", "content": context})
                return {
                    "command": "",
                    "raw_output": raw,
                    "output": "",
                    "solved": False,
                    "iteration": iteration,
                    "error": None,
                    "retry": False,
                    "messages_sent": messages_dispatched,
                    "findings": findings,
                }

            output = self.terminal.execute_command(command)

            solved = self.is_challenge_solved(command, output)
            self.log_command_result(command, output, True)

            if solved:
                self.solved = True
            else:
                context = self._build_output_context(output, iteration)
                self.conversation_history.append({"role": "user", "content": context})

            return {
                "command": command,
                "raw_output": raw,
                "output": output,
                "solved": solved,
                "iteration": iteration,
                "error": None,
                "retry": False,
                "messages_sent": messages_dispatched,
                "findings": findings,
            }

        except Exception as e:
            self.log_command_result(command if command is not None else "unknown",
                                    str(e), False)
            retry = self._on_iteration_error(iteration, e)
            if retry:
                self.conversation_history.append({
                    "role": "user",
                    "content": f"Error executing command: {e}. Try a different approach."
                })
            return {
                "command": command,
                "raw_output": raw,
                "output": None,
                "solved": False,
                "iteration": iteration,
                "error": str(e),
                "retry": retry,
                "messages_sent": messages_dispatched,
                "findings": findings,
            }

    # ------------------------------------------------------------------
    # Main run loop (Template Method, single-agent)
    # ------------------------------------------------------------------

    def run(self, max_iterations: int = 30) -> Dict[str, any]:
        """Execute the CTF challenge solving process (single-agent mode)."""
        self._print_run_header(max_iterations)
        self.start()

        try:
            for _ in range(max_iterations):
                iteration = self.attempts + 1
                print(f"\n{'─' * 70}")
                print(f"-- Iteration {iteration}/{max_iterations}")
                print("─" * 70)

                result = self.step()

                if result["error"] is not None:
                    print(f"❌ Error during iteration {result['iteration']}: {result['error']}")
                    if not result["retry"]:
                        break
                    continue

                print(f"==> AI Command: {result['command']}")
                print(f"==> Output:\n{result['output']}\n")

                if result["solved"]:
                    self.end_time = datetime.now()
                    print("\n" + "=" * 70)
                    print(" CHALLENGE SOLVED!")
                    print("=" * 70)
                    if self.discovered_flags:
                        print(f" Flags found: {', '.join(self.discovered_flags)}")
                    print(f"  Time taken: {(self.end_time - self.start_time).total_seconds():.2f} seconds")
                    print(f" Commands executed: {result['iteration']}")
                    print("=" * 70)
                    break
            else:
                self.end_time = datetime.now()
                print(f"\n Max iterations ({max_iterations}) reached")
                if self.discovered_flags:
                    print(f" Partial flags found: {', '.join(self.discovered_flags)}")

        finally:
            self.stop()

        report = self.generate_report()
        self.print_final_report(report)
        return report
