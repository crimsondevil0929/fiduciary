# Changelog

All notable changes to `interlock-fiduciary` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0]

Phase 5 of `docs/FIDUCIARY_SPEC.md` §11: the audit bundle. A lineage's authority, its
acts and the evidence for both leave the deployment as one file, which an auditor checks
offline against a charter digest they pinned themselves.

### Added

- **`fiduciary export-bundle --mandate <id> [--from] [--to] --out <b.fidb>`**
  (`fiduciary.bundle.export_bundle`). A deterministic tar (sorted members, mode 0644,
  owner 0, mtime 0), so one lineage over one range exports to identical bytes (§9.2). It
  holds:
  - the log from record 1, with every document, tool binding and intake item it names;
  - the lineage's ledger footprint, with RFC 9162 inclusion proofs against a signed
    checkpoint (`agentgov-ledger:<entry 1>`) and witnesses' cosignatures;
  - the lineage's ARC1 receipts as receipt bundles;
  - its ILOK1 runtime and operator records, and its escrow chains;
  - what compiling it reads from the deployment, and the keys it verifies under.

  It carries no row data, payloads, transcripts or secrets (F9-1). A bundle is never
  overwritten.
- **`fiduciary verify-bundle <b.fidb> --charter <digest> [--keys] [--witness] [--skew]`**
  (`fiduciary.bundle.verify_bundle`). V1 to V12 (§9.3), with no database and no network
  (F9-3). Every check and every finding is printed. The exit code is the first failing
  check's: 0 pass, 1 usage, 2 unreadable, 3 malformed, 4 signature, 5 authority,
  6 monotonicity or policy, 7 ambient, 8 evidence, 9 revocation or time.
  - Every decision in the log is taken again by the service's own rules, from the state
    before it.
  - Every act on the lineage's ledger footprint must trace to a bind, a plan, a session's
    metered call, a recovery or extension record, or a warrant's step. Anything else is
    named as ambient, and labeled break-glass when a charter declares its key so.
  - Each warrant step's evidence is checked against the systems' records, including a
    message head that moved after its ticket (U-4).
  - No commit or delivery may follow a revocation's trip.
- **`docs/BUNDLE_FORMAT.md`**, the format specified for an implementation in another
  language, and **`vectors/fidu1/bundles/`**: 30 golden bundles, three valid and the
  others failing each check and exit code, each with its recorded verdict
  (`scripts/make_bundles.py`).
- `fiduciary verify --skew`, defaulting to `[bridge] skew_seconds`: how far an instant may
  stray from its anchor's ledger time.
- The live acceptance suite runs on the wall clock, and exports and verifies each run's
  bundle, in process and over MCP. It also runs a sixth scenario, `ask-the-principals`.
  It never falls back to another model, and `--max-cost` is a hard cap (default $2.00).

### Changed

- Every metered call bills under a memo that names its session and what it was
  (`FIDU1 session <id> model <model>`, `... tool <name>`), recovery's included, so an
  auditor can place each one in the session's life.
- A refused call's feedback names the steps a repair keeps and drops. A repair is offered
  only when no dropped step depends on a kept one. A committed call names its plan and
  the queued requests.
- An MCP built-in's result carries the sentence and the JSON value, as a session's does.

### Fixed

- The last session to close a lineage's recovery closes its runtime: an unsettled step's
  hold is voided, and the unused reserve returns to its funding scope (F8-9). Interlock
  does not reopen a closed recovery (U-6, §12).

## [0.4.0]

Phase 4 of `docs/FIDUCIARY_SPEC.md` §11: the bridge. An agent acts through a session, in
process or over MCP, and every call it makes is bound by the mandate's pinned bindings,
metered, counted, and escalated to the principals when it reaches a boundary.

### Added

- **Sessions** (`fiduciary.bridge`). `Fiduciary.from_config` (or from its parts), and
  `Fiduciary.session(mandate, tenant=...)`, recorded before its first call. A session's
  methods are `call`, `govern`, `tool_definitions`, `tickets` and `close`, and nothing
  else (F8-1). In process, a session starts only with `in_process = "tool-calls-only"`
  (F8-10).
  - `call` resolves the tool against the bindings the version in force pins (F4-16),
    validates its arguments against the binding's schema and the canonical domain before
    anything is built (F4-13), and builds the plan from the binding alone, its tenant the
    session's (F4-14, F8-2). A refused plan is repaired, its ticket raised, and a rule's
    warrant executed in process (F7-14).
  - Passthrough tools are metered at their price through the lineage's guard, and every
    call counts against the cognitive breaker and the session's velocity (F4-15, F6-5).
  - Results carry interlock's sanitized feedback, and an escalation's ticket id, class
    and deadline; never a bound, a diff, another tenant or an approver (F8-3).
  - `govern(client)` meters model calls under the allowlist, a configured price,
    `max_output_tokens` and `per_call`. A halt goes to interlock's `RecoveryRuntime`; a
    spent reserve's quote raises an extension ticket, and a grant moves billing to the
    scope it funded (F8-8). A safety halt is never answered with money (F8-9).
  - A call refused before anything is built is recorded (`session.refused`, F8-12).
