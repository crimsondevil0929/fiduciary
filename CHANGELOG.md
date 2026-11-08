# Changelog

All notable changes to `interlock-fiduciary` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.12.0] - 2026-11-07

Agents that edit files and agents that work through SaaS APIs, governed like the rest; a console
rebuilt as a workbench; and every count written as English. Requires interlock-escrow 1.5.0 and
interlock-agentgov 1.3.2.

### Added

- **Two new workflows in the demo.**
  - `--domain workspace` (Platform ops): an ops agent edits a checkout service's config
    workspace on interlock's new filesystem substrate, in `swap` mode. A config edit is swapped
    in as one new version; a renderer hook that turns one edit into 25 region files is refused;
    a token appended to `.env` and a deleted backup script are refused by path class; an
    engineer's hand edit to the live config refuses the commit that measured it; a production
    rollout waits for 2 of 3; a staging rollout is released with the break-glass key.
  - `--domain service-desk` (Customer support): a support agent works tickets on a JSON REST
    API through interlock's new REST substrate. A triage is written on its ETag; an escalation
    that the desk's automation turns into a reassignment and a new incident is caught by the
    read-back and undone; another account's ticket and a $400 credit are refused before the
    desk or billing sees them; a human agent's edit refuses the write; a goodwill credit waits
    for 2 of 3.
  - Both play scripted calls, so `--replay` stays free. Both have sandbox presets.
- **`fiduciary init --substrate filesystem --directory DIR`:** `write_file` and
  `replace_in_file` in a private copy of the directory; the starter mandate allows config,
  source, tests, docs and other files, and refuses secrets, workflows, infrastructure,
  dependencies and scripts.
- **`fiduciary init --substrate rest --server URL --resource NAME=/PATH`:** a
  `set_<resource>_status` tool for each collection, written on the version read and read back,
  with `--tenant-field` naming each record's account and `--managed` the fields the API writes
  itself.

### Changed

- **The console is a workbench, not one long page.** One workflow at a time, picked from eight
  in six families (coding and repo agents, cloud and infrastructure, clinical, SaaS and CRM,
  supply chain, fintech), with the coding agent first. Each workflow has four tabs: Scenarios
  (a rail of the seven scenarios beside the one open, the input gateway and the staged
  execution side by side), Mandate and lineage, Warrants and operator log, and Offline proof.
  The overview, the measurement ladder and which check stopped which plan moved to "How it
  works". Every view has a route (`#/run/<workflow>/<tab>/<scenario>`), and old anchors still
  land on the right tab. It prints the view on screen, not every section at once. Checked in
  light and dark, at desktop, tablet and phone widths.
- **Plain English throughout.** The console, the scenario titles, the overview and the README
  were rewritten as a person would say them: "Each delivery receipt records the vendor's own
  ID for what it created", not "the ids the vendor gave what it made". The measurement ladder
  says the directory and REST substrates are shipped, instead of "next".
- **No message writes `receipt(s)` or `turn(s)` any more.** Every count fiduciary prints takes
  the form its number takes, as interlock's and AgentGov's now do. Signed records written by
  earlier versions (golden vectors, upgrade fixtures) keep their bytes.
- **The overview lists eight workflows.** A refused commit after a concurrent edit counts as
  "commit only on the version measured" on every substrate, not only Git and FHIR.

## [1.11.0] - 2026-11-07

An outside audit, run cold on the released code, found that a bundle could leave things
out and still pass both verifiers with every key pinned, and that the daemon had three
ways to go wrong under concurrency. This release closes each one, and turns every proof
of concept into a test.

### Security

- **A bundle may not leave anything out (F9-10).** Anyone able to rewrite a `.fidb`, with
  no key at all, could cut its tail off (records, the ledger after the last kept anchor,
  the receipts) or delete every receipt, relist the manifest unsigned, and `verify-bundle`
  and the console's in-browser check both passed it. Now:
  - the manifest's signature by the service key is **required** (V9). `export-bundle`
    signs every bundle, and refuses to export without the service key's signer. Every
    bundle the CLI and the demo exported was already signed;
  - the cut is in the bundle, proven, and is the anchor of the range's last record (V6), so
    a range cannot end early under the checkpoint of more of the ledger;
  - every action receipt the lineage's escrow chain names (`; receipt <id>`, which
    Interlock writes before it issues the receipt) is in the bundle (V5). One left out is
    named by its record, and so is one never issued.
- **The container is read as it is written.** `verify-bundle` read bundles with Python's
  `tarfile`, which fills a GNU sparse member's holes in memory: a 2 KB file made it
  allocate over 600 MB, through `POST /api/verify` too. Bundles are now read header by
  header, by the rules the exporter writes and the browser reads: regular files at relative
  ASCII paths, in POSIX ustar headers, mode 0644, mtime 0, zero padding, zeros after the
  end. Sparse, pax, GNU and link entries are unreadable (exit 2). At most 2 GiB is read, a
  member at most 1 GiB, and at most 1,000,000 members.
- **The browser verifier checks what the anchors are read from.** It read anchor memos
  from `ledger/entries.jsonl` without checking the entries, so a sealed bundle could hide
  one of its lineage's own records and pass in the browser. It now recomputes each entry's
  hash (AGOV1 to AGOV3), checks its inclusion in the checkpoint and the checkpoint's
  signature by the ledger key, and holds the cut to the range's last record (a new
  `ledger` check, part of V6). It fails an unsigned manifest, and checks that every receipt
  an escrow chain names is in the bundle (`receipts`, part of V5). Its inclusion proofs use
  whole-number arithmetic: a negative or fractional leaf index is refused, and a tree past
  2^31 leaves is proven correctly.
