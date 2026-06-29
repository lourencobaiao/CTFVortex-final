#!/usr/bin/env python3
"""Listener daemon — accepts framed JSON messages from peer agents.

Runs as a background process inside each agent's container, started by the
Dockerfile's entrypoint script. Single-threaded, one connection at a time,
which is plenty for round-robin coordination where at most one message lands
per tick.

Wire format (per connection, exactly one frame):

    4-byte big-endian length  ||  JSON envelope (UTF-8)

Envelope schema::

    {"from": str, "to": str, "ts": str, "body": str}

Side effects on receipt:

* append the raw envelope as one line to
  ``/var/spool/agent-inbox/messages.jsonl`` (consumed by the ``agent-inbox``
  CLI on the next read)
* append the same envelope as one line to ``/var/log/agent-tcp.log``
  (tailed by the orchestrator's ``TcpTransport`` for metrics + audit)
* reply with the single byte ``b"K"`` (ack) and close the connection

Errors (truncated frames, bad JSON, missing fields) are logged to stderr
and the connection is closed without acking; the sender will see EOF and
``agent-send`` will exit non-zero.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import time
from pathlib import Path


# All paths/ports overridable via env so the same script powers both the
# in-container daemon (production defaults) and the local subprocess tests
# (paths under a tmpdir, alternate ports).
LISTEN_HOST = os.environ.get("AGENT_LISTENER_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("AGENT_LISTENER_PORT", "7000"))
INBOX_DIR = Path(os.environ.get("AGENT_INBOX_DIR", "/var/spool/agent-inbox"))
INBOX_LOG = INBOX_DIR / "messages.jsonl"
AUDIT_LOG = Path(os.environ.get("AGENT_AUDIT_LOG", "/var/log/agent-tcp.log"))
MAX_FRAME_BYTES = 64 * 1024  # generous upper bound; protocol caps body at ~200


def _ensure_dirs() -> None:
    INBOX_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Touch both files so cursors / tails work from tick zero.
    INBOX_LOG.touch(exist_ok=True)
    AUDIT_LOG.touch(exist_ok=True)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``conn`` or raise EOFError."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise EOFError(f"connection closed after {len(buf)}/{n} bytes")
        buf.extend(chunk)
    return bytes(buf)


def _handle_one(conn: socket.socket, peer: str) -> None:
    header = _recv_exact(conn, 4)
    (length,) = struct.unpack("!I", header)
    if length <= 0 or length > MAX_FRAME_BYTES:
        raise ValueError(f"frame length {length} out of bounds")
    payload = _recv_exact(conn, length).decode("utf-8")

    envelope = json.loads(payload)
    for required in ("from", "to", "body"):
        if required not in envelope:
            raise ValueError(f"missing field {required!r} in envelope")
    # Stamp a server-side receipt timestamp; preserve sender's ts if present.
    envelope.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    line = json.dumps(envelope, ensure_ascii=False) + "\n"

    with INBOX_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(line)

    conn.sendall(b"K")


def serve_forever() -> None:
    _ensure_dirs()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((LISTEN_HOST, LISTEN_PORT))
        srv.listen(8)
        print(
            f"[tcp_listener] listening on {LISTEN_HOST}:{LISTEN_PORT} "
            f"(agent_id={os.environ.get('AGENT_ID', '?')})",
            flush=True,
        )
        while True:
            conn, addr = srv.accept()
            peer = f"{addr[0]}:{addr[1]}"
            try:
                _handle_one(conn, peer)
            except (EOFError, ValueError, json.JSONDecodeError, OSError) as exc:
                print(
                    f"[tcp_listener] dropping connection from {peer}: {exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
            finally:
                try:
                    conn.close()
                except OSError:
                    pass


if __name__ == "__main__":
    try:
        serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)
