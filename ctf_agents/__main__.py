"""
CTF AI Agents — unified CLI entry point.

Usage:
    python -m ctf_agents                        # interactive menu
    python -m ctf_agents jeopardy               # run jeopardy challenge
    python -m ctf_agents web                    # run single web challenge
    python -m ctf_agents web --batch 5          # run 5 web challenges
    python -m ctf_agents multi --roster attacker:2  # 2 cooperating agents
    python -m ctf_agents diamond-vault          # 2 agents, isolated envs, must cooperate
    python -m ctf_agents docker-pivot           # 1 agent, DMZ→Internal lateral movement
    python -m ctf_agents generate               # generate websites only
    python -m ctf_agents build-docker           # build Docker image
"""

import argparse
import os
import sys

from ctf_agents.config import (
    DEFAULT_MODEL, SSH_HOST, SSH_PORT, SSH_USER, SSH_PASS,
    DOCKER_CONTAINER, DOCKER_IMAGE, MAX_ITERATIONS, FLAG_FORMAT,
)


def _get_api_key() -> str:
    """Return the primary API key (Groq) and validate at least one provider
    has credentials configured.

    Groq is the primary provider (better free-tier rate limits), OpenRouter
    is the fallback. Either key alone is enough; if both are set the chain
    uses Groq first and falls over to OpenRouter on failure.
    """
    groq_key = os.getenv("GROQ_API_KEY", "")
    openrouter_key = os.getenv("OPENROUTER_API_KEY", "")

    if not groq_key and not openrouter_key:
        print("Error: no provider API key configured.")
        print("Set GROQ_API_KEY (primary) and/or OPENROUTER_API_KEY (fallback):")
        print("  export GROQ_API_KEY='your-groq-key'")
        print("  export OPENROUTER_API_KEY='your-openrouter-key'")
        sys.exit(1)

    # Return Groq key when available (treated as primary); otherwise return
    # the OpenRouter key so the single-provider path still works.
    return groq_key or openrouter_key


# ------------------------------------------------------------------
# Subcommand handlers
# ------------------------------------------------------------------

def cmd_jeopardy(args):
    """Run a Jeopardy-style CTF challenge with Docker reset."""
    from ctf_agents.infra.docker_manager import run_ctf_with_reset
    from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
    from ctf_agents.reporting.engine import save_report

    api_key = _get_api_key()

    docker_config = {
        "container_name": args.container,
        "image_name": args.image,
        "ssh_port": args.ssh_port,
        "dockerfile_path": "./Dockerfile",
    }

    ctf_config = {
        "api_key": api_key,
        "model": args.model,
        "challenge_name": args.challenge,
        "challenge_description": args.description,
        "flag_format": args.flag_format,
        "ssh_host": SSH_HOST,
        "ssh_port": args.ssh_port,
        "ssh_user": SSH_USER,
        "ssh_pass": SSH_PASS,
        "max_iterations": args.max_iterations,
    }

    result = run_ctf_with_reset(
        agent_class=JeopardyCTFAgent,
        config=ctf_config,
        docker_config=docker_config,
        reset_before=True,
        reset_after=True,
    )

    if result:
        save_report(result, prefix="ctf_report", output_dir="./reports")


def cmd_web(args):
    """Run web-based CTF challenge(s)."""
    from ctf_agents.runners.web_runner import WebCTFRunner

    api_key = _get_api_key()
    runner = WebCTFRunner(api_key=api_key, model=args.model)

    if args.batch:
        runner.run_batch_challenges(count=args.batch, max_iterations=args.max_iterations)
    else:
        runner.run_single_challenge(
            challenge_type=args.challenge_type,
            max_iterations=args.max_iterations,
        )


def _parse_roster(spec: str):
    """Parse "role:N[,role:N,...]" into a list of AgentSpec.

    Examples:
        attacker:2          -> two attackers on team "red"
        attacker:2,defender:1 -> two attackers + one defender (reserved for Phase 3)
    """
    from ctf_agents.multi_agent.scenarios import AgentSpec

    specs = []
    index = 0
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise ValueError(
                f"Bad roster entry {chunk!r}. Expected 'role:count' (e.g. 'attacker:2')."
            )
        role, count_str = chunk.split(":", 1)
        role = role.strip()
        try:
            count = int(count_str)
        except ValueError:
            raise ValueError(f"Bad count {count_str!r} in roster entry {chunk!r}")
        team = "red" if role == "attacker" else ("blue" if role == "defender" else None)
        for _ in range(count):
            specs.append(AgentSpec(
                agent_id=f"{role}-{index}",
                agent_class="web",
                role=role,
                team_id=team,
            ))
            index += 1
    if not specs:
        raise ValueError("Roster is empty.")
    return specs


