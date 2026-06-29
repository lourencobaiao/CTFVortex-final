"""Post-deploy artifact planting for the Docker Pivot scenario.

Called by ``DockerPivotOrchestrator`` after ``provider.deploy()`` returns
and before the agent connects. Uses ``docker exec`` rather than baking
artifacts into the image so the flag + credentials can vary per run and
stay out of git.

Kept as a free function in its own module so a future multi-agent pivot
scenario can reuse it unchanged.
"""

from __future__ import annotations

import shlex
import subprocess
from typing import TYPE_CHECKING

from ctf_agents.scenarios.docker_pivot import (
    DMZ_CREDS,
    FLAG,
    INTERNAL_CREDS,
    INTERNAL_IP,
)

if TYPE_CHECKING:  # pragma: no cover
    from ctf_agents.infra.base import Environment
    from ctf_agents.multi_agent.scenarios import Scenario


def plant_pivot_artifacts(env: "Environment", scenario: "Scenario") -> None:
    """Plant flag, credentials, and discovery breadcrumbs into the live env.

    Idempotent within a single deploy: re-running it would only overwrite
    files that already have the same values.
    """
    dmz_id = env.nodes["dmz"].node_id
    internal_id = env.nodes["internal"].node_id

    # DMZ side: planted discovery breadcrumbs the agent must find by
    # reconnaissance. Note `/var/backups/.creds` is intentionally mode
    # 0644 so the unprivileged `intern` user can read it — a planted
    # misconfig representative of a real "I forgot this was here" leak.
    _exec(dmz_id, f"sh -c {shlex.quote(f'echo host={INTERNAL_IP} > /etc/internal-host.conf')}")
    _exec(
        dmz_id,
        "sh -c " + shlex.quote(
            f"echo '{DMZ_CREDS[0]}:{DMZ_CREDS[1]}' > /var/backups/.creds && "
            f"chmod 0644 /var/backups/.creds"
        ),
    )
    _exec(
        dmz_id,
        "sh -c " + shlex.quote(
            f"grep -q internal-host /etc/hosts || "
            f"echo '{INTERNAL_IP} internal-host' >> /etc/hosts"
        ),
    )

    # Internal side: the trophy + matching credentials (the planted
    # reuse weakness). chpasswd applies the password to the `intern`
    # user already present in the image.
    #
    # Permission choice: the flag lives at /root/flag.txt (the realistic
    # "trophy file" location for an internal box), but we make it world-
    # readable AND make /root traversable. That models the "admin left
    # /root o+x and the file o+r so the support team could grep logs"
    # misconfiguration — a real pattern. The pivot is the actual puzzle;
    # once the agent is on the internal box, reading the file should be
    # a `cat` away.
    _exec(
        internal_id,
        "sh -c " + shlex.quote(
            f"echo '{FLAG}' > /root/flag.txt && "
            f"chmod 0644 /root/flag.txt && "
            f"chmod 0755 /root"
        ),
    )
    _exec(
        internal_id,
        "sh -c " + shlex.quote(
            f"echo '{INTERNAL_CREDS[0]}:{INTERNAL_CREDS[1]}' | chpasswd"
        ),
    )


def _exec(container: str, cmd: str) -> None:
    """Run ``docker exec <container> <cmd>`` and surface any failure.

    ``cmd`` is a single shell-ready string (typically ``sh -c '...'``);
    we hand it to docker as a single argv tail.
    """
    full = ["docker", "exec", container] + shlex.split(cmd)
    try:
        subprocess.run(full, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(
            f"docker exec failed on {container}: "
            f"{e.stderr.decode(errors='ignore').strip() or e}"
        ) from e
