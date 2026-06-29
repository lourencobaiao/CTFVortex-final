"""Integration test for the in-container messaging tools.

Boots a real ``tcp_listener.py`` subprocess against a tmpdir + free port,
fires ``agent-send`` (also as a subprocess) against it, then validates
``agent-inbox`` reads back what was sent. Pure stdlib; no Docker, no SSH.

Run with::

    python tests/test_container_tools.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "ctf_agents" / "multi_agent" / "container_tools"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_listen(port: int, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _start_listener(port: int, inbox_dir: Path, audit_log: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        "AGENT_LISTENER_HOST": "127.0.0.1",
        "AGENT_LISTENER_PORT": str(port),
        "AGENT_INBOX_DIR": str(inbox_dir),
        "AGENT_AUDIT_LOG": str(audit_log),
        "AGENT_ID": "agent-b",
    })
    return subprocess.Popen(
        [sys.executable, str(TOOLS_DIR / "tcp_listener.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run_send(peers_file: Path, agent_id: str, peer: str, body: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({
        "AGENT_PEERS_FILE": str(peers_file),
        "AGENT_ID": agent_id,
    })
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR / "agent-send"), peer, body],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_inbox(inbox_dir: Path, *flags: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.update({"AGENT_INBOX_DIR": str(inbox_dir)})
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR / "agent-inbox"), *flags],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
    )


def _assert(cond: bool, msg: str, errors: List[str]) -> None:
    if not cond:
        errors.append(msg)


def main() -> int:
    errors: List[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        inbox_dir = tmp / "spool"
        audit_log = tmp / "agent-tcp.log"
        peers_file = tmp / "peers"

        port = _free_port()
        peers_file.write_text(f"agent-b 127.0.0.1:{port}\n", encoding="utf-8")

        proc = _start_listener(port, inbox_dir, audit_log)
        try:
            if not _wait_for_listen(port):
                errors.append(f"listener did not bind to :{port} in time")
                return _finish(errors, proc)

            # 1. send a message → ack expected, exit 0
            r1 = _run_send(peers_file, "agent-a", "agent-b", "alpha7f3a91e25c4d")
            _assert(r1.returncode == 0,
                    f"agent-send exit={r1.returncode} stderr={r1.stderr!r}", errors)

            # Give the listener a moment to flush the log line.
            time.sleep(0.2)

            # 2. audit log should have one JSON line
            audit = audit_log.read_text(encoding="utf-8").strip().splitlines()
            _assert(len(audit) == 1, f"audit log lines={len(audit)} (expected 1)", errors)
            _assert("alpha7f3a91e25c4d" in audit[0] if audit else False,
                    f"audit line missing body: {audit!r}", errors)
            _assert('"from": "agent-a"' in audit[0] if audit else False,
                    f"audit line missing sender: {audit!r}", errors)

            # 3. agent-inbox --json returns the envelope
            r2 = _run_inbox(inbox_dir, "--json")
            _assert(r2.returncode == 0,
                    f"agent-inbox --json exit={r2.returncode} stderr={r2.stderr!r}", errors)
            _assert("alpha7f3a91e25c4d" in r2.stdout,
                    f"agent-inbox --json missing body: {r2.stdout!r}", errors)

            # 4. agent-inbox --latest --raw returns just the body
            r3 = _run_inbox(inbox_dir, "--latest", "--raw")
            _assert(r3.returncode == 0,
                    f"agent-inbox --latest --raw exit={r3.returncode}", errors)
            _assert(r3.stdout.strip() == "alpha7f3a91e25c4d",
                    f"--latest --raw stdout={r3.stdout!r}", errors)

            # 5. --since-last cursor: first call shows the message, second is empty.
            r4 = _run_inbox(inbox_dir, "--since-last")
            _assert("alpha7f3a91e25c4d" in r4.stdout,
                    f"first --since-last missing body: {r4.stdout!r}", errors)
            r5 = _run_inbox(inbox_dir, "--since-last")
            _assert("alpha7f3a91e25c4d" not in r5.stdout,
                    f"second --since-last should be empty, got: {r5.stdout!r}", errors)

            # 6. unknown peer → exit 1
            r6 = _run_send(peers_file, "agent-a", "no-such-agent", "x")
            _assert(r6.returncode == 1,
                    f"unknown peer exit={r6.returncode} (expected 1)", errors)

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    return _finish(errors, None)


def _finish(errors: List[str], proc) -> int:
    if errors:
        print("\n=== FAIL ===")
        for e in errors:
            print(f"  - {e}")
        if proc is not None:
            out, err = proc.communicate(timeout=1) if proc.poll() is None else (b"", b"")
            if out:
                print(f"\nlistener stdout:\n{out.decode(errors='replace')}")
            if err:
                print(f"\nlistener stderr:\n{err.decode(errors='replace')}")
        return 1
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
