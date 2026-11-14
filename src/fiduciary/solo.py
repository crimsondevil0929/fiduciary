"""A deployment of one (``fiduciary init --solo``, 1.14.0), and how it grows.

A solo deployment has one principal, who adopted the charter and issued the mandate alone,
and whose key is in ``keys/``. It is a real deployment, with the same signed records, the
same checks on every plan and the same offline audit as one with three principals: the
difference is only who must agree before the authority changes.

Two changes take the owner's signature alone, so this module makes them:

- :func:`enforce`: the next version of the mandate, the same bounds without
  ``enforcement = "shadow"``. From then on a plan that breaks a bound is refused.
- :func:`add_principals`: a second version of the charter, naming the owner and two more
  principals, then the next version of the mandate, whose roster is the three with the
  quorums every other deployment uses (two of three to grant, one to take away). Keys are
  written for the new principals; give each theirs and delete it here.

After :func:`add_principals` neither applies: the mandate changes as any mandate does, by
a version that two of the three approve (``fiduciary mandate amend``).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fiduciary import fidu1
from fiduciary.exceptions import FiduciaryError

if TYPE_CHECKING:
    from agentgov.receipts.signing import Signer

    from fiduciary.bridge import Fiduciary
    from fiduciary.documents import Charter, Mandate
    from fiduciary.webauthn import PasskeySigner

__all__ = ["Grown", "add_principals", "enforce"]

QUORUMS = {
    "amend": 2,
    "revoke": 1,
    "reduce": 1,
    "grant": 2,
    "reset": 2,
    "reset_safety": 2,
    "release": 2,
    "requeue": 2,
    "compensate": 2,
    "compensate_late": 2,
    "accept_repair": 2,
}
"""The roster's quorums once there are three: what every demo deployment has."""


class SoloError(FiduciaryError):
    """The deployment is not one a solo command can change."""


@dataclass(frozen=True, slots=True)
class Grown:
    """What a solo command issued."""

    mandate_id: str
    version: int
    digest: str
    charter_version: int
    keys: tuple[Path, ...] = ()


def _approve(
    fid: Fiduciary,
    charter: Charter,
    principal: str,
    signer: Signer | PasskeySigner,
    subject: str,
    digest: str,
) -> None:
    from fiduciary.documents import Approval

    now = datetime.now(UTC)
    approval = Approval.from_json(
        {
            "v": "FIDU1",
            "type": "approval",
            "approval_id": str(uuid.uuid4()),
            "charter": {"charter_id": charter.charter_id, "version": charter.version},
            "principal": principal,
            "subject": {"type": subject, "digest": digest},
            "resolution": None,
            "decision": "approve",
            "issued_at": fidu1.format_instant(now),
            "not_after": fidu1.format_instant(now + timedelta(hours=1)),
        },
        signed=False,
    ).signed(signer)
    fid.log.store.submit(approval.canonical())


def _solo(
    fid: Fiduciary, root: Path, mandate: str | None, passkey: PasskeySigner | None = None
) -> tuple[Charter, Mandate, Signer | PasskeySigner]:
    """The charter in force, the lineage's head, and the owner's key, when the
    deployment is solo: the key file in ``keys/``, or (1.17.0) the owner's passkey,
    asked in the browser unless ``passkey`` is given."""
    from fiduciary.passkeys import PasskeyError, signer_for

    state = fid.log.state()
    authority = fid._authority
    charter = authority.decide.charter(state)
    if len(charter.principals) != 1:
        raise SoloError(
            f"the charter lists {len(charter.principals)} principals: a change takes their "
            f"approvals (fiduciary mandate amend), not one key"
        )
    owner = charter.principals[0].name
    try:
        signer = signer_for(fid, root, owner, passkey=passkey)
    except PasskeyError as exc:
        raise SoloError(str(exc)) from exc
    if mandate is None:
        live = sorted(m for m, e in fid.binder.engines().items() if not e.closed)
        if len(live) != 1:
            raise SoloError(f"{len(live)} lineages are bound: name one")
        mandate = live[0]
    mandate_id = fid._lineage(state, mandate)
    return charter, authority.decide.head_mandate(state, mandate_id), signer


