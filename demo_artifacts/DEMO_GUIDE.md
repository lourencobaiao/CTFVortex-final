# Docker Pivot Demo — Live Walkthrough Guide

This is a short script for showing the working Docker Pivot scenario.
Total time: ~3 minutes once Docker is up.

## Pre-flight (do once, before the director arrives)

```bash
# Docker Desktop running?
docker info >/dev/null && echo "Docker OK"

# Keys loaded?
set -a; source .env; set +a

# Mock test passes deterministically (proves framework correctness):
python3 tests/test_docker_pivot_mock.py
# Expect: === PASS ===, 4 iterations, flag found
```

## The live demo (what the director sees)

### 1. Show the topology before deploying

```bash
# Nothing is deployed yet
docker ps --filter "name=ctf-"
docker network ls --filter "name=ctf-"
```

Empty. Then explain the topology you're about to spin up:

> "Two containers, two isolated Docker subnets. The DMZ host gets a host
>  port mapping so I can SSH into it. The internal host is on a private
>  subnet with no host-side mapping — unreachable from this Mac. The DMZ
>  host is dual-homed: it has interfaces on both networks, which is what
>  makes the pivot possible."

### 2. Run the live demo

```bash
python3 -m ctf_agents docker-pivot --max-iterations 10
```

Expected: the script prints 9 steps, then the agent's iterations.
Solves in 5 iterations / ~8 seconds. Reference output:
`demo_artifacts/SUCCESSFUL_RUN.log`.

The interesting iterations (point these out):

| # | Agent command | What it demonstrates |
|---|---|---|
| 1 | `cat /etc/internal-host.conf` → `host=10.99.0.20` | Recon — discovers internal IP |
| 2 | `cat /etc/hosts` → `10.99.0.20 internal-host` | Confirms via second source |
| 3 | `ls /var/backups` | Looks where creds typically hide |
| 4 | `cat /var/backups/.creds` → `intern:Spr1ng2024!` | Credential discovery |
| 5 | `sshpass -p 'Spr1ng2024!' ssh ... intern@10.99.0.20 cat /root/flag.txt` → `flag{pivot_intern4l_pwn3d}` | **The pivot** — real SSH across network segments returns the flag |

### 3. Prove the network is real (if he asks)

Run with `--keep-on-failure` so containers stay up, then:

```bash
# Real Linux bridges:
docker network inspect ctf-internal-net | grep -A2 IPAM

# DMZ has TWO real network interfaces (dual-homed):
docker exec ctf-dmz ip -br addr
# eth0: 10.88.0.10/24, eth1: 10.99.0.10/24

# Real routing table inside the container:
docker exec ctf-dmz ip route
# 10.99.0.0/24 dev eth1 — that's how the pivot reaches internal

# The isolation IS real — from the Mac, internal is unreachable:
nc -zv 10.99.0.20 22
# → "Operation timed out": no route from the host machine
```

### 4. Cleanup

```bash
docker rm -f ctf-dmz ctf-internal
docker network rm ctf-dmz-net ctf-internal-net
```

(Or just re-run `docker-pivot` — it idempotently cleans stale state.)

## What to highlight if asked technical questions

- **"Is this really a network?"** — Yes: real Linux bridges (`brctl show`),
  real veth pairs, real network namespaces, real kernel routing. Same
  primitives Kubernetes uses for pod isolation. The Linux kernel doesn't
  know any of it is "fake."
- **"Why not GNS3?"** — Setup time (GNS3 VM under VMware on macOS is
  2-3 hours), no agent-visible difference (same shell, same ssh, same
  packets), and `InfrastructureProvider` keeps GNS3 as a future plug-in
  (~one new class).
- **"Can it do multi-agent next?"** — Yes — the scenario module is
  decoupled from the orchestrator. A v2 builder produces a roster of 2+
  agents on the same topology with `<MSG>` coordination, reusing Diamond
  Vault's substrate.

## Artifacts pinned for review

- `demo_artifacts/SUCCESSFUL_RUN.log` — full transcript of the working
  run (commands, outputs, timing).
- `demo_artifacts/SUCCESSFUL_RUN_report.json` — machine-readable report
  with discovered flags, attempt counts, success/failure breakdown.
- `tests/test_docker_pivot_mock.py` — deterministic substrate test,
  passes in <1s, proves framework correctness independent of LLM.
