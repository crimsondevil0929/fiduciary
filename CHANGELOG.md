# Changelog

All notable changes to `interlock-fiduciary` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0]

Phase 2 of `docs/FIDUCIARY_SPEC.md` §11: compiling a mandate into Interlock's checkers
and AgentGov's policies, binding it to a deployment, and what becomes of a lineage after.

### Added

- **Compilation** (`fiduciary.compiler`).
  - `compile(lineage, deployment=...)` turns a mandate and its ancestors in force into a
    `CompiledMandate`. It first writes a canonical description of every constructor and
    its arguments, and the description's SHA-256 is the compiled digest. Every object is
    then built from that description (F6-1, F6-2).
  - Every applicable built-in Interlock checker is compiled. `TruncationGuard` and
    `NoSchemaChange` are always on (F6-3).
  - Also compiled:
    - the sink registry, narrowed to the mandate's sinks and operations;
    - rate windows, namespaced `f<16 hex>_<name>` per lineage and inherited down it;
    - `GovernancePolicy`, `CognitivePolicy` and the interceptor's bounds;
    - `RecoveryPolicy` and `BudgetGuard`'s parameters;
    - a `ReceiptIssuer` with `issuer="fiduciary:<mandate_id>"` and `policy_epoch` set to
      the version.
  - Compilation fails closed: `CompileError` names everything the deployment cannot
    enforce, such as a table it does not observe, a sink it lacks, a pinned tool binding
    it does not run or a directive whose text it lacks (F6-4, F6-5).
  - `receipt_problems` checks that a receipt shows the compiled policy ran (F6-7).
    `canonical_digests()` probes whether this Interlock digests sets canonically (U-1).
    Without U-1, the five checkers that hold a set are checked by name only (F6-8).
- **Fiduciary's checkers** (`fiduciary.checkers`), all pure functions of the plan and
  diff, each with a repair hint:
  - `MandateScope` (`fiduciary_mandate`) admits only the subject scope and its
    satellites;
  - `TenantAllowlist`;
  - `BoundStatements` admits only the statements the pinned tool bindings carry.
- **Binding** (`fiduciary.binding`).
  - `Binder.bind` checks every §6.4 precondition before writing anything. It then
    delegates the envelope (`memo="FIDU1 bind <digest>"`, idempotently), builds the
    lineage's engine and records `mandate.bound`.
  - Each lineage has one escrow chain, kept across versions.
  - Rebinding at a plan boundary: the plan in flight finishes under the version it was
    admitted under (F1-3). A larger envelope is delegated to `{scope}/ext-N`, and a
    smaller one is released.
  - `Binder.issue` issues a version and, in the same log transaction, rebases every
    descendant that still refines it and suspends every one that does not (F1-4, F5-7).
    Bound lineages are then rebound, parents first.
  - `Binder.derive` signs a derived child with the service key and issues it.
  - `Binder.revoke` revokes on the revocation quorum of the lineage's roster, an
    ancestor's, or the charter's. `Binder.expire` ends lineages past their term. Both
    cascade to every descendant (F5-8).
  - Suspension, revocation and expiry trip each lineage's subject scope and every
    satellite in one AgentGov transaction. No plan of a halted scope commits (E3-8), and
    the relay holds its messages (§5.5).
- **The lineage projection.** The stores are at version 2, and a version-1 store
  upgrades in place. `State` gives each lineage's status, parents, children and binding.
- **`Log.append_all`** writes several records in one transaction, then anchors them.
- **Tool bindings** may name parameters `%(name)s`, as psycopg binds them on
  PostgreSQL, besides `:name`. A statement uses one style.
- **The command line**: `fiduciary mandate compile`, `bind`, `derive`, `amend`, `revoke`
  and `expire`.
- **Configuration**: `interlock = "interlock.toml"` and `[binding]` (`tools`,
  `directives`, `chains`), the deployment compilation reads.