- **The MCP bridge** (`fiduciary.mcp`, `fiduciary mcp serve|proxy`).
  - `tools/list` is the pinned bindings and two built-ins, `fiduciary_request` and
    `fiduciary_ticket_status` (F8-4). `tools/call` is `Session.call`; a malformed
    request is a JSON-RPC error and builds nothing (F8-5). No method, path or tool takes
    an approval (F7-4).
  - stdio, and Streamable HTTP: `POST /mcp`, one bearer token per session, loopback or
    TLS, refusing other paths, methods, sessions and browser origins (F8-6).
  - The proxy fronts an upstream MCP server and exposes only the tools a passthrough
    binding pins, each call metered and counted (F8-7).
  - A bridge process also judges the intake and executes the warrants formed.
- **`fiduciary.Tool`** writes a binding in code; `fiduciary tool digest module:name`
  pins it.
- **Records and store**: `session.opened`, `session.refused` and `session.closed`, each
  replayed by `verify`. The stores are at version 4, with the `sessions` projection;
  earlier stores upgrade in place.
- **Configuration**: `in_process`, `[bridge]` (`http`, `skew_seconds`, `handlers`,
  `pricing`), `[receipts]`, and `[executor] outbox`.
- **The live acceptance suite** (`scripts/live_acceptance.py`): business requests to a
  Claude model through a session, opt-in on `BENCHMARK_API_KEY` and cost-capped, with
  reference proposals and back-office oracles that run offline in CI.

### Fixed

- **`Ticket.observed` is checked.** Replay refuses a ticket whose `observed.log` is not
  the head its record follows, and `verify`, given the ledger, names one whose
  `observed.ledger` the ledger does not hold or that does not precede its anchor (F4-17).
- **The executor reads interlock's operator log under the keyring `interlock.toml`
  trusts**, and its key must be one of them. A log holding another operator's records
  could not be read before.
- **The executor finds each lineage's own budget guard**, so quotes issued under one
  mandate's margin and ttl are granted where they were issued.

### Tests

- The end-to-end lifecycle on both stores against AgentGov's `DummyLLM`: a commit, a
  refusal repaired by rule, the envelope spent, recovery, a quote, a grant by two
  approvals signed through a fake AWS KMS, and the task finished on the extension.
- MCP conformance in process, over stdio, over HTTP (with TLS), through the proxy, and
  as subprocesses from a full `fiduciary.toml`.
- The adversarial agent suite: SQL in arguments, another tenant's id, floats and
  oversized strings, unpinned tools, forged approvals, and a ticket storm.
- The open escrow commit intent path of `resolve()`.
- The README's session example, run as written.

## [0.3.0]

Phase 3 of `docs/FIDUCIARY_SPEC.md` §11: escalation tickets, quorum, warrants, their
two-phase execution against Interlock and AgentGov, and resolving what a crash left.

### Added

- **Tickets and warrants** (`fiduciary.documents.ticket`, `fiduciary.documents.warrant`).
  - A ticket's evidence is typed per class: `refused`, `held`, `dead`, `undo`,
    `extension` and `amend` (§7.1, F4-17).
  - `agent.statement` is clipped to 512 characters, and enters no rule, option or
    decision (F4-18).
  - Each option carries its resolution's digest and its class's quorum (F4-19).
  - A principal may approve a narrowing of an option (F4-20).
  - A warrant's basis is the approvals counted or an autonomous rule (F4-24). Its
    `not_after` is the earliest of its bounds (F4-25).
  - `execution_steps(warrant, ticket)` is the pure plan an execution records.
    `resolution_quorum` is §7.2 in one place.
  - Golden vectors for both documents.
- **Escalation** (`fiduciary.escalation`).
  - The six triggers, each reading its evidence from Interlock and AgentGov, never from
    the agent's prose.
  - One ticket per distinct evidence (F7-1). Past `escalation.max_open`, a trigger
    raises nothing and its boundary stands (F7-2).
  - Autonomous rules within their closed vocabulary. An autonomous grant is bounded per
    quote and over the version's life, counted from the log (F7-9 to F7-11).