def cmd_multi(args):
    """Run a multi-agent scenario on a shared container."""
    from ctf_agents.multi_agent.scenarios import Scenario, MessagingConfig, any_agent_solved, all_agents_solved
    from ctf_agents.orchestration.multi_agent import MultiAgentOrchestrator
    from ctf_agents.reporting.engine import save_report

    api_key = _get_api_key()

    roster = _parse_roster(args.roster)

    win_condition = all_agents_solved if args.require_all else any_agent_solved

    scenario = Scenario(
        name=args.scenario_name,
        roster=roster,
        max_ticks=args.max_ticks,
        win_condition=win_condition,
        messaging=MessagingConfig(),
    )

    orchestrator = MultiAgentOrchestrator(
        api_key=api_key,
        scenario=scenario,
        default_model=args.model,
        challenge_type=args.challenge_type,
    )

    result = orchestrator.run()
    if result:
        save_report(result, prefix="multi_agent_report", output_dir="./reports")


def cmd_diamond_vault(args):
    """Run the Diamond Vault cooperative scenario (2 agents, isolated envs)."""
    from ctf_agents.multi_agent.diamond_vault import build_diamond_vault_scenario
    from ctf_agents.orchestration.multi_agent import MultiAgentOrchestrator
    from ctf_agents.reporting.engine import save_report

    api_key = _get_api_key()

    scenario = build_diamond_vault_scenario(
        model=args.model,
        max_ticks=args.max_ticks,
    )

    orchestrator = MultiAgentOrchestrator(
        api_key=api_key,
        scenario=scenario,
        default_model=args.model,
    )

    result = orchestrator.run()
    if result:
        save_report(result, prefix="diamond_vault_report", output_dir="./reports")


def cmd_docker_pivot(args):
    """Run the Docker Pivot single-agent lateral-movement scenario."""
    from ctf_agents.scenarios.docker_pivot import build_docker_pivot_scenario
    from ctf_agents.orchestration.docker_pivot import DockerPivotOrchestrator
    from ctf_agents.reporting.engine import save_report

    api_key = _get_api_key()

    scenario = build_docker_pivot_scenario(
        model=args.model,
        max_iterations=args.max_iterations,
    )

    orchestrator = DockerPivotOrchestrator(
        api_key=api_key,
        scenario=scenario,
        model=args.model,
    )

    result = orchestrator.run(
        max_iterations=args.max_iterations,
        keep_on_failure=args.keep_on_failure,
    )
    if result:
        save_report(result, prefix="docker_pivot_report", output_dir="./reports")


def cmd_generate(args):
    """Generate CTF websites without running an agent."""
    from ctf_agents.pcg.website_generator import CTFWebsiteGenerator

    gen = CTFWebsiteGenerator(output_dir="./ctf_websites")
    gen.generate_batch(count=args.count)


def cmd_build_docker(args):
    """Build the Docker image for web CTF challenges."""
    from ctf_agents.runners.web_runner import WebCTFRunner

    runner = WebCTFRunner(api_key="unused", model="unused")
    runner.build_docker_image()


def cmd_interactive(_args):
    """Show an interactive menu (default when no subcommand is given)."""
    from ctf_agents.runners.web_runner import WebCTFRunner

    api_key = _get_api_key()
    runner = WebCTFRunner(api_key=api_key)

    print("\n" + "=" * 70)
    print(" CTF AI AGENTS")
    print("=" * 70)
    print("1. Run Jeopardy challenge")
    print("2. Run single Web challenge")
    print("3. Run specific Web challenge type")
    print("4. Run batch of Web challenges")
    print("5. Generate websites only")
    print("6. Build Docker image")
    print("=" * 70)

    choice = input("\nSelect option (1-6): ").strip()

    if choice == "1":
        # Delegate to jeopardy with defaults
        cmd_jeopardy(argparse.Namespace(
            container=DOCKER_CONTAINER, image=DOCKER_IMAGE, ssh_port=SSH_PORT,
            model=DEFAULT_MODEL,
            challenge="Password Hunt",
            description="Find the hidden password file",
            flag_format=FLAG_FORMAT,
            max_iterations=MAX_ITERATIONS,
        ))
    elif choice == "2":
        runner.run_single_challenge()
    elif choice == "3":
        types = ["hidden_comment", "robots_txt", "source_code",
                 "base64_encoded", "hidden_form_field"]
        print("\nAvailable challenge types:")
        for i, t in enumerate(types, 1):
            print(f"  {i}. {t}")
        type_choice = input("Select type (1-5): ").strip()
        if type_choice.isdigit() and 1 <= int(type_choice) <= 5:
            runner.run_single_challenge(challenge_type=types[int(type_choice) - 1])
    elif choice == "4":
        count = input("How many challenges? (default 3): ").strip()
        runner.run_batch_challenges(count=int(count) if count.isdigit() else 3)
    elif choice == "5":
        count = input("How many websites? (default 5): ").strip()
        from ctf_agents.pcg.website_generator import CTFWebsiteGenerator
        CTFWebsiteGenerator(output_dir="./ctf_websites").generate_batch(
            count=int(count) if count.isdigit() else 5
        )
    elif choice == "6":
        runner.build_docker_image()
    else:
        print("Invalid option")


