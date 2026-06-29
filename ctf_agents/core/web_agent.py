"""Web-based CTF agent — uses curl/wget via SSH to analyse websites and find flags."""

import re
from typing import Dict

from ctf_agents.core.agents import CTFAgent
from ctf_agents.core.providers import AIProvider
from ctf_agents.core.connections import TerminalConnection


class WebCTFAgent(CTFAgent):
    """Agent specialized for web-based CTF challenges using CLI tools."""

    def __init__(self, ai_provider: AIProvider, terminal: TerminalConnection,
                 challenge_name: str, challenge_description: str = "",
                 web_url: str = "http://localhost",
                 flag_format: str = r"flag\{[^\}]+\}|CTF\{[^\}]+\}|[A-Z0-9]{32}"):
        super().__init__(ai_provider, terminal, challenge_name,
                         challenge_description, flag_format)
        self.web_url = web_url
        self.discovered_endpoints: set = set()

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def get_initial_prompt(self) -> str:
        base_prompt = f"""You are an expert web penetration tester and CTF player solving a web-based challenge.

 CHALLENGE: {self.challenge_name}
"""
        if self.challenge_description:
            base_prompt += f" DESCRIPTION: {self.challenge_description}\n"

        base_prompt += f"""
 TARGET: {self.web_url}

 YOUR CAPABILITIES:
You have access to a Linux terminal with web analysis tools. You can use:

**Reconnaissance Tools:**
- curl: Fetch web pages and analyze HTTP responses
  Examples:
  - curl {self.web_url}
  - curl -I {self.web_url} (headers only)
  - curl -v {self.web_url} (verbose)
  - curl -L {self.web_url}/robots.txt

- wget: Download files
  Examples:
  - wget {self.web_url}/index.html
  - wget -r -l1 {self.web_url} (recursive 1 level)

- grep: Search for patterns
  Examples:
  - curl {self.web_url} | grep -i "flag"
  - curl {self.web_url} | grep -oP 'flag\\{{[^\\}}]+\\}}'

**Text Processing:**
- cat, head, tail: View file contents
- strings: Extract readable strings
- base64: Decode base64 encoded data
  Example: echo "ZmxhZ3t0ZXN0fQ==" | base64 -d
- sed, awk: Text manipulation

**Analysis Strategy:**
1. Fetch the main page: curl {self.web_url}
2. Check robots.txt: curl {self.web_url}/robots.txt
3. Analyze HTML source: Look for comments, hidden fields, JavaScript
4. Search for flag patterns: Use grep with regex
5. Check HTTP headers: curl -I {self.web_url}
6. Decode any encoded data: base64, hex, etc.
7. Follow links to other pages found in HTML

 COMMON CTF WEB LOCATIONS:
- HTML comments: <!-- flag{{...}} -->
- robots.txt: Disallowed paths may contain secrets
- JavaScript code: Look in <script> tags
- Hidden form fields: <input type="hidden">
- HTTP headers: X-Flag, X-Debug, etc.
- Base64 encoded strings
- Secret pages: /admin, /secret, /flag, etc.

 YOUR OBJECTIVE:
1. Use CLI tools to explore the website
2. Find and extract the hidden flag
3. When you find the flag, display it clearly with: echo "FLAG_FOUND: <flag>"

 RULES:
- Respond ONLY with Linux/Bash commands (no explanations, no markdown)
- One command per response
- Build on previous discoveries
- When you've successfully found and displayed the flag, respond with: exit

The next messages will contain your command outputs. Start your reconnaissance now with a curl command."""
        return base_prompt + self.multi_agent_prompt_suffix()

    # ------------------------------------------------------------------
    # Overrides / hooks
    # ------------------------------------------------------------------

    def _exit_commands(self) -> set:
        return {"exit", "quit", "done"}

    def _extract_endpoints(self, output: str):
        """Extract potential endpoints from HTML/text output."""
        href_pattern = r'(?:href|src)=["\']([^"\']+)["\']'
        matches = re.findall(href_pattern, output, re.IGNORECASE)
        for match in matches:
            if match.startswith("/") or match.startswith("http"):
                self.discovered_endpoints.add(match)

    def log_command_result(self, command: str, output: str, success: bool):
        super().log_command_result(command, output, success)
        if success and ("curl" in command or "wget" in command):
            self._extract_endpoints(output)

    def _build_output_context(self, output: str, iteration: int) -> str:
        context = f"Command output:\n{output}"
        if self.discovered_endpoints and iteration % 5 == 0:
            context += f"\n\nDiscovered endpoints so far: {', '.join(list(self.discovered_endpoints)[:5])}"
        return context

    def _on_iteration_error(self, iteration: int, error: Exception) -> bool:
        # Web agent continues on error (tries a different approach)
        return True

    def _get_extra_report_fields(self) -> Dict:
        return {
            "target_url": self.web_url,
            "discovered_endpoints": list(self.discovered_endpoints),
        }

    def _print_extra_report_lines(self, report: Dict):
        endpoints = report.get("discovered_endpoints", [])
        print(f"Endpoints Discovered: {len(endpoints)}")
        if endpoints:
            print(f"    {', '.join(endpoints[:10])}")

    def _print_run_header(self, max_iterations: int):
        print("=" * 70)
        print(f" WEB CTF CHALLENGE SOLVER")
        print("=" * 70)
        print(f" Challenge: {self.challenge_name}")
        print(f" Target URL: {self.web_url}")
        print(f" AI Provider: {self.ai_provider.get_provider_name()}")
        print(f" Max Iterations: {max_iterations}")
        print(f" Flag Format: {self.flag_format}")
        print("=" * 70 + "\n")
