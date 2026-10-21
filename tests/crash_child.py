"""The service as a child process, killed at one checkpoint (§10.4).

Run as ``python -m tests.crash_child <manifest> <job>``. It opens the
deployment the manifest names, raises one ticket of the job's class,
submits the principals' approvals, records them, and executes the warrant.
At the checkpoint the job names, it kills itself with ``SIGKILL``: no
``finally`` runs, no buffer is flushed, nothing is closed. Exit status 3
means the checkpoint was never reached.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from tests.crash_env import Env, Manifest, raise_ticket, submit_approvals, warrant_of


def main(manifest_path: str, job_path: str) -> int:
    job = json.loads(Path(job_path).read_text())
    kill = str(job["kill"])

    def checkpoint(point: str) -> None:
        if point == kill:
            os.kill(os.getpid(), signal.SIGKILL)

    env = Env(Manifest.load(Path(manifest_path)), checkpoint)
    ticket = raise_ticket(env, job["action"], job["n"], job["prepared"])
    warrant = warrant_of(env, ticket)
    if warrant is None:
        submit_approvals(env, ticket, job["action"])
        env.escalation.process_intake()
        warrant = warrant_of(env, ticket)
    assert warrant is not None
    env.executor.execute(warrant)
    env.close()
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
