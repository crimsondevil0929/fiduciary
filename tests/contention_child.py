"""A service process for the contention test (§11 Phase 3).

Run as ``python -m tests.contention_child <manifest> <job>``: it records
the intake, and executes every formed warrant that has no intent yet, over
and over, while another process does the same. It stops once the job's
tickets are all warranted and executed, and every submission is answered.
It then writes how many executions it ran to the job's ``out`` file.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fiduciary.exceptions import AuthorityError
from fiduciary.executor import ExecutionError
from tests.crash_env import Env, Manifest


def main(manifest_path: str, job_path: str) -> int:
    job = json.loads(Path(job_path).read_text())
    env = Env(Manifest.load(Path(manifest_path)), engines=False)
    executed: list[str] = []
    deadline = time.monotonic() + float(job["timeout"])
    try:
        while time.monotonic() < deadline:
            env.escalation.process_intake()
            state = env.log.state()
            for digest in sorted(state.warrants, reverse=bool(job["reverse"])):
                if state.execution_status(digest) is None:
                    try:
                        env.executor.execute(digest)
                    except (AuthorityError, ExecutionError):
                        continue  # the other process got there first, or is there now
                    executed.append(digest)
            state = env.log.state()
            settled = (
                Path(job["submitted"]).exists()
                and {i for i, _ in env.store.intake()} <= state.processed
                and len(state.warrants) >= int(job["tickets"])
                and all(state.execution_status(d) not in (None, "intent") for d in state.warrants)
            )
            if settled:
                break
            time.sleep(0.02)
        else:
            return 4
    finally:
        Path(job["out"]).write_text(json.dumps(executed))
        env.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