- **The two verifiers agree.** Each disagreement the audit found now gets one verdict
  from both: a member's mode, mtime, type or pax header; a manifest of another format or
  not canonical; `log.jsonl` with no final newline; a duplicate key; a record line not in
  canonical form (V1 now requires each line to be its record's canonical bytes).
- **The TLS bridge can no longer be stalled before authentication.** The listening socket
  was wrapped whole, so each TLS handshake ran inside `accept`. One client that connected
  and said nothing stopped every other. Each handshake now runs in its connection's own
  thread, within 5 seconds, and a connection that waits 60 seconds between bytes of a
  request is closed.
- **A webhook's redirect is no longer followed (F10-26, amended).** The notifier posted
  through urllib's default opener, which followed a `301`, `302` or `303` as a `GET`
  without the notice, to wherever `Location` pointed, past the URL checked when the
  webhook was configured, and counted the `2xx` found there as delivered. Every `3xx` is
  now a failed delivery, retried with backoff as any other.

### Fixed

- **Anchors are written in order (F4-26, amended).** A record whose anchor the ledger
  refused once stayed unanchored while the next record was anchored at once. The repair a
  turn later put it after its successor, and V12 then called the log tampered with, for
  good. Now a record is anchored only once every record before it is: a gap is looked up
  in the ledger by memo and anchored first, oldest first. That covers a record another
  process wrote too: a cluster's executor and writer both append to the log, and the
  writer could anchor its record before the executor's late anchor of the one before
  (found by the PostgreSQL soak while testing this release). A log opened in a new
  process checks the whole log first, so a record a crash left unanchored is anchored
  before the next one.
- **Threads no longer share a transaction (F10-35).** The SQLite and PostgreSQL stores
  each serve the service's threads over one connection. A read on another thread saw a
  transaction's rows before they committed, and kept seeing them after a rollback, so a
  notice could go out for a record that never stood. A write on another thread joined the
  open transaction and was lost with its rollback. The store's lock now holds each
  transaction, from its start to the last action after commit, against every other read
  and write. The notifier reads through a read-only connection of its own
  (`Store.reader()`).
- **`Retry-After` tells the truth.** An agent that holds its most sessions was told to
  retry in 5 seconds, whatever the wait. It is now told the seconds until its least
  recently used session is closed idle, and how to end one (`DELETE /mcp`).

### Changed

- **The bridge's listener queues 64 connections** (it was the standard library's 5). A
  burst of agents opening sessions had connections reset.
- **`fiduciary init --substrate sqlite` says what it did to your database.** It measures
  the file before and after, and says so on its output (`changed`), in `init.json`
  (`changed`) and in the README: the journal moved to WAL, Interlock's tables added, and
  whether any table of yours changed.
- **The next steps `init` prints are the README's and the quickstart's**: `fiduciary serve`
  and the agent's `claude mcp add` line for a substrate, and `mcp proxy` (with its relay)
  for an MCP server. It printed `mcp serve`, which cannot run beside the writer.
- **Bundles are checked against pinned keys from the first one.** `fiduciary init` writes
  `pinned-keys.json`, the deployment's public keys read from its configuration as
  `export-bundle` reads them, and `charter.digest`. The quickstart and the README `init`
  writes verify with `--charter` and `--keys`. Before, they verified with the charter alone,
  so every key but the principals' came from the bundle itself. `init` prints the command
  (`audit`) and says where the private keys are and who each belongs with (`custody`).
  The console's Verify page pins the same file.
- **The quickstart's `call.py` ends its session** with `DELETE /mcp`. Run five times, it
  used up the agent's four sessions.
- `uv.lock` resolves interlock-escrow 1.4.3 and interlock-agentgov 1.3.1 from PyPI, so the
  site is built on agentgov 1.3.1, interlock 1.4.3 and fiduciary 1.11.0. The floors are
  unchanged. `live-run/stack-demo-latest` is rendered again on those versions with
  `--replay` ($0): its 16 bundles reach the verdicts and findings they had on 1.10.1 (run
  ids aside), and pass every check in the browser.
- **Golden bundles.** There are 40, up from 37: a tail cut off with no key, a range ended
  early and signed again, and a receipt left out and signed again. The three unsigned
  vectors now also fail V9. `scripts/make_bundles.py --rederive` derives the tampered
  bundles again from the valid ones and takes every verdict again; each bundle derived
  before came out byte for byte the same.

## [1.10.1] - 2026-11-06

The first thirty minutes, made to work as written; a one-page threat model; and honest
package metadata. No change to what a bundle holds or how it verifies.

### Added

- **`docs/QUICKSTART.md`: put it in front of your agent in 30 minutes.** From `pip
  install` to `fiduciary init`, the agent's first governed calls, a refusal the trigger
  causes, an amendment two of three principals approve, and an offline bundle that passes.
  `tests/test_quickstart.py` runs every marked command in it, in order, so the guide cannot
  drift.
