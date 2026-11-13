"""Claude Code on a repository a mandate governs (``fiduciary init --claude-code``, 1.15.0).

A coding agent works for a whole session with its own Edit and Bash tools. Interlock 1.7.0
gives that session a stage of its own, a Git worktree, and promotes what it changed as one plan
(:mod:`interlock.session`). Here the plan is judged by the deployment's signed mandate and
recorded on its lineage's chain, like any other plan the agent makes:

- ``fiduciary hook claude-code`` answers Claude Code's hooks (:mod:`interlock.hooks`). Its
  ``WorktreeCreate`` opens a session, and Claude Code works in its worktree; ``PreToolUse``
  refuses an edit outside it; ``Stop`` checks the session against the mandate and, when it
  would be refused, keeps the agent working with the reason; ``SessionEnd`` and
  ``WorktreeRemove`` promote it. The receipt of each promotion is signed with the
  deployment's receipts key.
- :func:`install` writes the hooks and Claude Code's sandbox into the repository's
  ``.claude/settings.local.json``: its commands write only in the working directory (the
  worktree) and reach no network, and the deployment's keys are hidden from them.

Nothing rewrites a tool call: the worktree is the directory Claude Code works in.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, TextIO

__all__ = ["hook", "install", "repository_of"]

SETTINGS = Path(".claude") / "settings.local.json"
EXCLUDED = (".fiduciary/", ".claude/settings.local.json")


def repository_of(directory: Path) -> tuple[Path, str, Path]:
    """The Git repository ``directory`` is in: its top, its current branch, and its common
    Git directory.

    :raises ValueError: When it is not in a repository, or HEAD is not on a branch.
    """

    def git(*args: str) -> str:
        done = subprocess.run(  # noqa: S603 - an argument list, never a shell
            ["git", *args],  # noqa: S607 - the user's own git
            cwd=directory,
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            raise ValueError(
                f"{directory.resolve()} is not in a Git repository on a branch: "
                + done.stderr.strip().removeprefix("fatal: ")
            )
        return done.stdout.strip()

    top = Path(git("rev-parse", "--show-toplevel"))
    branch = git("symbolic-ref", "--short", "HEAD")
    common = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir"))
    return top, branch, common


def hooks_command(config: Path) -> str:
    """The command every hook runs: this Python's fiduciary, on this deployment."""
    return (
        f"{shlex.quote(sys.executable)} -m fiduciary --config {shlex.quote(str(config))} "
        f"hook claude-code"
    )


def install(repository: Path, deployment: Path, common: Path) -> list[dict[str, str]]:
    """Write the hooks and the sandbox into ``repository``'s Claude Code settings, and keep
    the deployment and those settings out of Git; what was changed, and why.

    An existing ``settings.local.json`` keeps everything but its ``hooks`` and ``sandbox``.
    """
    from interlock.hooks import settings as hook_settings

    config = (deployment / "fiduciary.toml").resolve()
    path = repository / SETTINGS
    current: dict[str, Any] = {}
    if path.is_file():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ValueError(f"{path} is not JSON: {exc}") from exc
    current["hooks"] = hook_settings(hooks_command(config))
    current["sandbox"] = {
        "enabled": True,
        "failIfUnavailable": True,
        "allowUnsandboxedCommands": False,
        "network": {"allowedDomains": [], "strictAllowlist": True},
        "filesystem": {
            # git add and git commit in the worktree write its index and objects here; the
            # branches are elsewhere, so the agent's commits move no branch.
            "allowWrite": [str(common / "worktrees"), str(common / "objects")],
            "denyRead": [str((deployment / "keys").resolve())],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    exclude = common / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    lines = exclude.read_text(encoding="utf-8").splitlines() if exclude.is_file() else []
    added = [e for e in EXCLUDED if e not in lines]
    if added:
        with exclude.open("a", encoding="utf-8") as handle:
            handle.write("".join(f"{e}\n" for e in added))
    return [
        {
            "name": str(SETTINGS),
            "why": "Claude Code's hooks open, check and promote a staged session, and its "
            "sandbox lets a command write only in the session and reach no network",
        },
        {
            "name": str(exclude.relative_to(common.parent)) if common.parent else str(exclude),
            "why": "the deployment and the local settings stay out of Git: " + ", ".join(EXCLUDED),
        },
    ]


def hook(payload: dict[str, Any], config: Path, out: TextIO, err: TextIO) -> int:
    """Answer one Claude Code hook for the deployment at ``config``. The deployment is opened
    only by the events that promote; an edit's check reads the session's record alone."""
    from interlock.hooks import HookContext, claude_code
    from interlock.session import Promotion, Session, sessions_of
    from interlock.wiring import open_substrate

    from fiduciary.bridge import Fiduciary
    from fiduciary.config import load_config
    from fiduciary.exceptions import FiduciaryError

    settings = load_config(config)
    interlock = settings.interlock_config()
    opened: dict[str, Any] = {}

    def engine() -> Any:
        if "engine" not in opened:
            fid = Fiduciary.from_config(config)
            opened["fid"] = fid
            live = sorted(m for m, e in fid.binder.engines().items() if not e.closed)
            if len(live) != 1:
                raise FiduciaryError(f"{len(live)} lineages are bound here: one governs a session")
            opened["engine"] = fid.binder.engine(live[0])
        return opened["engine"]

    def promote(session: Session, check: bool) -> Promotion:
        signer = None if check else settings.receipts()
        found: Promotion = engine().promote(session, check=check, signer=signer)
        return found

    context = HookContext(
        substrate=lambda: sessions_of(open_substrate(interlock)),
        promote=promote,
        lifetime=interlock.sessions.lifetime_hours * 3600,
        target=interlock.git.branch if interlock.git is not None else "the workspace",
    )
    try:
        return claude_code(payload, context, out, err)
    except FiduciaryError as exc:
        print(f"fiduciary: {exc}", file=err)
        return 1
    finally:
        if "fid" in opened:
            opened["fid"].close()