- **Quorum, decline and lapse** (`fiduciary.authority`).
  - A warrant forms in the same log transaction as the approval that completes it. It
    counts each principal's latest decision once, only under the charter's keys, and
    only when recorded before both deadlines (§7.3, F7-5, F7-6).
  - A ticket is declined when declines make every option unreachable (F7-7), and lapses
    at its deadline (F1-6).
  - `verify` replays every Phase 3 record. That includes each execution intent, as its
    warrant's steps, and each outcome, as an answer to its intent.
- **Execution** (`fiduciary.executor`).
  - Two phases, for all eleven resolutions: `execution.intent`, each step, then
    `applied` or `refused`.
  - The warrant's digest is bound into each system's signed or hashed record: the ILOK1
    operator's reason, the AgentGov anchor immediately before a reset, and the guard's
    `approved_by` and `declined_by` (§7.6, F2-2).
  - Each execution holds a claim on its warrant. `resolve()` skips intents a live process
    claims, and resolves the others from the four places a digest can reach. Evidence
    not yet settled leaves an intent open (F7-13).
  - The F3-9 guard refuses AWS KMS executor and runtime keys, and outbox actions are
    chunked to 8 targets (U-2).
  - A quote or repair proposal lost to a restart refuses the step and lapses the ticket
    (F7-14, U-3).
  - A head the ILOK1 intent signed that is not the one the ticket observed is recorded
    as a mismatch (F7-15, U-4).
- **The stores are at version 3**, with the `tickets`, `warrants` and `executions`
  projections. Version 1 and 2 stores upgrade in place.
- **`Binder.resume`** opens the engine of every bound lineage at start, writing nothing.
- **The command line**:
  - `fiduciary tickets list|show`, which renders a ticket in fixed templates from its
    signed bytes (F7-3);
  - `fiduciary warrant show` and `fiduciary resolve`;
  - `fiduciary approve` of a ticket, offline with `--charter FILE`, of an option or of a
    narrowing read from a file;
  - configuration gains `[executor]` (`key`, `signer`, `log`) and `[runtime]`
    (`records`, `key`, `signer`).
- **`scripts/ilok1_sizes.py`** measures the ILOK1 records the executor has interlock
  sign, and `--check` holds §3.5's table to it.

### Fixed

- **An anchor was written before its record committed.** `Log.append_all` anchored as
  soon as its own block ended, which inside an outer transaction is not a commit. A
  rollback or a crash then left the ledger anchoring a record the log never held, and
  the log could no longer be anchored. Stores now run actions after the outermost
  commit.
- **PostgreSQL write transactions now wait for each other**, as SQLite's `BEGIN
  IMMEDIATE` does. A second writer used to fail at the append instead.
- **SQLite readers fetch their rows before yielding them.** An open cursor held a stale
  read snapshot, and a second process's write made the next `BEGIN IMMEDIATE` fail at
  once with "database is locked".
- **A closed `MandateEngine` releases its escrow chain**, after any plan in flight. The
  chain used to stay claimed until garbage collection.

### Proven

- **Crash consistency.** The service runs as a child process and is killed with
  `SIGKILL` at every checkpoint (`raised`, `recorded`, `formed`, `intent`, `acted:<n>`,
  `applied`) of all eleven resolutions, on both stores: 66 kills per store. F10-9 to
  F10-11 hold.
- **Exactly once under contention.** Two service processes share one deployment and an
  AgentGov ledger shared through PostgreSQL. Approvals are replayed, and some arrive as
  their warrant forms. Each warrant still executes once.
- **Quorum, by property test.** A warrant forms iff §7.3 says it does, from the
  approvals it counts. The generated approvals include duplicates, wrong and suspended
  keys, another ticket, resolution or deployment, and approvals expired or recorded
  late.
- **Tamper.** On both stores, `verify` names:
  - a forged approval, and an approval moved to another ticket or deleted;
  - a reordered log;
  - an edited ticket, warrant or execution projection;
  - a second intent, and a lapse written before its deadline.
- **Fatigue.** A ticket storm past `max_open` raises nothing, and every boundary stands.
- Coverage on both stores: 97.29% of lines and 94.31% of branches.

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
- **`refines` keeps `min_cycle_repeats` at 2 or more under a parent that detects cycles**
  (F5-11). AgentGov's cycle detector does not run below 2 repeats, so a child at 1 was
  looser than its parent while the order counted it as tighter. The cognitive property
  test found it at seed 7810, and a fixed sweep of 400 seeds now runs on every test run.
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
