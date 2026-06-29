# VORTEX-CoLab

**Dynamic Capture-the-Flag Challenge Generation for Adversarial AI Security Tasks.**

A sandbox framework for autonomous, LLM-driven cybersecurity agents that solve
CTF-style and penetration-testing challenges inside isolated Docker environments.
It provides agent logic, environment orchestration, procedural challenge
generation, scoring/reporting, and a multi-agent coordination substrate
(mailbox / event-bus, shared state, cooperative scenarios such as **Diamond Vault**).

> Internship project — Departamento de Ciência de Computadores, Faculdade de
> Ciências da Universidade do Porto (Estágio/Projeto, 2025/26).

---

## Features

- **LLM agent abstraction** with a Template-Method run loop (`start → step → stop`).
- **Multi-provider LLM backend**: Groq (primary) → OpenRouter (fallback), each with
  its own model-fallback chain to survive rate limits (HTTP 429) and removed models.
- **Gymnasium-style interaction loop**: the LLM is the policy, an SSH terminal is the
  environment, one shell command per step.
- **Procedural challenge generation (PCG)**: `CTFWebsiteGenerator` builds 5 families of
  web challenges (hidden comment, robots.txt, source code, base64, hidden form field)
  with random flags, difficulty and partial-credit markers.
- **Docker-based isolated environments** (Ubuntu 22.04 + sshd + nginx).
- **Multi-agent coordination**: round-robin tick coordinator, per-agent mailbox over an
  event bus, shared state, and a `<MSG>`/`<FINDING>` message protocol.
- **Two messaging transports** behind one interface: in-process mailbox (`inproc`) and a
  real **TCP** socket transport (`tcp`, default) using in-container `agent-send` /
  `agent-inbox` CLIs.
- **Cooperative scenario — Diamond Vault**: two agents in isolated containers must
  exchange keys to each open a `sudo`-gated vault; win condition `all_agents_solved`.
- **Network scenario — Docker Pivot**: single-agent DMZ → internal lateral movement.
- **Configurable scoring** (`ScoringEngine`) and **JSON reporting**.

## Repository layout

```
ctf_agents/
  core/            AI providers, SSH terminal, base + specialized agents, primitives, enums
  infra/           Docker lifecycle + infrastructure providers
  pcg/             Procedural CTF website generation (Faker)
  scoring/         Scoring engine + multi-agent metrics
  reporting/       JSON report build / print / save
  runners/         End-to-end web workflow (single + batch)
  orchestration/   Single-agent, tournament, multi-agent, docker-pivot orchestrators
  multi_agent/     Coordinator, mailbox, protocol, scenarios, transports, Diamond Vault
                   container_tools/  (tcp_listener.py, agent-send, agent-inbox, entrypoint.sh)
  scenarios/       Docker-pivot scenario + vuln planter
  config.py        Central, env-driven configuration
  __main__.py      Command-line interface
tests/             Deterministic mock tests (no Docker / no LLM needed)
Dockerfile*        Jeopardy, Web CTF, Diamond Vault, DMZ/Internal pivot images
```

## Requirements

- Python 3.10+
- [Docker](https://www.docker.com/) (running) — for any challenge that provisions a sandbox
- A free API key for **Groq** and/or **OpenRouter**

Install Python dependencies:

```bash
pip install -r requirements.txt
```

## Configuration

Copy the example env file and set at least one API key:

```bash
cp .env.example .env
# edit .env:
#   GROQ_API_KEY=...          # primary provider (higher free-tier RPM)
#   OPENROUTER_API_KEY=...    # fallback provider (broader model catalogue)
```

Optional overrides (see `ctf_agents/config.py`): `CTF_MODEL` (OpenRouter default
model), `CTF_GROQ_MODEL` (Groq default model), `PIVOT_DMZ_SSH_PORT`, subnets, etc.

> **Security:** never commit real keys. `.gitignore` already excludes `.env`, `*.env`
> and `sk-or-*`. If a key is ever exposed, revoke and rotate it immediately.

## Usage

The framework is driven through its CLI:

```bash
# Jeopardy-style filesystem/system challenge
python -m ctf_agents jeopardy --challenge "Password Hunt"

# Web challenge(s) — single or batch
python -m ctf_agents web
python -m ctf_agents web --batch 5

# Multi-agent run on a shared container
python -m ctf_agents multi --roster attacker:2 --max-ticks 30

# Diamond Vault cooperative scenario (two isolated agents)
python -m ctf_agents diamond-vault

# Docker pivot — DMZ → internal lateral movement
python -m ctf_agents docker-pivot

# Generate CTF websites only (no agent)
python -m ctf_agents generate --count 5

# Build the Docker image used by web challenges
python -m ctf_agents build-docker
```

Running `python -m ctf_agents` with no subcommand opens an interactive menu.
Use `--model <name>` on most subcommands to pick the LLM; names ending in
`:free`/`:paid` are routed to OpenRouter, others to Groq.

Reports are written as JSON to `./reports/`.

## Tests

The tests are deterministic mocks — they exercise the multi-agent substrate,
TCP transport, coordinator transcript, Diamond Vault cooperation, and the docker
pivot logic **without** requiring Docker or an LLM. Run them directly:

```bash
python tests/test_diamond_vault_mock.py
python tests/test_tcp_transport.py
python tests/test_coordinator_transcript.py
python tests/test_docker_pivot_mock.py
python tests/test_container_tools.py
```

Each prints its result and exits `0` on success.

## License

No license file is currently included. The code is the property of the host
institution; do **not** publish it publicly without your supervisor's
authorization (see the internship IP guidelines). If/when open-sourcing is
approved, add a `LICENSE` file (e.g. GPLv3 — see https://choosealicense.com/).
