"""``python -m tests.compiled_digests``: compile a mandate holding every
checker type, and a derived child, and print the compiled digests, the
checker records and whether this Interlock digests sets canonically (U-1)."""

from __future__ import annotations

import json

from fiduciary.compiler import canonical_digests, compile
from fiduciary.documents import Mandate
from tests import deploy
from tests import examples as e
from tests.test_compiler import full


def digests() -> dict[str, object]:
    mandate = Mandate.from_json(full(), signed=False)
    compiled = compile([mandate], deployment=deploy.deployment())
    child = compile([e.mandate(), e.child()], deployment=deploy.deployment())
    return {
        "digest": compiled.digest,
        "child": child.digest,
        "records": {r.name: r.config_hash for r in compiled.checker_records()},
        "classes": {c.name: type(c).__name__ for c in compiled.engine_checkers()},
        "canonical": canonical_digests(),
    }


if __name__ == "__main__":
    print(json.dumps(digests(), sort_keys=True))