# ------------------------------------------------------------------
# Argument parser
# ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctf_agents",
        description="CTF AI Agents — AI-powered CTF challenge solver",
    )
    sub = parser.add_subparsers(dest="command")

    # -- jeopardy --
    jp = sub.add_parser("jeopardy", help="Run a Jeopardy-style CTF challenge")
    jp.add_argument("--challenge", default="Password Hunt", help="Challenge name")
    jp.add_argument("--description", default="Find the hidden password file",
                    help="Challenge description")
    jp.add_argument("--model", default=DEFAULT_MODEL,
                    help="LLM model identifier")
    jp.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS,
                    help="Max AI iterations")
    jp.add_argument("--container", default=DOCKER_CONTAINER,
                    help="Docker container name")
    jp.add_argument("--image", default=DOCKER_IMAGE,
                    help="Docker image name")
    jp.add_argument("--ssh-port", type=int, default=SSH_PORT,
                    help="SSH port mapping")
    jp.add_argument("--flag-format",
                    default=FLAG_FORMAT,
                    help="Flag regex pattern")

    # -- web --
    wb = sub.add_parser("web", help="Run web-based CTF challenge(s)")
    wb.add_argument("--challenge-type", default=None,
                    choices=["hidden_comment", "robots_txt", "source_code",
                             "base64_encoded", "hidden_form_field"],
                    help="Specific challenge type (random if omitted)")
    wb.add_argument("--batch", type=int, default=None,
                    help="Run N challenges in sequence")
    wb.add_argument("--model", default=DEFAULT_MODEL,
                    help="LLM model identifier")
    wb.add_argument("--max-iterations", type=int, default=25,
                    help="Max AI iterations per challenge")

    # -- multi --
    mt = sub.add_parser("multi", help="Run a multi-agent scenario on a shared container")
    mt.add_argument("--roster", default="attacker:2",
                    help="Roster spec, e.g. 'attacker:2' or 'attacker:2,defender:1'")
    mt.add_argument("--scenario-name", default="ad-hoc",
                    help="Scenario name (used in reports)")
    mt.add_argument("--challenge-type", default=None,
                    choices=["hidden_comment", "robots_txt", "source_code",
                             "base64_encoded", "hidden_form_field"],
                    help="Specific challenge type (random if omitted)")
    mt.add_argument("--max-ticks", type=int, default=30,
                    help="Max coordinator ticks (one full round-robin per tick)")
    mt.add_argument("--model", default=DEFAULT_MODEL,
                    help="Default LLM model for agents (per-agent override via scenario spec)")
    mt.add_argument("--require-all", action="store_true",
                    help="Win only when every agent solves (default: first-to-flag wins)")

    # -- diamond-vault --
    dv = sub.add_parser("diamond-vault",
                        help="Run the Diamond Vault cooperative 2-agent scenario")
    dv.add_argument("--model", default=DEFAULT_MODEL,
                    help="LLM model for both agents")
    dv.add_argument("--max-ticks", type=int, default=30,
                    help="Max coordinator ticks")

    # -- docker-pivot --
    dp = sub.add_parser(
        "docker-pivot",
        help="Run the Docker Pivot single-agent lateral-movement scenario",
    )
    dp.add_argument("--model", default=DEFAULT_MODEL,
                    help="LLM model for the attacker agent")
    dp.add_argument("--max-iterations", type=int, default=MAX_ITERATIONS,
                    help="Max agent iterations before giving up")
    dp.add_argument("--keep-on-failure", action="store_true",
                    help="Leave containers/networks up on failure (for debugging)")

    # -- generate --
    gn = sub.add_parser("generate", help="Generate CTF websites (no agent)")
    gn.add_argument("--count", type=int, default=5,
                    help="Number of websites to generate")

    # -- build-docker --
    sub.add_parser("build-docker", help="Build Docker image for web challenges")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    handlers = {
        "jeopardy": cmd_jeopardy,
        "web": cmd_web,
        "multi": cmd_multi,
        "diamond-vault": cmd_diamond_vault,
        "docker-pivot": cmd_docker_pivot,
        "generate": cmd_generate,
        "build-docker": cmd_build_docker,
    }

    handler = handlers.get(args.command)
    if handler:
        handler(args)
    else:
        # No subcommand → interactive menu
        cmd_interactive(args)


if __name__ == "__main__":
    main()