- **`docs/THREAT_MODEL.md`**, on one page: what the stack protects against, what it does
  not (a compromised host in real time, enough colluding principals, reads, a remote
  system's own actions after a request, purposes the mandate allows), the trust boundary of
  each measurement class (T, P, V, R), who holds each key and what its theft means, and what
  offline verification proves.
- **`fiduciary tickets show TICKET --out FILE`** writes the ticket's signed bytes, so a
  principal can read and approve it on their own machine with `fiduciary approve FILE`.
  `approve` took only a file, and no command wrote one.

### Changed

- **A deployment `init` writes for a substrate runs as one writer** (spec F10-34).
  `fiduciary serve` holds the ledger and serves the agent's tools on `127.0.0.1:8765`, with
  the bearer token `init` wrote to `keys/agent.token`; `fiduciary.toml` holds only its
  SHA-256. The README `init` writes said to run `mcp serve` beside `fiduciary serve`, which a
  SQLite ledger does not allow: the second process could not open it.
- **`init` names the triggers the starter mandate will refuse.** A trigger that writes a
  table `--table` left out, or another row past the one-row limit, is printed as a caution
  and written into the deployment's README.
- **The README `init` writes ends with the bundle's export and verification**, the mandate
  and charter digest written in. An `--mcp` deployment's README no longer lists `fiduciary
  serve` beside the proxy, which records approvals and carries out warrants itself.
- **`fiduciary mandate propose` runs beside the writer.** Proposing stores a signed
  document and records nothing, so it no longer opens the ledger the writer holds.
- **The README opens with how AgentGov, Interlock and Fiduciary fit together**, the live
  console, the quickstart and the threat model, and what a write costs, measured.
- **The console's footer links to the quickstart, the threat model and the three
  repositories,** and heads the verifier's notes "Notes from the verifier": since 1.10.0
  they include the delivery receipts it checked, which "Out of scope" misnamed.
- **The development status is "4 - Beta",** not "5 - Production/Stable": a full test suite
  and offline verification, but no external production deployment yet.

## [1.10.0] - 2026-11-06

A bundle now carries the relays' delivery receipts, so an auditor can match a vendor's or
a system's own log against the bundle alone. And the recorded claude-opus-5-5 run can be
replayed on today's engine at no cost, so the public site shows the model's real turns
beside everything 1.9.0 added. Same upstream releases as 1.9.0. Every earlier golden
bundle verifies to its recorded verdict, byte for byte.

### Added

- **Delivery receipts in bundles** (spec F9-8, `docs/BUNDLE_FORMAT.md` §3.5).
  - A bundle carries each delivery receipt whose action receipt it carries. A settler's
    receipt has no ledger anchor, so the receipt log's order stands in for the ledger's:
    one settled after the bundle's cut is the next bundle's.
  - `keys.json` names the relay keys that attested them (`relays`), only when there is
    one. A bundle without a delivery keeps its bytes.
  - V5 checks each: the receipt log's signature, inclusion in its checkpoint, its
    binding to the action receipt, and the relay's attestation. The console's
    in-browser checks do the same with Web Crypto (`report/verify.js`, `deliveries`).
  - Three golden bundles: a pack's after bundle with two attested deliveries, a relay
    key the auditor did not pin (V1), and an attestation forged by the receipt log's
    own key (V5).
- **`fiduciary reconcile-actions <bundle> <export>`** (spec F9-9). It matches a system's
  own log (Stripe, SendGrid, EDI records, a Kubernetes audit log, FHIR AuditEvents,
  GitHub's audit log) against the receipts a bundle carries, with no `receipts.jsonl`.
  The bundle is verified first, and its receipts are used only when V1, V2 and V5 pass.
- **`fiduciary demo --replay [RECORDING]`** (`fiduciary.demo.replay`).
  - It plays every scenario again on the installed engine. Where a recorded model worked
    one, its recorded turns choose the calls: no model is called, and each turn is
    metered from its recorded usage, so the ledger shows what the turns cost then.
  - With no RECORDING, it replays the claude-opus-5-5 recording that ships with
    fiduciary: six scenarios recorded 2026-10-25 to 2026-11-01, $0.154312 when recorded.
  - A tool the engine no longer offers, arguments its schema no longer takes, or an
    outcome that differs from the recording stops the replay (exit 2). Ids the engine
    mints afresh are mapped, and a tool result worded differently is shown both ways.
  - A live run now records each turn's usage, so it is a recording itself.

### Changed

- Each industry's audit reconciles its system's log against `after.fidb`, and the
  command the console shows is `fiduciary reconcile-actions after.fidb ...`.
- The zero-data scan counts the vendor's ids for what it made apart. Each delivery
  receipt names one (spec F9-1, amended): it is what the vendor's log is matched on.
- The public site is built with `--replay`, so it shows the model's recorded turns, all
  16 bundles and the six reconciliations together.
- `fiduciary init` ends with the commands to serve the tools and open the approvals desk.
- The package's PyPI page links to the live console, the repository, the spec, the
  changelog and the issue tracker. 1.9.0 and earlier linked to nothing.

### Fixed

- `fiduciary demo --domain coding-agent` was refused by the command line, though the
  pack runs.
- `report.md` still named each scenario "Act".

## [1.9.0] - 2026-11-05

A new deployment pointed at the system an agent will change; each vendor's own log
reconciled against what the relays delivered; and a console and demo redrawn and rewritten
in plain sentences. Requires `interlock-escrow` 1.4.2 and `interlock-agentgov` 1.3.0.

### Added

- **`fiduciary init --substrate sqlite|postgres|git|kubernetes|terraform|fhir`**
  (`fiduciary.starters`, spec §10.10, F10-31 to F10-33).
  - It reads the target and writes starter tool bindings for it:
    - SQLite and PostgreSQL: `insert_<table>` and `update_<table>` for each table it can
      read (`--table` picks some);
    - Git: `write_file` and `replace_in_file`;
    - Kubernetes: `scale_deployment` and `set_image`;
    - Terraform: `set_<variable>` for each input variable;
    - FHIR: `set_<type>_status` for each `--type`.
  - The starter mandate allows inserts and updates, one row or object a plan, at most 120
    plans an hour, and no delete. On Git it refuses writes to CI, infrastructure,
    dependency and governance files; on Kubernetes, a scale-up past three times the
    replicas.
  - Columns and variables FIDU1 can't hold exactly are left out and named.
  - On PostgreSQL it writes `grants.sql` for the stage role, which must not be a
    superuser. On Terraform it adds `interlock_stage.tf` when the configuration has no
    stage marker, and says so.
  - Each deployment's README lists the substrate's remaining setup.
- **Delivery receipts and vendor logs in the demo** (`fiduciary.demo.vendor_logs`).
  - Every deployment's relays settle what they delivered, so each request has a delivery
    receipt (interlock's ARC1-delivery) in `receipts.jsonl`.
  - Fintech, supply chain and SaaS keep the vendor's own log in `audit/`: Stripe's Events
    API, the EDI partner's records and SendGrid's Event Webhook.
  - Each break-glass scenario adds a change made in the vendor's own tools: a Dashboard
    refund, an EDI order sent directly, an invitation sent from SendGrid.
  - Each audit scenario runs `agentgov reconcile-actions` over the log. It matches every
    request the deployment made and names the one it didn't. The console shows the result.
- **Recorded runs retold** (`fiduciary.demo.narration`). Scenario titles and ledes are data.
  `scripts/stack_dashboard.py --keep` rewrites a recorded run's wording and leaves its
  model calls byte-identical.

### Changed

- **The console, redrawn** (`report/console.css`, `report/console.js`). One type scale,
  hairline rules, tabular numerals, mono for wire data, and a dark and a light palette.
  The scenarios show the pipeline a call goes through (stage, measure, adjudicate, commit
  or put back), the measurement rung, the quorum's slots, and the verifier's V1 to V12 as a
  grid. Routes, requests and checks are unchanged.
- **Plain prose** in the console, the demo's scenarios, the Markdown report and the README.
  The demo's scenarios are numbered, not called acts, and read as incident traces.
- The desk shows delivery receipts as the requests they delivered (F10-20, amended).
- A tool call that fails because the substrate is set up wrong, or can't be reached, is
  logged at `WARNING` by `fiduciary.bridge` with the cause. The agent is told only that
  nothing was tried.
- Requires `interlock-escrow>=1.4.2` (feedback that says a substrate is misconfigured, not
  unreachable) and `interlock-agentgov>=1.3.0` (the `stripe-events` and `sendgrid-events`
  parsers).

## [1.8.0] - 2026-11-04

An agent's MCP server, governed from its own tool list; the approvers told when they are
needed; and the browser's bundle checks held to every golden bundle in CI, and published.
Requires `interlock-escrow` 1.4.1.

### Added

- **`fiduciary init [--mcp -- COMMAND]`** (`fiduciary.init`, spec §10.10, F10-28 to F10-30).
  - It asks the server for `tools/list` and governs each tool by its MCP annotations:
    - a read-only tool becomes a metered passthrough;
    - a non-destructive write becomes a request made through an `mcp` sink, at most once;
    - any other write is held for a 2-of-3 warrant.
  - A tool whose input the binding grammar cannot hold whole is left out, and named.
  - It writes a working deployment: keys, charter, mandate, `interlock.toml` with the
    relay that runs the server, `fiduciary.toml` and a README.
  - `fiduciary.demo.mcp_desk` is a small MCP server to try it on.
  - `scripts/init_live.py` runs a live model through `fiduciary mcp proxy`.
- **Notices** (`fiduciary.notify`, `[notify]`, `fiduciary notify`, the service's
  `notifier` part; §10.9, F10-25 to F10-27).
  - They tell webhooks when a ticket is raised, a warrant forms, its execution is applied,
    refused or abandoned, or a ticket is declined or lapses.
  - Delivery is in log order and at least once. The cursor moves only past a `2xx`, and a
    retry is the same notice.
  - Three formats:
    - Standard Webhooks, signed with a dedicated Ed25519 key (`v1a`);
    - Slack blocks;
    - PagerDuty events keyed by the ticket.
  - Each notice links to the ticket in the console, and never carries a payload.
- **The browser's bundle checks, gated** (`report/verify.js`, F10-23 amended).
  - They are now one module that the page and Node both load.
  - New checks:
    - each document's path names its digest;
    - the bundle's keys are the ones pinned, when given;
    - every record in range has its ledger anchor;
    - in a sealed bundle, no withheld record is the bundle's own.
  - `tests/test_browser_verifier.py` runs the module over all 35 golden bundles: no false
    alarm, no silent pass on V1, V9 or V10 with pinned keys, and each outcome pinned in
    `tests/js/browser_vectors.json`. Node is required in CI.
- **The public site** (`scripts/build_site.py`, `scripts/check_site.py`,
  `.github/workflows/pages.yml`).
  - It is the console as a static page, with the demo's bundles, their pins and
    `SHA256SUMS`.
  - It is built from the commit (offline, no model) and checked: each bundle verifies as
    the page says and passes the page's own checks.
  - It is deployed to GitHub Pages from `main`.
- **The console's view of a deployment.** The desk now shows the mandate: its budget,
  quorums, windows and tables, every tool it pins and how each call is governed, and the
  latest receipts (`GET /api/deployments/<id>/profile`). A notice's link opens one
  ticket (`#/desk?d=&t=`).

### Changed

- `data.tables` may be empty (F4-12 table amended): a mandate whose tools write only
  outbound requests allows no table.
- `fiduciary mcp proxy` and `mcp serve` run the triggers in their turn, so a call held for a
  warrant is ticketed. A committed call whose request is held now tells the agent that the
  request waits for the principals' approval and is not yet made.
- `kit.interlock_toml` writes a typed sink's `type`, delivery window, attempts,
  unknown-outcome rule and pinned operation schemas.


## [1.7.0] - 2026-11-04

The console: a web control plane on loopback over any deployment a `fiduciary.toml`
names, and over the demo's runs. It decides nothing, holds no principal's key, and changes
nothing on disk by looking.

### Added

- **`fiduciary console`** (`fiduciary.control`, spec §10.8, F10-20 to F10-24): the
  deployments in `--dir` (each `fiduciary.toml`, and one level down) or named with
  `--deployment`, served on `--listen` (127.0.0.1:8740). `fiduciary demo --serve` serves the
  same console over its run; `--out DIR --serve` on a directory that holds a run serves that
  run again, read and never written.
- **The approvals desk.** Each ticket from the deployment's log, from its signed bytes: the
  receipt a refusal cites (rows, tables and tenants measured, the substrate, the rung of the
  measurement ladder, stated against measured, the checker that refused it); a held
  message's outbound payload, read from the outbox and checked against the `payload_hash`
  the ticket signs; the options with their quorums and pinned heads; the votes the log
  recorded and those still in the intake; the warrant and its execution.
- **Votes signed by the principal.** The console drafts an approval and returns its signing
  input; the browser signs it with the principal's key file (Web Crypto Ed25519), which never
  leaves the page; the console verifies the signature under the charter's key and, with
  `--accept-approvals`, submits it to the intake. A signed approval from `fiduciary approve
  --out` is taken as well. The deployment's writer records it.
- **The bundle verifier.** V1 to V12 in process, offline, against a deployment's pins or a
  charter digest, `pinned-keys.json` and witness given; the nine framework mappings; the
  attestation as JSON. In the browser, with Web Crypto alone, the archive, every member's
  digest, the hash chain, every record's and the manifest's signature, and a sealed
  bundle's seals, shown as a partial check.
- **The sandbox** (`fiduciary.demo.sandbox`, `--sandbox`): each of the six industries built
  in a temporary directory, with three calls to try (one that commits, one with a
  consequence the gateway cannot see, one held for two of three approvers) or one written by
  hand, on SQL, Git, Kubernetes (plan-bound) and FHIR (predicted). There the console holds
  the demo's keys and is the writer, so a vote is recorded at once and a warrant executes.
- **The page, with no console.** `dashboard.html` opened as a file or hosted statically shows
  the recorded run in the same views, the tickets and votes each run recorded, every recorded
  call, and the in-browser bundle checks; it signs nothing and says what needs the console.

### Security

- The console listens on loopback only, answers only its own `Host`, refuses cross-site
  requests (`Sec-Fetch-Site`) except a navigation to its page, takes writes only from its own
  origin with the `X-Fiduciary-Console` header and a JSON (or octet-stream) body, bounds
  bodies (256 KiB; 64 MiB for a bundle), refuses chunked bodies, serves an allowlist of
  files (never keys, configuration or databases), sends CSP, `nosniff` and `DENY` framing,
  and answers failures without their cause.
- SQLite stores and outboxes are read through the backup API; a database with no
  write-ahead log beside it is opened immutable, so no `-wal` or `-shm` file appears.

### Changed

- F9-4 amended: the console page fetches a bundle beside it only when the reader asks, and
  asks a console only when that console served it.

## [1.6.0] - 2026-11-01

The fourth rung: a FHIR server, predicted and verified. Every new member is written only
when it is used, so every mandate, binding, compiled policy, record, bundle and golden
vector written before keeps its bytes. Requires interlock-escrow 1.4.0 and
interlock-agentgov 1.2.0.

### Added

- **A tool's effect declares its undo** (`compensation`, F4-14): a statement in its
  substrate's language that puts the write back (`void <table>`, `restore <table>` on a FHIR
  server). The plan carries it as the effect's compensation. `Tool.insert/update/delete`
  take `compensation=`.
