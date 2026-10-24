"""What the installed interlock and AgentGov provide beyond the releases
fiduciary requires (interlock 1.0.2, AgentGov 1.0.0): the upstream gaps of
``docs/FIDUCIARY_SPEC.md`` §12, which interlock 1.0.3 and AgentGov 1.0.1
close. Each is read from the installed code itself, so fiduciary uses it where
it is present, and holds to the fallback §12 states where it is not.
"""

from __future__ import annotations

import inspect
from typing import Final

from agentgov.core import BudgetManager
from interlock import records
from interlock.extension import BudgetGuard
from interlock.operators import Operator
from interlock.recovery import RecoveryRuntime

__all__ = ["EXPECTED_HEADS", "ILOK1_DIGESTS", "QUOTES_ADOPTED", "RECOVERY_REOPENS", "RESET_MEMO"]

ILOK1_DIGESTS: Final = hasattr(records, "SIGNATURE_VERSIONS")
"""U-2: an ILOK1 record may be signed at signature version 2, a digest, so a
key service that limits what it signs (AWS KMS) signs any record."""

QUOTES_ADOPTED: Final = "adopt" in inspect.signature(BudgetGuard).parameters
"""U-3: a budget guard adopts the open quotes of the log it is opened over, so
a restart loses no extension quote."""

EXPECTED_HEADS: Final = "expected_heads" in inspect.signature(Operator.release).parameters
"""U-4: an operator acts only at the delivery-log heads it is given, and
refuses before signing anything when a head has moved."""

RESET_MEMO: Final = "memo" in inspect.signature(BudgetManager.reset).parameters
"""U-5: a reset entry's memo names what authorized it."""

RECOVERY_REOPENS: Final = "reopen" in inspect.signature(RecoveryRuntime).parameters
"""U-6: a recovery that was closed opens again on a fresh reserve, so a
lineage recovers each time it halts, not once."""