def _next(
    fid: Fiduciary,
    head: Mandate,
    charter: Charter,
    signer: Signer | PasskeySigner,
    approvers: Sequence[tuple[str, Signer | PasskeySigner]],
    **changes: Any,
) -> Grown:
    """Issue version n+1 of ``head``'s lineage with ``changes``, approved by ``approvers``,
    and rebind it."""
    from fiduciary.documents import Mandate

    body = head.body()
    body.update(
        {
            "version": head.version + 1,
            "prev": head.digest,
            "charter": {
                "charter_id": charter.charter_id,
                "version": charter.version,
                "digest": charter.digest,
            },
            "issued_at": fidu1.format_instant(datetime.now(UTC)),
            **changes,
        }
    )
    if changes.get("enforcement") is None:
        body.pop("enforcement", None)
    draft = Mandate.from_json(body, signed=False).signed(signer)
    fid._authority.propose(draft)
    for principal, key in approvers:
        _approve(fid, charter, principal, key, "mandate", draft.digest)
    fid.binder.issue(draft.digest)
    return Grown(draft.mandate_id, draft.version, draft.digest, charter.version)


def enforce(
    fid: Fiduciary,
    root: Path,
    *,
    mandate: str | None = None,
    passkey: PasskeySigner | None = None,
) -> Grown:
    """Issue the next version of a solo deployment's shadow mandate, enforced.

    :raises SoloError: When the deployment is not solo, or its mandate already enforces.
    """
    charter, head, signer = _solo(fid, root, mandate, passkey)
    if not head.shadow:
        raise SoloError(f"mandate {head.mandate_id} version {head.version} already enforces")
    owner = charter.principals[0].name
    return _next(fid, head, charter, signer, [(owner, signer)], enforcement=None)


def add_principals(
    fid: Fiduciary,
    root: Path,
    names: Sequence[str],
    *,
    mandate: str | None = None,
    enforce: bool = False,
    passkey: PasskeySigner | None = None,
) -> Grown:
    """Grow a solo deployment to three principals, two of whom must agree to grant.

    :param names: The two principals beside the owner. A key is written for each in
        ``keys/``.
    :param enforce: Issue the new version enforced, if the mandate was in shadow.
    :raises SoloError: When the deployment is not solo, or the names are not two new ones.
    """
    from fiduciary.bundle.export import spec as key_spec
    from fiduciary.documents import Charter
    from fiduciary.keys import generate_key

    charter, head, signer = _solo(fid, root, mandate, passkey)
    owner = charter.principals[0].name
    names = list(names)
    if len(names) != 2 or len(set(names)) != 2 or owner in names:
        raise SoloError(f"name two principals beside {owner}, each once")
    keys = []
    signers: dict[str, Any] = {owner: signer}
    for name in names:
        path = root / "keys" / f"{name}.key"
        if path.exists():
            raise SoloError(f"{path} exists: a key is never overwritten")
        signers[name] = generate_key(path)
        keys.append(path)
    grown = Charter.from_json(
        {
            "v": "FIDU1",
            "type": "charter",
            "charter_id": charter.charter_id,
            "version": charter.version + 1,
            "prev": charter.digest,
            "deployment": charter.deployment.to_json(),
            "principals": [
                # The owner keeps its key as the charter holds it: a passkey keeps its policy.
                charter.principals[0].to_json()
                if n == owner
                else {"name": n, "key": key_spec(signers[n].public_key())}
                for n in sorted(signers)
            ],
            "quorums": {"charter": 3, "issue": 2, "revoke": 1},
            "break_glass": list(charter.break_glass),
            "issued_at": fidu1.format_instant(datetime.now(UTC)),
        },
        signed=False,
    ).signed(signer)
    authority = fid._authority
    authority.propose(grown)
    # Version n+1 of a charter is adopted by the quorum of version n: the owner alone.
    _approve(fid, charter, owner, signer, "charter", grown.digest)
    authority.adopt_charter(grown.digest)
    (root / f"charter-v{grown.version}.json").write_text(grown.pretty())
    changes: dict[str, Any] = {
        "roster": {"principals": sorted(signers), "quorums": dict(QUORUMS)},
    }
    if enforce or not head.shadow:
        changes["enforcement"] = None
    else:
        changes["enforcement"] = "shadow"
    # The new version is approved by the previous version's roster: the owner alone.
    issued = _next(fid, head, grown, signer, [(owner, signer)], **changes)
    return Grown(issued.mandate_id, issued.version, issued.digest, grown.version, tuple(keys))
