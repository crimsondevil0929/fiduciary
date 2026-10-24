# The 1.0.0 mutation pass

`docs/FIDUCIARY_SPEC.md` §11, Phase 6: a mutation-testing pass over `fidu1`, `refines`,
`compiler`, quorum counting and `verify_bundle`, as interlock's epics ran theirs. Every
surviving mutant is killed, or explained.

## Method

Each mutant removes or weakens one mechanism, written by hand at one site of the source:
a check skipped, a comparison loosened by one, a set difference reversed, a field dropped
from what is hashed or signed. For each, the target's tests run against the mutated tree,
on SQLite. A mutant is **killed** when pytest exits 1 (a test failed) or hangs; it
**survives** when the tests pass. The runner:

- confirms, before anything is rewritten, that every site occurs exactly once;
- runs every target's tests on the unmutated tree first, and stops if one fails;
- restores each file from a copy, never from git, and drops its cached bytecode after
  every write and every restore (`PYTHONDONTWRITEBYTECODE=1`);
- counts any other exit as broken, not as a kill.

| Target | Source | Tests |
|---|---|---|
| fidu1 | `fidu1.py` | `test_fidu1.py`, `test_vectors.py`, `test_documents.py`, `test_signers.py` |
| refines | `refines.py` | `test_refines.py`, `test_monotonicity.py` |
| compiler | `compiler.py` | `test_compiler.py`, `test_binding.py`, `test_monotonicity.py`, `test_determinism.py` |
| quorum | `authority.py` (counting), `store.py` (decisions) | `test_escalation_quorum.py`, `test_authority.py`, `test_decide.py`, `test_tamper_escalation.py`, `test_binding.py` |
| verify | `bundle/verify.py`, `bundle/format.py` | `test_bundle_checks.py`, `test_bundle_vectors.py`, `test_bundle_tamper.py`, `test_bundle.py` |

## Result

109 mutants: 23 in fidu1, 28 in refines, 18 in the compiler, 13 in quorum counting, 27 in
the verifier. The first run killed 90 of 107 (A13 was added after, and V27 with the rule
for receipts issued before U-1). Of the 17 survivors,
15 were gaps in the tests, now closed: each is killed by a test added for it. Two were
code that could not matter, and is gone. The final run killed all 107 mutants whose code
remains.

| Survivor | Mechanism | Now |
|---|---|---|
| F03 | `key_of` matched a key's prefix: `ed25519:<64 hex>` and more was decoded | killed: a key with trailing hex is refused (`test_fidu1.py`) |
| F13 | `check_integer` without its maximum | killed: an integer past 2**53 − 1, or a field's maximum, is refused |
| R08 | `kept`'s verbatim comparison, where no comparison is given | **removed.** Every such rule's identity is the whole rule, so a rule kept and changed is one dropped: the branch could not be reached |
| C12 | a sink's `max_attempts` narrowed to the mandate's | killed: a mandate tighter than the installed sink compiles to its own bound |
| C14 | the compiled digest over the whole description | killed: the digest is the description's SHA-256, and a checker's bound changes it |
| A01 | the decision row's resolution, checked before the approval's | **removed.** The approval document's own resolution is checked below it: one copy of the same check |
| A04, A05 | an approval recorded after the ticket's deadline, or its own, counted | killed: the counting rule, on a state that records each late against one deadline only, beside one in time. The intake refuses both before they are recorded, so only a log written around the service reaches this: defense in depth, now tested |
| A08 | a revocation one approval short | killed: `test_binding.py` joined the target, whose revocation test refuses it |
| A13 | a warrant formed one approval short | killed at once (added on finding A08 mislabeled) |
| V03 | a bundle member the manifest does not list | killed: an extra member is named (V1) |
| V04 | a document under another digest's name | killed: a mandate moved and re-listed is named (V1) |
| V08 | a pinned charter of another ledger than the bundle's | killed (V2) |
| V10 | a version bound as another compiled policy | killed: a deployment whose sinks are tighter compiles it otherwise (V4) |
| V16 | a reset anchored by a warrant that is not the lineage's | killed (V7) |
| V17 | a session's memo outside its life | killed: a hold naming a closed session (V7) |
| V20 | an operator act on other messages than its warrant names | killed: a release that acted on one of two (V8) |
| V26 | a grant other than the warrant's | killed: the guard granting less than the warrant names (V8) |
| V27 | the allowance for receipts issued before U-1, extended past the first canonical one | killed at once (added with the rule, F6-8) |

The runner (`mutate_phase6.py`) and the per-mutant results are kept with the working notes,
not in the repository, as interlock's are.

## The second pass: what moved, and what is new

The work that made the state incremental moved two mechanisms the first pass held: quorum
counting, now one rule for approvals and declines (`Decide._ticket_votes`), and each
principal's decisions, now an index by subject. It added others: the state's indexes, the
document cache, the incremental fold, the two forms of a reset's evidence (V7, V8), and
the wiring of each upstream fix. Each was mutated as the first pass mutated, with the
same runner rules; the targets that need an upstream fix run in a venv that has
interlock's `feat/fiduciary-prerequisites` and AgentGov's `feat/reset-memo` installed.

| Target | Source | Tests |
|---|---|---|
| quorum | `authority.py` (counting), `store.py` (decisions) | as in the first pass |
| state | `store.py` (the indexes, `copy`), `log.py` (the fold), `authority.py` (the cache) | `test_state.py`, `test_store.py`, `test_decide.py`, `test_authority.py`, `test_escalation_quorum.py` |
| verify, with U-5 | `bundle/verify.py` (the reset's forms) | `test_bundle_checks.py`, `test_bundle.py`, `test_bundle_tamper.py` |
| upstream, with each fix | `executor.py`, `bridge.py` | `test_executor.py`, `test_bridge.py`, `test_bridge_lifecycle.py`, `test_bundle.py`, `test_bundle_checks.py`, `test_crash_recovery.py` |

34 mutants: 15 of quorum counting (A02 to A16, the first pass's re-sited and three
more), 11 of the state (S01 to S11), 2 of the verifier (V28, V29) and 6 of the wiring
(U01 to U06). The first run killed 32. The two survivors were gaps, each now killed by a
test added for it:

| Survivor | Mechanism | Now |
|---|---|---|
| V29 | V8's check that a reset step's entry names its warrant, where the reset names it itself (U-5) | killed: a step whose result names another entry (`test_bundle_checks.py`) |
| U06 | only the open recovery's steps decide whether it answers a safety halt (U-6) | killed: a recovery closed after a safety halt, and the next, opened by a spent-out halt, whose quote goes to the principals (`test_bridge_lifecycle.py`) |

The state's mutants were each killed by `tests/test_state.py`, which holds every index to
the events it folds, over logs in any order, and a copy to leaving its source as it was.