- **The healthcare pack runs on a FHIR server, predicted and verified** (Interlock's
  loopback FHIR R4 server: the real REST protocol, versions, `If-Match`, search, one server
  rule, an `AuditEvent` log). Orders and tasks are `MedicationRequest` and `Task`
  resources; a bed resolves to its chart by search when an order is predicted. Scenario 2:
  the EHR's encounter-stop rule fires on the write, the read-back catches it, and every
  change is put back, the pharmacy task it opened marked `entered-in-error`; the receipt
  says `compensated`. Scenario 4 adds a dose change raced by a nurse's edit: `412`, nothing
  lands. Scenario 6 adds the administrator's `PUT` with the agent's token, the phantom
  scenario 7 reconciles out of the EHR's `AuditEvent` log. Dispenses go to the pharmacy,
  Schedule II held for a warrant.
- **A compensated call** (F4-14): a plan written and put back (a read-back that was not the
  prediction, a write that failed part way) is refused to the agent, told what was put back
  in counts, and shown to the engine's observers with its diff and verdict.
- **Overview**: healthcare is class V on FHIR R4, and the V rung is shipped: all four rungs
  run in `fiduciary demo --domain all`. Two new counts: a commit refused at the measured
  version (`412`), and a write read back and put back. Every pack's audit scenario shows
  its system-of-record reconciliation.

