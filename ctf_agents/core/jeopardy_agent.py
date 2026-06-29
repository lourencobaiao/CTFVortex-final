"""Jeopardy-style CTF agent — explores a Linux system via SSH to find flags."""

from ctf_agents.core.agents import CTFAgent


class JeopardyCTFAgent(CTFAgent):
    """Agent specialized for Jeopardy-style CTF challenges."""

    def get_initial_prompt(self) -> str:
        base_prompt = f"""You are an expert CTF (Capture The Flag) player participating in a Jeopardy-style CTF challenge.

 CHALLENGE: {self.challenge_name}
"""
        if self.challenge_description:
            base_prompt += f"📝 DESCRIPTION: {self.challenge_description}\n"

        base_prompt += """

 YOUR CAPABILITIES:
- You have SSH access to a Linux system where the challenge is hosted
- You can execute any Linux command to explore, analyze, and solve the challenge
- Use reconnaissance commands (ls, find, cat, grep, strings, file, etc.)
- Look for hidden files, SUID binaries, environment variables, running processes
- Check common CTF locations: /tmp, /home, /root, /opt, /var/www

 YOUR OBJECTIVE:
1. Explore the system thoroughly
2. Find vulnerabilities or hidden information
3. Locate and extract the flag
4. When you find the flag, use the command: echo "FLAG_FOUND: <actual_flag>"

 RULES:
- Respond ONLY with Linux commands (no explanations, no markdown)
- Be methodical: reconnaissance → exploitation → flag extraction
- Each command should build on previous discoveries
- When you've found the flag and displayed it, respond with: exit
- Only one command at a time

The next messages will contain the output of your commands. Start your reconnaissance now."""
        return base_prompt + self.multi_agent_prompt_suffix()

    def _print_run_header(self, max_iterations: int):
        print("=" * 70)
        print(f" CTF JEOPARDY CHALLENGE SOLVER")
        print("=" * 70)
        print(f" Challenge: {self.challenge_name}")
        print(f" AI Provider: {self.ai_provider.get_provider_name()}")
        print(f"  Max Iterations: {max_iterations}")
        print(f" Flag Format: {self.flag_format}")
        print("=" * 70 + "\n")
