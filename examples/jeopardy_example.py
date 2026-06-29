"""Example: Run a Jeopardy-style CTF challenge with automatic Docker reset."""

from ctf_agents.infra.docker_manager import run_ctf_with_reset
from ctf_agents.core.jeopardy_agent import JeopardyCTFAgent
from ctf_agents.reporting.engine import save_report
from ctf_agents.config import (
    OPENROUTER_API_KEY, DEFAULT_MODEL, SSH_HOST, SSH_PORT, SSH_USER, SSH_PASS,
    DOCKER_CONTAINER, DOCKER_IMAGE, MAX_ITERATIONS, FLAG_FORMAT,
)

DOCKER_CONFIG = {
    "container_name": DOCKER_CONTAINER,
    "image_name": DOCKER_IMAGE,
    "ssh_port": SSH_PORT,
    "dockerfile_path": "./Dockerfile",
}

CTF_CONFIG = {
    "api_key": OPENROUTER_API_KEY,
    "model": DEFAULT_MODEL,
    "challenge_name": "Password Hunt",
    "challenge_description": "Find the hidden password file",
    "flag_format": FLAG_FORMAT,
    "ssh_host": SSH_HOST,
    "ssh_port": SSH_PORT,
    "ssh_user": SSH_USER,
    "ssh_pass": SSH_PASS,
    "max_iterations": MAX_ITERATIONS,
}

if __name__ == "__main__":
    result = run_ctf_with_reset(
        agent_class=JeopardyCTFAgent,
        config=CTF_CONFIG,
        docker_config=DOCKER_CONFIG,
        reset_before=True,
        reset_after=True,
    )

    if result:
        save_report(result, prefix="ctf_report", output_dir="./reports")
