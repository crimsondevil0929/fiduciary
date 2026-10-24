# Fiduciary

Signed mandates, quorum approvals and offline compliance proofs for AI agents, over
[AgentGov](https://pypi.org/project/interlock-agentgov/) (the budget ledger) and
[Interlock](https://pypi.org/project/interlock-escrow/) (the side-effect reference
monitor).

AgentGov says whether a scope may spend an amount, and Interlock says whether a plan's
measured consequence satisfies its checkers. Fiduciary says who decided those limits,
on whose authority, and who approved an exception. It records each answer as a signed
document that anyone holding the public keys can verify.

`docs/FIDUCIARY_SPEC.md` is the specification. This is **1.0.0**, the end of its
roadmap; its format, documents, records and bundles are frozen for 1.x:
- Phase 1 (0.1.0) gave the FIDU1 format, the documents, the order on mandates, the log
  and its two stores, and the command line.
- Phase 2 (0.2.0) compiles a mandate into Interlock's checkers and AgentGov's policies,
  and binds it to a deployment.
- Phase 3 (0.3.0) escalates what a mandate does not allow to its principals, and
  executes their decision exactly once.
- Phase 4 (0.4.0) bridges an agent to it, in process or over MCP.
- Phase 5 (0.5.0) exports a lineage as an audit bundle, which anyone verifies offline.
- Phase 6 (1.0.0) runs it as a supervised service, alone or as a cluster on PostgreSQL.

## What 1.0.0 adds

- **`fiduciary serve`** (`fiduciary.service`), the service under interlock's supervisor:
  each part restarted after a backoff when it fails, keys opened again on `SIGHUP`, every
  part drained on `SIGTERM`, and `/healthz` and `/metrics` on a listener of their own. The
  parts follow the log, judge approvals, lapse and expire what is due, raise tickets for
  held and dead messages, execute warrants, receipt deliveries, and serve agents' sessions
  over MCP. See [Running the service](#running-the-service).
- **A cluster**, on PostgreSQL: several nodes over one store and one ledger, one leading the
  writer and one the executor. A node killed hands its roles over at once; one frozen is
  fenced once its session falls silent, and none acts twice. The node that takes the
  writer role from a frozen one leads, asks for its files each turn, and answers
  `/healthz` with 200 (F10-19).
- **A turn costs what is due, not what the log holds.** The service keeps the state it has
  folded, folds only what was appended since, and answers each question from an index: at
  10,000 records a writer's turn takes 0.03 ms and a tool call 0.44 ms, as at 300.
  `verify` reads each document once.
- **The upstream fixes, where they are installed** (§12): with the interlock and AgentGov
  that close them, an AWS KMS executor key, quotes that survive a restart, an outbox act
  refused before signing at a head that moved, a reset that names its warrant, and a
  recovery that opens again. On interlock 1.0.2 and AgentGov 1.0.0, the fallbacks §12
  states.
- **Every 0.x store upgrades in place**, proven on stores each earlier release wrote
  itself (`tests/upgrades/`).
- **Proofs at production scale**: a soak of a cluster under load, forgeries, a killed and a
  frozen node, and edits around the service (`scripts/live_stress_test.py`); a security
  audit that no secret, row, payload, transcript or agent text reaches a hash, a log or a
  bundle (`scripts/security_audit.py`); and a mutation pass (`docs/MUTATION_PASS.md`).
- **The freeze**: what 1.x writes verifies under every later 1.x. The specification's
  Conformance section states what 1.0.0 implements, and what each upstream gap changes
  with the fix installed and without it.

## What 0.5.0 adds

- **The audit bundle** (`fiduciary export-bundle`, `fiduciary.bundle`). One file holds a
  lineage's authority, its acts and the evidence for both:
  - the signed log and its documents;
  - the ledger entries the lineage touched, each with an inclusion proof;
  - its receipts, its interlock records and its escrow chain.

  It carries no row data, payloads, transcripts or secrets. The same lineage over the same
  range always exports to the same bytes. See [Auditing a lineage](#auditing-a-lineage).
- **`fiduciary verify-bundle`**, which checks a bundle with no database and no network:
  - it takes every decision again by the service's rules;
  - it traces every act on the lineage's ledger footprint to the authority that allowed
    it, and names what nothing allowed;
  - it holds each warrant to what the systems recorded, and each instant to the ledger's
    order.

  `docs/BUNDLE_FORMAT.md` specifies the format, and `vectors/fidu1/bundles/` holds golden
  bundles that pin every check and exit code.

## What 0.4.0 adds

- **Sessions** (`fiduciary.bridge`). An agent acts through a session: one mandate version
  in force, one tenant, one trajectory, one billing scope. A call names a binding the
  mandate pins; its arguments are checked against the binding's schema before anything
  is built, and the plan is the binding's own, for the session's tenant. A refused plan
  is repaired and escalated, and a model's calls are metered and recovered from a halt,
  with an extension ticket when the money runs out. See [An agent's
  session](#an-agents-session).
- **The MCP bridge** (`fiduciary mcp serve`, `fiduciary mcp proxy`): over stdio, or
  Streamable HTTP with one bearer token per session; the proxy fronts another MCP server
  and exposes only what the mandate pins. Nothing an agent reaches can approve anything.
- **`fiduciary.Tool`**, for writing a binding in code.
- **The live acceptance suite**, opt-in and cost-capped, with offline references.

## What 0.3.0 adds

- **Tickets** (`fiduciary.escalation`). When an agent reaches a boundary, the service
  raises a signed ticket of typed evidence read from Interlock and AgentGov. The
  boundaries are:
  - a refused plan with a repair;
  - a held or dead message;
  - an undo;
  - an extension quote;
  - a proposed amendment.

  The agent's own words sit apart, as an unverified claim. One ticket covers one piece of
  evidence, and past `escalation.max_open` nothing is raised and the boundary stands.
- **Quorum and warrants.** Principals approve an option, or a narrowing of one. A
  warrant forms when the approvals counted reach the quorum of its class, by principal,
  under the charter's keys, before every deadline. A mandate's autonomous rules may form
  one alone, within a closed vocabulary. Silence refuses: a ticket lapses at its
  deadline, and is declined once no option can still be reached.
- **Execution** (`fiduciary.executor`). A warrant runs in two phases, its digest bound
  into what each system signs:
  - interlock's operator intents;
  - the AgentGov anchor before a reset;
  - the guard's grant and decline records.

  `resolve()` settles what a crash interrupted, from what the systems hold, and guesses
  nothing. The crash tests kill the service at every checkpoint of every resolution, and
  each warrant still executes once.
- **The command line**: `fiduciary tickets list|show`, `fiduciary warrant show`,
  `fiduciary resolve`, and `fiduciary approve` of a ticket.

## What 0.2.0 adds

- **Compilation** (`fiduciary.compiler`). `compile` turns a mandate and its ancestors
  into Interlock checkers, a narrowed sink registry, namespaced rate windows,
  AgentGov's governance and cognitive policies, and a receipt issuer.
  - The compiled digest is the SHA-256 of a canonical description, and every object is
    built from that description.
  - What the deployment cannot enforce fails compilation, and every problem is named.
- **Binding** (`fiduciary.binding`).
  - `bind` delegates the envelope and records the binding.
  - An amendment takes effect at the next plan boundary. Descendants that still refine
    it are rebased; the others are suspended.
  - Revocation and expiry cascade down the lineage. They trip every scope of it in one
    AgentGov transaction, so nothing in flight commits and the relay holds its messages.
- **The command line**: `fiduciary mandate compile`, `bind`, `derive`, `amend`, `revoke`
  and `expire`.

## What 0.1.0 does

- **FIDU1** (`fiduciary.fidu1`). Every document is canonical JSON, read strictly.
  - Its signature covers a domain and the SHA-512 of its bytes, so a signer is never
    asked to sign more than 96 bytes. AWS KMS, whose limit is 4096 bytes, signs any
    document.
  - Signatures are Ed25519 only. HMAC is refused, because whoever verifies an HMAC can
    forge it.
- **The documents** (`fiduciary.documents`):
  - the charter: the root of authority, its principals, keys and quorums;
  - the mandate: every bound one agent scope is delegated;
  - the tool binding: what an agent may call, as a principal reviews it;
  - the approval;
  - the log record.
- **The order** (`fiduciary.refines`). `refines(child, parent)` names every field where a
  child mandate is wider than its parent, so a delegation can only narrow.
- **The log and its stores** (`fiduciary.log`, `fiduciary.sqlite`, `fiduciary.postgres`).
  - Every record is signed with the service key and hash-chained.
  - Every record is anchored into the AgentGov ledger.
  - The database refuses to update or delete the log, the documents or the intake.
  - On PostgreSQL there are three roles: service, intake and audit.
- **Authority** (`fiduciary.authority`).
  - Approvals count by principal, and only those that verify under the charter's keys.
  - Charter versions, key suspensions and mandates take effect only on a quorum.
  - A derived child mandate needs no approval, only to refine its parent.
- **`fiduciary verify`** checks a store against its log, the log against the service key
  and the ledger's anchors, and the projections against the log. It also takes every
  recorded decision again and names any the rules would not have written.

## Install

```
pip install 'interlock-fiduciary[sign]'          # local Ed25519 keys
pip install 'interlock-fiduciary[postgres,sign]' # the PostgreSQL store
pip install 'interlock-fiduciary[kms-aws]'       # keys held by AWS KMS
```

## A charter, a mandate, a derived child

```
fiduciary keygen alice.key        # prints alice's public key: ed25519:<hex>
fiduciary keygen bob.key
fiduciary keygen carol.key
fiduciary keygen service.key

fiduciary charter init --principal alice=ed25519:... --principal bob=ed25519:... \
    --principal carol=ed25519:... --quorums 2,2,1 \
    --ledger <entry_hash of AgentGov entry 1> --label acme --out charter.draft.json
fiduciary sign charter.draft.json --key alice.key --out charter.json
```

`fiduciary.toml`:

```toml
database = "fiduciary.db"            # or postgresql://...
ledger   = "ledger.db"               # the AgentGov ledger every record is anchored into

[charter]
file   = "charter.json"
digest = "<the digest fiduciary sign printed>"

[service]
key = "service.key"                  # or signer = "<name>", a [signers.<name>] table
```

```
fiduciary install
fiduciary charter propose charter.json
fiduciary approve charter.json --principal alice --key alice.key --submit
fiduciary approve charter.json --principal bob --key bob.key --submit
fiduciary charter adopt <digest>

fiduciary mandate draft --charter charter.json --scope agent --funded-from support \
    --label "Support agent" --out mandate.draft.json   # every bound at its tightest
# edit the bounds, then:
fiduciary mandate check mandate.draft.json --charter charter.json
fiduciary sign mandate.draft.json --key bob.key --out mandate.json
fiduciary mandate propose mandate.json
fiduciary approve mandate.json --principal alice --key alice.key --submit
fiduciary approve mandate.json --principal carol --key carol.key --submit
fiduciary mandate issue <digest>

fiduciary verify
```

To bind a mandate, the configuration also names the deployment it binds to:

```toml
interlock = "interlock.toml"         # its tables and sinks

[binding]
tools      = ["refund.tool.json"]    # the tool bindings it runs: files, or module:name
directives = ["stop-retrying.txt"]   # directive texts, by the SHA-256 of their bytes
chains     = "chains"                # each lineage's escrow chain
```

The charter's `--ledger` must be that ledger's first entry.

```
fiduciary mandate compile <digest>   # the compiled policy, and its digest on stderr
fiduciary mandate bind <digest>      # delegates the envelope to the mandate's scope

fiduciary mandate draft --charter charter.json --parent mandate.json \
    --scope agent/triage --label "Triage" --out child.draft.json
fiduciary mandate derive child.draft.json --bind   # signed by the service, no approvals

# an amendment, approved as an issue is:
fiduciary mandate amend mandate-v2.json   # rebases or suspends children, rebinds
# a revocation, on --revoke approvals:
fiduciary approve mandate-v2.json --principal carol --key carol.key --revoke --submit
fiduciary mandate revoke <digest>
fiduciary mandate expire                  # every lineage past its term
```

`fiduciary approve` prints the document it signs and asks before signing it (G8). A
principal signs on their own machine, and needs no database credential beyond the intake
role.

An exception is escalated as a ticket, and decided on the principals' own machines:

```
fiduciary tickets list                    # open tickets
fiduciary tickets show <ticket_id>        # rendered from its signed bytes (F7-3)
fiduciary approve ticket.json --charter charter.json --principal alice --key alice.key \
    --resolution grant --submit           # an option, by action or digest
fiduciary approve ticket.json --charter charter.json --principal bob --key bob.key \
    --resolution narrowed.json --submit   # or a narrowing of one, read from a file
fiduciary warrant show <warrant_id>
fiduciary resolve                         # settle executions a crash interrupted
```

The executor signs interlock's operator records. Its key and the runtime records key may be
AWS KMS keys only with an Interlock that signs ILOK1 records at signature version 2 (U-2,
`docs/FIDUCIARY_SPEC.md` §12); fiduciary reads which Interlock it runs on, and refuses one
otherwise:

```toml
[executor]
key = "executor.key"                 # an [operators.keys] key of interlock.toml
log = "operators.jsonl"              # interlock's operator log

[runtime]
records = "runtime.jsonl"            # the guard's ILOK1 log of quotes and grants
key     = "runtime.key"
```

## An agent's session

A session is one mandate version in force, one tenant, one trajectory and one billing
scope, recorded before its first call. With the deployment above bound, and
`in_process = "tool-calls-only"` in `fiduciary.toml` (an agent that runs code it writes is
bridged out of process instead):

```python
from fiduciary import Fiduciary

fid = Fiduciary.from_config("fiduciary.toml")
with fid.session("support-agent", tenant="acme") as session:
    tools = session.tool_definitions()  # the pinned bindings, as the Messages API takes them
    result = session.call(
        "refund_order", {"order_id": 7, "amount": "12.50", "payment_intent": "pi_0000000007"}
    )
    print(result.outcome)  # committed; or refused, with interlock's sanitized feedback
fid.close()
```

`session.govern(anthropic.Anthropic())` meters the model's own calls on the session's
billing scope, under the mandate's breaker, ceilings and model allowlist. A halt is
answered by interlock's recovery runtime, and a spent reserve raises an `extension` ticket
for the principals. Out of process, the same session speaks MCP:

```
fiduciary mcp serve --mandate support-agent --tenant acme                        # stdio
fiduciary mcp serve --mandate support-agent --tenant acme --http 127.0.0.1:8765 \
    --token-file session.token                                                  # Streamable HTTP
fiduciary mcp proxy --mandate support-agent --tenant acme -- <upstream MCP server command>
```

The agent sees the bindings the mandate pins and two built-ins (`fiduciary_request`, to
ask the principals for an undo or an amendment, and `fiduciary_ticket_status`), and no way
to approve anything. A binding can be written in code with `fiduciary.Tool`, and pinned
with `fiduciary tool digest module:name`.

## Auditing a lineage

The operator exports a lineage. The ledger's checkpoint key signs the checkpoint its
inclusion proofs verify against:

```
fiduciary export-bundle --mandate support-agent --ledger-key ledger.key --out support-agent.fidb
```

The auditor verifies it anywhere, with the charter digest and the deployment's public keys
obtained from outside the bundle:

```
fiduciary verify-bundle support-agent.fidb --charter <digest> --keys keys.json
```

`verify-bundle` prints V1 to V12 and every finding. It exits 0 on a pass. Otherwise it
exits with the first failing check's code:

| Exit | Failure |
|---|---|
| 1 | a usage error |
| 2 | a file that is no bundle |
| 3 | malformed |
| 4 | a bad signature |
| 5 | authority |
| 6 | monotonicity or policy |
| 7 | an ambient act no authority explains |
| 8 | missing evidence |
| 9 | revocation or time |

An act by a key the charter declares break-glass is still named, and labeled so.

## Running the service

`fiduciary serve` runs everything above as one service, from the same `fiduciary.toml`,
with a few tables more:

```toml
[serve]
every_seconds = 1                 # a turn of the writer and the executor

[metrics]
listen = "127.0.0.1:9465"         # GET /metrics and /healthz

[bridge]
http = "127.0.0.1:8765"           # tls_cert and tls_key, beyond loopback

[bridge.agents.support-desk]      # an agent's sessions: initialize opens one
mandate = "support-agent"
tenant = "acme"
token_sha256 = "<sha256 of the agent's bearer token>"
```

```
fiduciary serve
kill -HUP <pid>                   # open every key again, in place
```

Several services over one PostgreSQL store and ledger are a cluster, each started with its
own node name:

```toml
[cluster]
heartbeat_seconds = 1
session_timeout_seconds = 10
```

```
fiduciary serve --node fiduciary-0
fiduciary serve --node fiduciary-1
```

One node leads the writer: it holds the engines and serves the agents. One leads the
executor. The others stand by, and `/healthz` says which roles each holds.

The writer's files (the escrow chains, the runtime, receipt and operator logs) are on
storage every node reaches and whose locks every node honors: one host's filesystem, or a
network filesystem whose locks hold across hosts. A writer that freezes keeps their claims
until it thaws or dies, and the node that took its role waits, healthy, until then. Have
your supervisor kill a node whose `/healthz` does not answer: that bounds the wait
(`docs/FIDUCIARY_SPEC.md` F10-19).

## Tests

```
uv sync
uv run pytest
```

The PostgreSQL store's tests run against a database whose role may create schemas and
roles:

```
FIDUCIARY_TEST_POSTGRES_DSN=postgresql://user:pw@localhost:5432/postgres \
FIDUCIARY_REQUIRE_POSTGRES=1 uv run pytest
```

The crash tests (`tests/test_crash_recovery.py`) run the service as a child process and
kill it at every checkpoint. The contention test (`tests/test_contention.py`) runs two
service processes over an AgentGov ledger shared through PostgreSQL, so it needs the
DSN even when the store under test is SQLite.

The service and cluster tests (`tests/test_service.py`, `tests/test_cluster.py`) run
`fiduciary serve` as processes: a node is killed, and one is frozen and thawed. The
upgrade tests (`tests/test_upgrade.py`) open stores each 0.x release wrote;
`scripts/make_upgrade_stores.py` records them anew from each release's own code.

Fiduciary uses each upstream fix of `docs/FIDUCIARY_SPEC.md` §12 where the installed
interlock and AgentGov have it, and holds to its fallback where not; the tests of each side
skip on the other, and the suite passes on both. To run it against their development
trees: `uv pip install -e ../interlock -e ../agentgov`.

The soak runs a cluster for minutes against live PostgreSQL, then holds the database, the
ledger and the logs to every invariant; `tests/test_soak.py` runs it for forty seconds:

```
uv run python scripts/live_stress_test.py --dsn postgresql://user:pw@localhost:5432/postgres --minutes 5
```

The security audit plants a canary for every kind of sensitive value, records the input of
every hash and signature, crawls every column, file and bundle member, and exits 0 only
when nothing leaked:

```
uv run python scripts/security_audit.py --dsn postgresql://user:pw@localhost:5432/postgres
```

`scripts/live_acceptance.py` puts a Claude model in front of a session with business
requests. It spends money only with `BENCHMARK_API_KEY` set, within each session's envelope
and `--max-cost`; `--offline` replays its reference proposals, as the tests do:

```
uv run python scripts/live_acceptance.py --offline
BENCHMARK_API_KEY=... uv run --with anthropic python scripts/live_acceptance.py --out live-run
```

`scripts/ilok1_sizes.py --check` measures the ILOK1 records the executor has interlock
sign, and requires the spec's table (§3.5) to match.

`vectors/fidu1/` holds the golden vectors: valid documents with their digests and
signing inputs, a chain of records, and invalid documents, each with the error it must
fail with. `scripts/make_vectors.py` regenerates them, and the tests require them
unchanged. An implementation in another language that reproduces them reads and signs
FIDU1 as this one does.

`vectors/fidu1/bundles/` holds golden audit bundles, each with the verdict it must reach
(`index.json`). They are recorded rather than generated, because a bundle is stamped by the
wall clock; `scripts/make_bundles.py` records them anew.

## License

Apache-2.0.