### Changed

- **`refines` requires `exempt_tools` to be equal**, no longer a subset (F5-11). A child
  that judges a call its parent exempts can trip *later* than its parent. The cognitive
  property test found this on about one random call sequence in five. §5.2 is amended to
  match.
- `Authority.issue_mandate` refuses to amend a lineage that is bound or has descendants.
  Such an amendment goes through `Binder.issue` (`fiduciary mandate amend`), which
  rebinds and rebases what hangs from it.

### Proven

- Semantic monotonicity (F5-10) over generated mandates, plans and diffs: whatever a
  child admits, its parent admits. Every compiled checker type refuses some generated
  case. The cognitive variant (F5-11) runs over generated call sequences.
- Under eight `PYTHONHASHSEED` values, `compile` gives one compiled digest. With U-1
  (Interlock's main branch), every checker record is identical too.
- On SQLite and PostgreSQL, a bound mandate refuses a plan outside it on each of: table,
  operation, tenant, row count, drawdown, sink, sink operation, cap, recipient and
  unbound statement. Every receipt satisfies F6-7.
- Also on both stores:
  - concurrent children are held exactly to their parent's window;
  - window history survives an amendment;
  - the amendment applies at the next plan boundary;
  - a revocation mid-stage commits nothing, the relay holds the lineage's messages and
    every satellite is tripped.

## [0.1.0]

Phase 1 of `docs/FIDUCIARY_SPEC.md` §11: FIDU1, the documents, the order on mandates,
the log and its stores, and the command line.

### Added

- **FIDU1** (`fiduciary.fidu1`).
  - Canonical JSON in agentgov's value domain, read strictly: exactly the fields a type
    names, each of its form, and every set sorted and free of duplicates (F3-1).
  - One signature construction, over a domain and the SHA-512 of the document, so every
    signing input is at most 96 bytes and AWS KMS signs any document (F3-2 to F3-8).
  - Ed25519 only. HMAC is refused.
- **The documents** (`fiduciary.documents`), each signed, verified and refused with a
  stable error code:
  - the charter (F4-1 to F4-6);
  - the mandate, with every bound of §4.2 (F4-7 to F4-12);
  - the tool binding and its JSON Schema subset (F4-13 to F4-16);
  - the approval (F4-21 to F4-23);
  - the log record (F4-26).
- **The order on mandates** (`fiduciary.refines`). `refines(child, parent)` is the §5.2
  table. `delegation_problems` adds the rules that relate a child to its parent (F5-1 to
  F5-3).
- **The FIDU1 log** (`fiduciary.log`), on SQLite and PostgreSQL (`fiduciary.sqlite`,
  `fiduciary.postgres`).
  - Each record, the documents it names and its projection rows are written in one
    transaction.
  - Each record is anchored into AgentGov as `FIDU1 <log> <seq> <hash>` on the scope
    `fiduciary`.
  - Both stores refuse an update or deletion of the log, the documents and the intake,
    and refuse a record that is not the next at its head.
  - PostgreSQL has three roles: service, intake and audit (F10-1 to F10-5).
- **Authority** (`fiduciary.authority`):
  - approvals recorded or refused;
  - charter adoption, key suspension and mandate issuance by quorum, counted by
    principal;
  - derived children that refine their parent.
- **Verification** (`fiduciary.verify`). `verify_store` replays every decision, and
  checks every record, document, projection and anchor.
- **The command line**: `fiduciary keygen`, `install`, `sign`,
  `charter init|show|propose|adopt|suspend`,
  `mandate draft|check|diff|show|propose|issue`, `tool digest`, `approve`, `submit` and
  `verify`.
- **Configuration**: `fiduciary.toml`, with `[signers.<name>]` for keys held by AWS KMS
  or an HTTP signing service.
- **Golden vectors** in `vectors/fidu1/`, with `scripts/make_vectors.py` to regenerate
  them.
