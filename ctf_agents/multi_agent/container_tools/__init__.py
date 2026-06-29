"""Container-side helpers installed inside each agent's container.

These files are copied into ``/usr/local/bin`` by the per-agent Dockerfile so
that the agent (an LLM driving a shell over SSH) can call them like any other
binary.  None of this code runs in the orchestrator process — they live in
the container, alongside the SSH daemon.

Three pieces:

* ``tcp_listener.py`` — Python daemon that binds ``0.0.0.0:7000`` and writes
  every received JSON envelope to ``/var/spool/agent-inbox/messages.jsonl``
  (consumed by ``agent-inbox``) and to ``/var/log/agent-tcp.log`` (tailed by
  the orchestrator's ``TcpTransport`` for metrics).
* ``agent-send`` — CLI used by the LLM to push a message to a peer.
  Resolves the peer-id via ``/etc/agent-peers``, opens a TCP connection,
  writes a length-prefixed JSON frame, exits 0 on ack.
* ``agent-inbox`` — CLI used by the LLM to read incoming messages, with a
  persistent cursor so the agent can ask for "what's new since last time".

Wire format (one frame):

    4-byte big-endian length || JSON object UTF-8 bytes

JSON object shape::

    {
        "from": "agent-a",
        "to":   "agent-b",
        "ts":   "2026-05-23T12:34:56.789Z",
        "body": "alpha7f3a91e25c4d"
    }
"""