### Changed

- **The SRE and coding-agent reconciliations expect only the receipts of writes to their
  own system** (agentgov 1.2.0's scope): the cluster's audit log no longer counts the
  requests to the cloud API as unobserved, nor the code host's log the requests to CI. Each
  reconciliation records its unobserved and out-of-scope counts.
- The console draws a permitted call stopped at its commit as not written (it was drawn as
  denied by the gateway).

## [1.5.0] - 2026-10-31

Plan-bound infrastructure, analyzers' word, and pinned checker packs.

Mandates for systems that cannot roll back, measured by their own planners. Every new
member is written only when it is used, so every mandate, compiled policy, record, bundle
and golden vector written before keeps its bytes. Requires interlock-escrow 1.3.0 and
interlock-agentgov 1.1.1.

### Added

- **`apply`** (F4-12b, F4-14): a tool binding's effect may be in a plan-bound substrate's
  own language (a Kubernetes manifest or patch, a Terraform input), and a mandate may allow
  `apply` on a table. What it does is judged on the measured diff, so a deletion it measures
  still needs `delete`.
- **`data.sensors`** (F4-12b): the analyzers whose clean result, signed by a key the mandate
  pins and bound to the exact diff the stage measured, every plan needs before it commits
  (Interlock's `SensorAttestation`). The deployment runs them as `[sensors.<name>]` in
  `interlock.toml`.
- **`data.checkers`: pinned checker packs** (F4-12b, F4-16a): a checker of a pack pinned by
  module, version and the digest of its code, as a tool binding is pinned by its document's.
  Compilation, and a verifier's replay of the policy (V4), refuse a pack that is missing,
  another version, or not the code pinned, and say which.
- **`data.acknowledge_unknown`** (F4-12b): values a plan-bound substrate knows only after
  the apply, accepted unmeasured; without one, such a value refuses the plan.
- **The Cloud SRE pack runs on Kubernetes, plan-bound.** Every workload change is staged on
  a cluster (Interlock's loopback API server: the real wire protocol, admission webhooks
  called over HTTP, no credentials): the API server's dry run measures the mutating
  webhook's 60 replicas, and the volume resize that forces the orders database's
  replacement as a deletion `NoDelete` refuses. An admission-policy analyzer
  (`fiduciary.demo.analyzer`), run as a sensor in its own process with its own key, signs
  every diff; the mandate pins its key. Receipts say `plan-bound`. Security groups stay in
  the cloud account, as held requests.
- **Audit exports, reconciled.** The SRE deployment keeps the cluster's audit log
  (`audit/kubernetes-audit.jsonl`) and the coding-agent deployment the code host's log of
  pushes (`audit/github-audit.json`); act 7 reconciles each against the receipts, and
  `agentgov reconcile-actions` runs on either out of the box. Each names its phantom: the
  administrator's `kubectl` with the remediator's shared token, and the teammate's push
  around the queue.

### Changed

- **Every demo deployment opens from the command line**: its `interlock.toml` registers the
  keys that sign its operator log (`[operators.keys]`), and its log is sealed, so
  `fiduciary --config <dir>/fiduciary.toml export-bundle` exits 0, standard and `--sealed`,
  on each.
- The SRE pack's tools are `scale_deployment`, `resize_database_volume` and `open_port`;
  its acts 3 and 6 are the database's forced replacement and an administrator working
  around the agent twice (a break-glass release, and a `kubectl` the bundle cannot see).

## [1.4.0] - 2026-10-31

Any unit, any partition, one customer's bundle.

One engine for any industry's consequences, with no finance, SQL or single-tenant
assumption left in what a mandate can say. Every new member of a document is written only
when it is used, so every mandate, compiled policy, record and `fidb/1` bundle written
before keeps its bytes and its digest, and every golden vector before is unchanged.
Requires interlock-escrow 1.2.0 and interlock-agentgov 1.1.0.

### Added

- **Budgets in any unit** (`budgets.<unit>`, F4-12a): morphine milligram equivalents,
  vCPUs, seats. Each is conserved in an AgentGov ledger of that unit (`[units.<code>]` in
  `fiduciary.toml`: its ledger and the places it counts) and compiles to an Interlock
  `Meter`, held on the lineage's subject before a plan stages and settled from what it
  measurably consumed. Its receipts name each quantity and the ledger transaction that
  settled it. A child's unit envelope is delegated from its parent's in that ledger and
  only ever narrows.
- **Partitions beside the tenant** (`data.partitions`): a plan may be confined to one
  patient, facility, account or supplier, as it is to one tenant. Tool bindings may name
  a partition's label from an argument (`partitions` on an effect or request).
- **Guards on a rise** (`max_rise`) as well as a fall, on a table, a tenant or any
  partition (`per: "partition:<dimension>"`).
- **Caps by unit** (`outbound.caps[].unit_field`): a quantity capped in the unit of
  measure the request names.
- **One agreement over several operations** (`data.agreements[].also`, interlock 1.2.0's
  `CrossEffectAgreement(also=)`): a quantity held to its rows whichever of the sink's
  operations carries it. Healthcare holds the dose of a standard order and of a held
  Schedule II order to the row it writes.
- **Sealed recipient maps** (`outbound.recipients[].sealed`, `.openings`;
  `fiduciary.sealing`). A vendor's root mandate holds one hash over every customer's
  allowed email domains and grants nothing from it; each customer's child opens only its
  own entry. No mandate names another customer's domains.
- **Receipts that name only the plan's own tenant** (`data.receipt_tenants:
  "declared"`), counting the others a refused plan reached (ARC1 1.3
  `summary.other_tenants`).
- **Sealed bundles** (`fiduciary export-bundle --sealed`, format `fidb/2`, F9-5 to F9-7,
  `BUNDLE_FORMAT.md` §3.4). One lineage's records whole, and of every other record its
  place and hash, each held to its place in the chain (V9) and the ledger's anchor of it
  (V10). A customer's bundle names no other customer's records, org keys or domains, and
  still verifies V1 to V12 offline.
- **Sealed logs** (`[service] seal_key`, `fiduciary.seals`). Every record carries a `seal`
  of two HMAC tags saying whose it is, and its anchor memo repeats them. A sealed bundle
  is cut by the seals, and carries only its own lineage's keys. So V10 proves that no
  withheld record is the bundle's own, even one that left no trace elsewhere, and learns
  nothing about whose the others are. `fiduciary verify` with the seal key holds every
  seal to its record. A log is sealed from its first record or never. Logs that are not
  sealed keep their bytes.
- **Measurement in the report.** The verifier notes how many receipts were measured short
  of a transaction (`plan-bound`, `predicted`, `request-only`), and says what each class
  promises.
- **MCP write tools as plans.** A plan binding whose one request goes to a sink of kind
  `mcp` is measured and judged before anything leaves, held for a warrant when its
  operation is `hold = true`, and called by Interlock's relay at most once, dead-lettered
  on an unknown outcome. Its receipt is `request-only`. A held operation is recorded in
  the bundle's sink snapshot (only when held).
- **The domain packs use all of it.** Healthcare: an MME ledger, a facility partition
  beside the chart, a dose rise guard, Schedule II orders held for a warrant on their
  own FHIR operation, and one dose agreement over both order operations. Cloud SRE: a
  vCPU ledger, an account partition beside the environment, a replica rise guard, and
  security-group changes held. Supply chain: a supplier partition beside the warehouse,
  and quantities capped per unit of measure.
  SaaS: a sealed recipient map, a workspace mandate per customer for every scenario,
  receipts naming one org, and each customer's bundle sealed: none names another
  customer's org key or domain. Initech's bundle fails on purpose: it names the support
  engineer's hand-sent invitation in Initech's own workspace.
- `scripts/make_bundles.py --add NAME`: records new golden bundles and keeps the others.
  New vectors: `valid-sealed-child`, `v10-withheld-rewritten`, `v9-withheld-dropped`,
  `v10-own-record-withheld`; two FIDU1 mandates with 1.4's members in
  `vectors/fidu1/valid.json`; and `vectors/fidu1/seals.json`, a sealed log.


## [1.3.0] - 2026-10-30

Coding agents, on Git. Nothing 1.2.0 writes or reads changes: FIDU1, the documents, the
records, `fidb/1`, the checks V1 to V12 and every exit code are as they were.

### Added

- **`fiduciary demo --domain coding-agent`.** A repository maintainer agent works on a real
  Git branch, staged and committed by Interlock 1.1.0's Git substrate, with the
  deployment's SQLite file as the home of its outbox, inbox and windows. Its seven acts:
  - a one-line fix, committed as one commit at the head it was measured on;
  - one edit that the code generator turns into 36 files, refused;
  - an issue's planted instructions to post the PyPI token from a workflow and delete a
    migration test, both refused;
  - a teammate's push while the agent's change is staged: the commit is refused and
    reconciliation names the push;
  - a dependency bump that commits only with CI's signed result for its exact bytes, on
    2 of 3 maintainers, at the head they reviewed;
  - an administrator's hotfix outside any session, which the offline verifier names;
  - both bundles verified offline.
  `--domain all` runs it beside the other five. It runs where the installed Interlock has
  the Git substrate and is left out otherwise.
- **A tool call consumes the facts its writes answer to.** When the lineage's engine
  verifies inbound facts, a plan tool's call consumes every attested fact pending for its
  billing scope whose kind a `data.facts` rule names for a table the binding writes. A
  refused plan consumes none. Before, a session consumed no fact, so a `data.facts` rule
  refused every write it covers. Spec F8-2 is amended.
- **A Git deployment configured in files.** The coding-agent pack writes `interlock.toml`
  (`substrate = "git"`, its `[git]` repository and hook, the control database, the inbox and
  its keys) and `fiduciary.toml`, and its acts stage on the substrate that file configures.
  `fiduciary --config fiduciary.toml verify`, and a service opened from it, work on the
  repository as on a database. Every pack's `fiduciary.toml` says `in_process =
  "tool-calls-only"`, as the demo runs its sessions.
- **The service verifies inbound facts with `interlock.toml`'s `[inbox.keys]`.** Before, a
  service opened from files built its engines with no inbox keys, so no plan could consume
  a fact.
- **`scripts/stack_dashboard.py --keep`** keeps the packs a recorded run holds, after
  verifying their bundles again, and plays only the others. A kept deployment recorded
  without its configuration files gets them back from its own keys, each tool binding
  checked against the pin in its recorded mandate.
- **The dashboard** says what each industry stages on (SQLite or Git), counts commits refused
  at a moved head, and draws the Git pack's race, warrant and hotfix.

### Changed

- **Requires `interlock-escrow` 1.1.0** (it was 1.0.4), for its Git substrate. Every
  CI job now runs the coding-agent pack, and the coverage floors count it.
- **A live act's model calls never take it past `--max-cost`.** Each turn may write only
  the output tokens that what is left of the cap buys, after the turn's input tokens, which
  the API counts (or the request's size bounds, where it cannot count them). Before, an act
  stopped between turns once it reached its cap, so one turn could take it up to $0.146
  past the cap on `claude-opus-5-5`. `--budget` now gives each pack what is left of it, and a
  pack whose cap cannot buy one turn replays.
- **The overview counts an after bundle's ambient finding as a named break-glass act** when
  the act was not an operator's (the Git hotfix), as it counts a labeled release.

## [1.2.0] - 2026-10-29

One engine, any industry. Nothing 1.1.1 writes or reads changes: FIDU1, the documents,
the records, `fidb/1`, the checks V1 to V12 and every exit code are as they were.

### Added

- **Control-framework packs.** The attestation now maps every check to nine frameworks,
  each shipped as a data file: OWASP ASI 2026, SOC 2 and the EU AI Act as before, and
  HIPAA, 21 CFR Part 11, ISO/IEC 27001:2022 Annex A, NIST SP 800-53 Rev. 5, SOX IT general
  controls and GDPR. The new `mappings` member records each pack by the SHA-256 of its
  canonical JSON, with a disclaimer that a mapping supports an assessment and is not a
  certification. A pack never changes a verdict, a finding or an exit code.
- **`fiduciary demo --domain`.** Four domain packs run on the same engine as the fintech
  demo, each a deployment of its own with seven acts: healthcare (medication
  reconciliation), cloud SRE (incident remediation, on a mirror of the cluster), supply
  chain (procurement in an ERP) and multi-tenant SaaS (a CRM AI admin, with one bundle per
  customer). `--domain all` runs fintech and all four behind one dashboard.
- **The dashboard opens on an overview:** six industries and which of them ran, what in
  the engine refused what in this run, the measurement classes, and interlock's recorded
  live acceptance matrix (42 of 42 cases, $1.67 metered). An industry switcher moves
  between the domains, and the control matrix lets the reader pick which frameworks to
  show, with each pack's digest.
- **`--live` works the domain packs too.** A Claude model works each pack's hidden-cascade
  act behind the gateway and the stack, as it works fintech's first act. The act records
  the model's calls, its summarized reasoning, what each call returned, its tokens and
  the spend AgentGov metered. `scripts/stack_dashboard.py --live` adds live pack acts to a
  recorded run and holds them to `--budget` together.
- **SaaS org keys are keyed pseudonyms.** Each customer's org key is an HMAC-SHA-256 of
  its account id under the vendor's pseudonym key. Each per-customer bundle is searched
  for the other customers' records, names, account ids and email domains, and for the
  pseudonym key, and the trust-center panel reports what was found.

### Changed

- Requires `interlock-agentgov` 1.0.1 (it was 1.0.0) and `interlock-escrow` 1.0.4.

## [1.1.1] - 2026-10-25

### Changed

- Requires `interlock-escrow` 1.0.4, whose value guards count each row once.
- **Plain wording in everything a person reads.** `verify-bundle` findings and notes,
  `fiduciary verify` problems, CLI help, the demo's output, the dashboard and the Markdown
  report now say directly what happened, for example "circuit reset without an
  authorizing warrant" and "acted on the lineage's outbox messages without an authorizing
  warrant". Internal requirement codes are no longer shown in output. Exit codes, the
  failing checks and the bundle format are unchanged; the golden bundles' recorded
  findings (`vectors/fidu1/bundles/index.json`) carry the new text.
- The demo's scenarios have descriptive titles ("Order queue: refund and loyalty
  discount", "Relay crash mid-refund (idempotent recovery)", and so on), and the dashboard
  labels the zero-payload check and delivery-log head pinning in plain terms.
- A committed call tells the agent "Outbound request(s) queued for delivery: ...".
- The demo's Cedar engine is warmed up before the first decision, so the first call's
  latency is not the engine's start-up.
- The dashboard explains, next to a live model's reply, that the agent sees only that a
  limit was exceeded, not the trigger that caused it.

## [1.1.0] - 2026-10-24

The whole stack, shown and attested. Nothing 1.0.0 writes or reads changes: FIDU1, the
documents, the records, `fidb/1` and every exit code are as they were.

### Added

- **`fiduciary demo`** (`fiduciary.demo`): the whole stack, offline, in one command. It
  stands a deployment up in a new directory and plays seven acts against it and against an
  input gateway of Cedar policies, every figure measured as it runs (§10.6). It leaves a
  self-contained console (`dashboard.html`), the lineage's bundles before and after a
  break-glass release, their attestation, and a `fiduciary.toml` that the other commands
  open. `--serve` and `--open` show the console; `--live` puts a Claude model in front of
  the first act (`BENCHMARK_API_KEY` only, stopped at `--max-cost`).
- **`fiduciary verify-bundle --json` and `--report <path.html|path.md>`** (F9-4,
  `fiduciary.attest`, `fiduciary.report`): the attestation, for a SIEM or GRC system and
  for people. The verdict and every finding are the checks' own; each check names what it
  proves and the controls its evidence bears on (OWASP ASI 2026, SOC 2, EU AI Act
  Articles 12 and 14). The HTML is one file that makes no network request and prints to
  PDF. Exit codes are unchanged.
- **`MandateEngine.observe(observer)`**: every result a lineage's engine returns, with the
  diff and the verdict a session never passes to an agent, for an operator's view.
- **The `cedar` and `demo` extras** (`cedarpy`), for the demo's gateway on the Cedar engine.
- `SECURITY.md`: how to report a vulnerability, what is stored where, the trust boundaries
  and what is not claimed.

### Changed

- **Signing is in the base install**: `interlock-fiduciary` depends on
  `interlock-agentgov[sign]`, so `pip install interlock-fiduciary && fiduciary demo` (or
  `uvx --from interlock-fiduciary fiduciary demo`) runs with no extra. The `sign` extra
  stays, and resolves as before; `demo` brings the Cedar engine.
- The README opens with the stack and a runnable demo; the release histories it opened
  with are here.

## [1.0.0]

Phase 6 of `docs/FIDUCIARY_SPEC.md` §11: production. Fiduciary runs as a supervised
service, alone or as a cluster on PostgreSQL; every 0.x store upgrades in place; a soak,
a security audit and a mutation pass hold it to its invariants; and FIDU1, the documents,
the record kinds and the bundle format are frozen for 1.x. It uses each upstream fix of
§12 that the installed interlock and AgentGov provide, and runs on interlock 1.0.2 and
AgentGov 1.0.0 without them.

### Added

- **`fiduciary serve [--node N] [--bridge HOST:PORT] [--metrics HOST:PORT]`**
  (`fiduciary.service`), every part a service of interlock's supervisor: restarted after
  an exponential backoff when a step raises, opened again on `SIGHUP`, drained and closed
  in order on `SIGTERM` (§10.7).
  - `writer`: the node's engines, guards, recoveries, runtime log and HTTP bridge. Each
    turn it follows the log, lapses what is due and judges the intake, expires lineages,
    executes the warrants only it can (`accept_repair`, `amend`, `grant`, `decline`:
    F10-12), anchors, and closes idle sessions.
  - `triggers`: `held` and `dead` tickets raised from the outbox (§7.1), never over a
    message a live ticket names.
  - `settler`: interlock's settler over each lineage's chain and receipt issuer, so a
    bundle carries delivery receipts.
  - `executor`: `resolve()`, then every other formed warrant.
  - `GET /healthz` (each part's state, the roles this node leads, the bridge's address)
    and `GET /metrics` (interlock's per-part metrics and fiduciary's: the log, lineages,
    tickets, warrants, intake, sessions), every label from a closed set.
- **The bridge serves agents** (`[bridge.agents.<name>]`, F10-13): the configuration holds
  the SHA-256 of each agent's bearer token; each `initialize` opens a session, `DELETE`
  ends it, an idle one is closed, and each agent has a bound (`max_sessions`).
- **Keys reload in place on `SIGHUP`** (F10-14): the service, runtime and receipt keys
  may not change; the executor's may rotate to a key interlock's operators trust.
- **A cluster** (`[cluster]`, PostgreSQL only; F10-15 to F10-17): each node joins through
  interlock's `ClusterNode`; one leads `fiduciary-writer`, one `fiduciary-executor`.
  A node on standby holds no chain, log claim or listener; one that loses the writer role
  lets go of everything at once. Every connection is named for its node, so a node found
  gone is fenced; the executor confirms its claim before each act. The node that takes the writer role
  from one frozen with the files' claims leads and waits for them, healthy: it asks each
  turn, counts `claims_held`, logs which process holds them, and takes them up within a
  turn of their release (F10-19).
- **`tests/upgrades/`** and `scripts/make_upgrade_stores.py`: a store written by each 0.x
  release's own code (schema versions 1 to 4), opened by this one on SQLite and
  PostgreSQL (F10-18).
- **`scripts/live_stress_test.py`**, the soak, and `tests/test_soak.py`, scaled down.
- **`scripts/security_audit.py`** and `tests/test_security_audit.py` (F9-1, F4-27).
- **`docs/MUTATION_PASS.md`**: 109 mutants over `fidu1`, `refines`, the compiler, quorum
  counting and `verify_bundle`, every one killed or its code removed; and 34 more over the incremental state, quorum
  counting and the upstream wiring, every one killed.
- `fiduciary.toml`: `[serve]`, `[metrics]`, `[cluster]`, `[bridge.agents]`, bridge TLS
  (`tls_cert`, `tls_key`) and `ledger_schema`.
- `.github/workflows/release.yml`: on a `v*` tag, the checks, then the wheel and sdist
  built once and published to PyPI by trusted publishing, each with a PEP 740 attestation
  and a GitHub build provenance attestation, and a GitHub release with a CycloneDX SBOM:
  the pipeline interlock and AgentGov release by.
- **The upstream fixes, where they are installed** (§12). Fiduciary reads which of them
  the installed interlock and AgentGov provide, from their code (`fiduciary._upstream`),
  uses each, and holds to its fallback where it is absent.
  - U-2: an AWS KMS key may be the executor's or the runtime's, its ILOK1 records signed
    at signature version 2 (F3-9). Outbox actions are still chunked to 8 targets. A
    verifier whose Interlock lacks U-2 cannot read a bundle holding such a record (exit
    2), and names the record and the release that reads it.
  - U-3: an extension quote survives a restart and the writer moving to another node
    (F7-14).
  - U-4: an outbox step at a head that moved after its ticket is refused before anything
    is signed; without it, the step is recorded and named (F7-15).
  - U-5: a reset's own entry names its warrant, with no anchor beside it (§7.6). V7 and
    V8 read either form.
  - U-6: a lineage's recovery opens again on a fresh reserve, `{scope}/recovery/ext-N`,
    at a halt after it closed; without it, a lineage recovers once (F8-9).
- A CI job runs the suite against interlock's and AgentGov's `main`.

### Changed

- **Every decision is taken under the writer's lock** (F10-5 amended). The binder's `bind`,
  `issue`, `revoke` and `expire` read their state before it, so two processes could both
  expire a lineage.
- **An idle turn takes no lock.** The intake read every submission ever received, and
  took the lock once per submission, folding the whole log under it; ticket lapses alike.
  This was the Phase 5 contention test's stall under coverage. A turn now reads the
  intake's ids, and the bytes of only the submissions no record answers; the binder's
  expiry takes no lock when no lineage is past its term.
- **Engines follow the log** (`Binder.follow`, §6.4 amended): an amendment bound from the
  command line or another node reaches a running session at its next call, and a
  revocation stops it there.
- **A warranted ticket lapses** when its warrant reaches its `not_after` unexecuted (F7-7
  amended).
- **A turn costs what is due, not what the log holds** (F10-1 amended). A service folded
  the whole log each time a decision asked about it, so a writer's turn and each tool call
  took time in proportion to the log. It now keeps only the folded state, reads only the
  records appended since, checking that they follow what it folded, and folds them into a
  copy (a state once read never changes). Each question is answered from an index: open
  tickets, unexecuted warrants, open intents, each subject's decisions, grants by mandate.
  At 10,000 records a turn takes 0.03 ms and a tool call 0.44 ms, as at 300, and memory
  does not grow with the records read.
- **Documents are read once.** A decision reads a charter, mandate, ticket, approval or
  warrant from the store once, kept by digest (bytes that do not hash to their digest are
  never kept). `verify` reads afresh each time, but reads each document once, as
  `export-bundle` does, and a document's digest is computed once (it is frozen).
- Approvals of a ticket and declines of it are counted by one rule (§7.3), where two
  copies of it were kept in step.
- **V7 explains 0.4's passthrough memo** (`FIDU1 tool <name> <digest>`), as 0.4 wrote it,
  and only before the ledger's first session-named memo (BUNDLE_FORMAT §4.1).
- **The specification is the 1.0.0 contract**: its Conformance section states what 1.0.0
  implements, the status of U-1 to U-6, and each gap's reason; its traceability table
  gives every requirement's status and tests.
- Two checks that could not matter are gone: `refines`' verbatim comparison of rules whose
  identity is the whole rule, and the quorum count's check of a decision row's resolution
  ahead of the approval's own.
- `Development Status :: 5 - Production/Stable`.

### Fixed

- An outbox step with no operator log configured is refused, instead of leaving an open
  intent behind.
- **V4 under an Interlock with U-1** (found running the suite against interlock `main`):
  a bundle exported there held every receipt to canonical set digests, so receipts issued
  before the upgrade failed. Those anchored before the lineage's first canonical receipt
  are now checked by name for their set-bearing checkers, and noted (F6-8 amended).
- **A message written right behind another is answered at once** (`mcp serve`, `mcp
  proxy`; found by the first CI run). The stdio bridge asked `select` whether the pipe
  held a message, then read it through a buffered reader, which reads ahead: two messages
  written together arrived in one read, and the second waited in the buffer, unseen,
  until something else was written. A client sending `notifications/initialized` and
  then `tools/list` could wait for its list for good, and the proxy took an upstream's
  answer written with a notification for silence. Both now read the pipe by its
  descriptor, and wait on it only when no whole line is held.
- **A writer whose opening fails part-way lets go of the chains it opened**, at once,
  where they were held until the engines were collected, and a retry in the same process
  could find its own claims.

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
