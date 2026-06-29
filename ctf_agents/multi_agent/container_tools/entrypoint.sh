#!/bin/bash
# Container entrypoint for the per-agent Diamond Vault image (and any
# future scenario that uses the same image layout).
#
# Boots two things:
#   1. tcp_listener.py — runs as sandboxuser in the background so it can
#      write to /var/spool/agent-inbox and /var/log/agent-tcp.log without
#      sudo. If TCP transport isn't in use this just sits idle; no harm.
#   2. sshd in the foreground (PID 1) — the orchestrator's SSHTerminal
#      attaches here to drive the agent's LLM commands.
#
# Container env vars are NOT inherited by ssh exec_command sessions; the
# Dockerfile bakes AGENT_ID into /etc/agent-id so agent-send can find it.
set -e

mkdir -p /var/spool/agent-inbox
chown sandboxuser:sandboxuser /var/spool/agent-inbox
touch /var/log/agent-tcp.log
chown sandboxuser:sandboxuser /var/log/agent-tcp.log
chmod 0644 /var/log/agent-tcp.log

# Background listener. Output captured for debugging; the listener itself
# is silent on the happy path.
runuser -u sandboxuser -- /usr/local/bin/tcp_listener.py \
    >/var/log/tcp_listener.out 2>&1 &

# SSH daemon in foreground keeps the container alive.
exec /usr/sbin/sshd -D
