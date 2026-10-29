# Fiduciary

Fiduciary adds signed authorization and offline audit to AI agents that change real data.
It works with [AgentGov](https://github.com/crimsondevil0929/agentgov) (spend limits) and
[Interlock](https://github.com/crimsondevil0929/interlock) (transactional checks on what
an agent's database writes actually changed). An MCP or policy gateway decides whether an
agent may call a tool. This stack decides whether the result of that call may commit, and
produces evidence that an auditor can verify without trusting the agent or the operator.

Fiduciary records who set each limit and who approved each exception, as signed
documents. Anyone with the public keys can verify them offline.

## One engine, any industry

Fintech is where the demo starts, not what the engine is. The same checks, budgets,
quorum warrants and offline proof apply to any agent action that has a consequence, a
limit, or someone who must approve it. Only the nouns change:

| | Fintech | Healthcare | Cloud SRE | Supply chain | Multi-tenant SaaS | Coding agents |
|---|---|---|---|---|---|---|
| **What the stack measures** | orders and refunds | orders on a patient's chart | resource changes, in a mirror of the cluster | purchase-order lines, stock levels | customer records, role grants | a diff of files |
| **What the gateway let through, and the stack stopped** | a dispute flag whose trigger reached 4 tenants | a discontinue that stopped a whole encounter (31 rows); an order for the patient in the next bed | 6 replicas that a mutating policy made 60; a resize that replaced a database | a reorder point that exploded into 400 PO lines; a remittance note that rewrote a bank account | a filter that reached 4 customers; a role that carried admin rights | an edit to a workflow file |
| **Partition** | tenant | chart | environment | warehouse | customer org | repository |
| **Approvers** | CFO, head of ops, security | chief pharmacist, CMIO, privacy officer | on-call SRE, service owner, security | procurement lead, AP manager, treasury | platform security, product owner, trust and safety | maintainers |
| **Frameworks mapped** | SOC 2, OWASP agentic, EU AI Act | HIPAA, 21 CFR Part 11 | NIST 800-53, ISO 27001 | SOX IT general controls | SOC 2, ISO 27001, GDPR | |
| **Run it** | `fiduciary demo` | `--domain healthcare` | `--domain sre` | `--domain supply-chain` | `--domain saas` | not built yet |

`fiduciary demo --domain all` runs the first five behind one dashboard in a few seconds.
Each industry is a deployment of its own on the real stack, with its own database
triggers, mandate, principals and break-glass key, and each ends with its audit bundles
verified offline. The dashboard opens on an overview that counts, from what was measured,
which check stopped what in each industry.

**With live models.** Interlock's [customer acceptance
matrix](https://github.com/crimsondevil0929/interlock/blob/main/docs/CUSTOMER_ACCEPTANCE_MATRIX.md)
put live Claude models (`claude-opus-5-5` and `claude-haiku-5-5`) in front of four
unrelated business domains on one engine: accounts payable, cluster operations, SaaS
administration and warehouse fulfilment. **42 of 42 live cases passed**, for $1.67 of
model spend metered by AgentGov (recorded 2026-10-11). One function builds every
domain's checks; only table names, column names and limits differ.

What this does not claim:

- The cloud SRE pack measures a mirror of the cluster kept in the database, not the
  cluster itself. Measuring a live cluster needs a Kubernetes or Terraform adapter, which
  is not built yet. Coding agents need a Git adapter, which is next.
- Without `--live`, the four domain packs replay a fixed sequence of calls. With it, a
  Claude model works each pack's second scenario, the hidden cascade, and the other
  scenarios still replay.
- A control-framework mapping shows which evidence supports an assessment. It is not a
  certification, and nothing here is "HIPAA compliant" or "FedRAMP ready" on its own.
- A per-customer bundle holds none of the other customers' records, names or account
  ids, but it is not anonymous. It names the other customers' org keys, because it
  carries the deployment's whole log. The vendor-wide mandate maps each org key to that
  customer's email domain, for the outbound recipient check, so a reader can work out
  who the other customers are. Each org key is a keyed hash (HMAC-SHA-256) of the
  account id, so the key alone gives nothing away; the domain does. A bundle cut to one
  customer's part of the log is not built yet.

## Architecture

```
agent ──▶ MCP / policy gateway ──▶ ┌────────────────────────────────────────────┐ ──▶ database
          identity, input checks   │ Fiduciary  who may decide: signed          │     Stripe
          (Cedar, OPA, allow-lists)│            mandates, quorum warrants,      │     mail
                                   │            an audit bundle, V1 to V12      │
                                   │ Interlock  what changed: stage, measure,   │
                                   │            commit or roll back; exactly-   │
                                   │            once outbox, signed inbox       │
                                   │ AgentGov   what it costs: holds across the │
                                   │            delegation tree and the fleet   │
                                   └────────────────────────────────────────────┘
```

| Layer | Package | What it checks |
|---|---|---|
| [AgentGov](https://github.com/crimsondevil0929/agentgov) | [`interlock-agentgov`](https://pypi.org/project/interlock-agentgov/) | Can this scope spend this amount? Limits apply across the whole delegation tree and every session, and in-flight calls hold funds until they settle. |
| [Interlock](https://github.com/crimsondevil0929/interlock) | [`interlock-escrow`](https://pypi.org/project/interlock-escrow/) | Does the change this transaction actually made, read back from the database, satisfy policy? It commits or rolls back that same transaction, and delivers outbound API calls exactly once. |
| **Fiduciary** | [`interlock-fiduciary`](https://pypi.org/project/interlock-fiduciary/) | Who set those limits, and who approved each exception? Signed mandates, quorum-approved warrants, and an audit bundle anyone can verify offline. |

Use the stack behind a gateway, not instead of one. Gateways handle prompt scanning,
identity and agent inventory; this stack checks consequences and records the evidence.

## Quickstart

```
pip install interlock-fiduciary
fiduciary demo --open
```

Without installing anything: `uvx --from interlock-fiduciary fiduciary demo --open`. The
demo's gateway uses a built-in evaluator of its Cedar policies; install the `demo` extra
(`pip install 'interlock-fiduciary[demo]'`) to use the Cedar engine itself. From a clone,
`uv sync && uv run fiduciary demo --open` includes it.

`fiduciary demo` creates a deployment in a new directory: an AgentGov ledger, a SQLite back
office with 200 orders across four tenants, a Fiduciary store, a charter with three
principals, and a bound mandate. Keys are generated for each run. It then runs seven
scenarios, sending the same tool calls to an input gateway (Cedar policies) and to the
stack:

```
Act 1  Order queue: refund and loyalty discount
  gateway: 3 of 3 calls permitted. stack: 1 committed, 1 rolled back
  order 9 total: 1000.00 -> 100.00 (90% drop, limit 30%); rolled back
Act 2  Batch settlement cascade (cross-tenant trigger)
  gateway: PERMIT. stack: 50 rows across 4 tenants (limit 20 rows, 1 tenant); rolled back, back office unchanged
Act 3  Sub-agent fan-out across 50 sessions
  gateway: 50 of 50 sessions permitted ($4,500.00; $100.00 per-session limit). stack: 5 committed ($450.00), 45 blocked by the $500/24h lineage window
  derive support-agent/returns (refund cap $100): derived and bound
  derive support-agent/billing (refund cap $50): derived and bound
  derive support-agent/vip (refund cap $500): refused, exceeds its parent: outbound.caps[stripe.refunds.create.amount.request].maximum.usd: is 500, above the parent's 200
Act 4  Relay crash mid-refund (idempotent recovery)
  relay-a crashed after Stripe processed the refund; relay-b retried with the same idempotency key: 2 requests, 1 refund
  webhook: signed event accepted (200), forged event rejected (401); 6 messages delivered
Act 5  2-of-3 principal quorum approval
  ticket 0f908f93 (held): 4 approvals submitted, 2 principals counted, quorum 2 of 3; warrant executed; message pending -> delivered
Act 6  Unauthorized DBA override (break-glass)
  night-shift key released message f2114609 and reset the breaker without a warrant; message delivered
Act 7  Offline third-party audit verification
  before.fidb: PASS, 0 findings, 0 network calls, 0 planted values found
  after.fidb: FAIL (exit 7), 2 findings, 0 network calls, 0 planted values found
    V7 ambient: ledger entry 221 (circuit_reset) on support-agent: circuit reset without an authorizing warrant
    V7 ambient [break-glass]: operator record 3 (release) by key f66328a7c3defa1c acted on the lineage's outbox messages without an authorizing warrant
```

All figures are measured during the run: row changes from the open transaction,
deliveries from the outbox log, holds from the ledger, and verdicts from `verify-bundle`
with networking disabled. The run takes a few seconds and writes:

- `dashboard.html`: a single file that makes no network requests and prints to PDF. It
  shows the verdict, the zero-payload check, the seven scenarios side by side, the mandate
  lineage with ledger usage, every ticket, approval, warrant and operator action, and the
  V1–V12 checks mapped to control frameworks: the OWASP Top 10 for Agentic Applications
  (2026), SOC 2, the EU AI Act, HIPAA, 21 CFR Part 11, ISO/IEC 27001:2022, NIST SP 800-53
  Rev. 5, SOX IT general controls and GDPR. Each mapping ships as a data file, and the
  attestation records which ones it applied by their SHA-256.
- `before.fidb` and `after.fidb`: audit bundles exported before and after the DBA override,
  with `pinned-keys.json` and `charter.digest` for verifying them yourself.
- `attestation.json` and `report.md`.
- The deployment and its `fiduciary.toml`, so `fiduciary --config <dir>/fiduciary.toml
  verify`, `tickets list --all` and the other commands work against it. This is the quickest
  way to a working charter, mandate and keys; setting them up by hand takes the steps in
  [A charter, a mandate, a derived child](#a-charter-a-mandate-a-derived-child).

`--out DIR` sets the directory, `--report PATH` also writes the dashboard or a Markdown
report elsewhere, and `--serve` serves the dashboard on 127.0.0.1. Added in 1.1.0.
`--domain` picks the industry (see [One engine, any industry](#one-engine-any-industry));
with `all`, each domain pack is written to a subdirectory named for it.

### Scenario 1 with a live model

`--live` runs scenario 1 with a Claude model. Its calls go through the session, are
metered against the mandate's budget, and stop at `--max-cost`. The API key comes from
`BENCHMARK_API_KEY` (environment or `--env-file`); `ANTHROPIC_API_KEY` is never read.
Results from the run on 2026-10-25 (UTC), with cedarpy 4.12.2:

| | |
|---|---|
| Model | `claude-opus-5-5`, effort `medium`: 3 turns, 9.4 s |
| Tokens and cost | 5,757 input and 715 output tokens: **$0.039328**, metered by AgentGov against the session's budget |
| Calls made | `refund_order` (order 5, `"12.50"`), `apply_discount` (order 9, `10`), `lookup_order` (9), `lookup_order` (5) |
| Gateway | Cedar permitted all four |
| Stack | The refund committed. The discount was rolled back: the repricing trigger dropped order 9 from 1,000.00 to 100.00 (90%), over the 30% limit. The back office was left unchanged. |
| Model's reply | It confirmed the refund, reported the blocked discount, said the 30% rule looked wrong for a 10% discount, and asked for the rule to be checked before retrying. |

The model's calls were valid and within policy. The problem was what the database did with
them, which no input check can see; the agent's feedback says only that the plan lowered
order totals by more than 30%, so from its side the limit looks wrong. Live results are
not reproducible because model behavior varies. The default mode replays a fixed call
sequence, and CI runs that.

With `--live`, the model also works each domain pack's second scenario, the hidden
cascade, from a plain request ("discontinue that order", "scale the api from 4 to 6").
`scripts/stack_dashboard.py --live` adds these to a recorded run and holds them to
`--budget` together. Results from the run on 2026-10-29 (UTC), `claude-opus-5-5` at
effort `medium`, all metered by AgentGov:

| Industry | Call the model made | What the stack measured, and refused | Turns, tokens, cost |
|---|---|---|---|
| Healthcare | `discontinue_order` (order 10) | 31 rows across the encounter (`blast_radius`) | 2 turns, 3,033 in / 267 out, $0.017472 |
| Cloud SRE | `scale_deployment` (api, 6 replicas) | 57 rows (`blast_radius`, `cross_effect_agreement`, the vCPU window) | 2 turns, 3,089 in / 524 out, $0.022836 |
| Supply chain | `set_reorder_point` (A-1001, 500) | 401 rows (`blast_radius`, `cross_effect_agreement`) | 2 turns, 3,533 in / 337 out, $0.020872 |
| Multi-tenant SaaS | `bulk_reassign` (lost deals to one rep) | 16 rows, across customers (`tenant_isolation`, `blast_radius`) | 2 turns, 3,155 in / 381 out, $0.020240 |

The gateway permitted every call. In each case, the model read the refusal, did not try
to work around it, and told the operator that nothing had changed and why. The four cost
$0.081420 together.

## Features

- **Signed documents (FIDU1).** Canonical JSON signed with Ed25519 (HMAC is not
  accepted): the charter (principals, keys, quorums, break-glass keys), the mandate (every
  limit delegated to one agent scope), the tool binding, the approval, the ticket and the
  warrant.
- **Delegation can only narrow.** `refines(child, parent)` lists every field where a
  child mandate is wider than its parent, and such a child is refused.
- **Compilation and binding.** A mandate compiles into Interlock checkers, sinks and rate
  windows, and AgentGov governance and loop-detection policies. A mandate the deployment
  cannot enforce fails to compile.
- **Escalation and warrants.** An action the mandate does not allow becomes a signed
  ticket with typed evidence. A warrant forms when enough distinct principals approve the
  exact option, before the deadline; it is bound to the ticket digest and the state the
  systems recorded, and it executes once through Interlock's operators and AgentGov's
  ledger.
- **Sessions and MCP.** An agent acts through a session, in process or over MCP. Nothing
  an agent can reach is able to approve anything.
- **Audit bundle.** A lineage exports to one file with no row data, payloads, prompts or
  secrets. `verify-bundle` checks it offline (V1 to V12) and re-evaluates every recorded
  decision.
- **Service and cluster** on PostgreSQL, with crash-safe recovery.

`docs/FIDUCIARY_SPEC.md` is the specification, stable across 1.x. `CHANGELOG.md` lists
each release. [`SECURITY.md`](SECURITY.md) describes what is stored where and what is out
of scope.

## Install

```
pip install interlock-fiduciary                  # local Ed25519 keys included
pip install 'interlock-fiduciary[postgres]'      # the PostgreSQL store
pip install 'interlock-fiduciary[kms-aws]'       # keys held by AWS KMS
pip install 'interlock-fiduciary[demo]'          # fiduciary demo's gateway on the Cedar engine
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

`fiduciary approve` prints the document it is about to sign and asks for confirmation. A
principal signs on their own machine, and needs no database credential beyond the intake
role.

An exception is escalated as a ticket, and decided on the principals' own machines:

```
fiduciary tickets list                    # open tickets
fiduciary tickets show <ticket_id>        # rendered from its signed bytes
fiduciary approve ticket.json --charter charter.json --principal alice --key alice.key \
    --resolution grant --submit           # an option, by action or digest
fiduciary approve ticket.json --charter charter.json --principal bob --key bob.key \
    --resolution narrowed.json --submit   # or a narrowing of one, read from a file
fiduciary warrant show <warrant_id>
fiduciary resolve                         # settle executions a crash interrupted
```

The executor signs Interlock's operator records. Its key and the runtime records key can
be AWS KMS keys only with Interlock 1.0.3 or later, which signs ILOK1 records in a form KMS
accepts. Fiduciary checks the installed Interlock and refuses a KMS key on an older one:

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

The operator exports a lineage. The ledger's checkpoint key signs the checkpoint that the
bundle's inclusion proofs are checked against:

```
fiduciary export-bundle --mandate support-agent --ledger-key ledger.key --out support-agent.fidb
```

The auditor verifies it anywhere, using the charter digest and the deployment's public
keys, obtained independently of the bundle:

```
fiduciary verify-bundle support-agent.fidb --charter <digest> --keys keys.json
```

`verify-bundle` prints the result of each check (V1 to V12) and every finding. `--json`
prints the attestation instead, for a SIEM or GRC system: the verdict, each check with its
findings and the controls it supports, and a summary of the bundle (mandates and their
tree, tickets, approvals, warrants, operator actions and ledger usage). `--report
audit.html` (or `.md`) also writes a report; the HTML version is a single file that prints
to PDF. Neither option changes the exit code. It exits 0 when every check passes, and
otherwise with the code of the first failing check:

| Exit | Failure |
|---|---|
| 1 | a usage error |
| 2 | the file is not a readable bundle |
| 3 | malformed |
| 4 | a bad signature |
| 5 | authority |
| 6 | monotonicity or policy |
| 7 | an action with no authorizing mandate or warrant |
| 8 | missing evidence |
| 9 | revocation or time |

An action signed with a key the charter declares as break-glass is still reported, and labeled `[break-glass]`.

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

The writer's files (escrow chains, and the runtime, receipt and operator logs) must be on
storage that every node can reach and whose file locks every node respects: one host's
filesystem, or a network filesystem with cross-host locking. A writer that freezes keeps
its locks until it resumes or exits, and the node that took over its role waits, healthy,
until then. Configure your supervisor to kill a node whose `/healthz` stops responding;
that bounds the wait (see `docs/FIDUCIARY_SPEC.md`, F10-19).

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

Fiduciary uses the newer Interlock and AgentGov features listed in
`docs/FIDUCIARY_SPEC.md` §12 when the installed versions have them, and falls back
otherwise. Tests for each path skip on the other, and the suite passes on both. To test
against development checkouts: `uv pip install -e ../interlock -e ../agentgov`.

The soak test runs a cluster for several minutes against PostgreSQL, then checks the
database, the ledger and the logs against every invariant. `tests/test_soak.py` runs it
for forty seconds:

```
uv run python scripts/live_stress_test.py --dsn postgresql://user:pw@localhost:5432/postgres --minutes 5
```

The security audit plants a canary for every kind of sensitive value, records the input of
every hash and signature, crawls every column, file and bundle member, and exits 0 only
when nothing leaked:

```
uv run python scripts/security_audit.py --dsn postgresql://user:pw@localhost:5432/postgres
```

`scripts/live_acceptance.py` runs a Claude model against a session with business
requests. It spends money only when `BENCHMARK_API_KEY` is set, within each session's
budget and `--max-cost`. `--offline` replays fixed reference calls, as the tests do:

```
uv run python scripts/live_acceptance.py --offline
BENCHMARK_API_KEY=... uv run --with anthropic python scripts/live_acceptance.py --out live-run
```

`scripts/ilok1_sizes.py --check` measures the ILOK1 records the executor asks Interlock to
sign, and checks them against the table in the spec (§3.5).

`vectors/fidu1/` holds the golden vectors: valid documents with their digests and
signing inputs, a chain of records, and invalid documents, each with the error it must
fail with. `scripts/make_vectors.py` regenerates them, and the tests require them
unchanged. An implementation in another language that reproduces them reads and signs
FIDU1 as this one does.

`vectors/fidu1/bundles/` holds golden audit bundles, each with the verdict it must reach
(`index.json`). They are recorded rather than generated, because a bundle is stamped by the
wall clock; `scripts/make_bundles.py` records them anew.

## License

Apache-2.0: see [LICENSE](LICENSE). To report a vulnerability, see [SECURITY.md](SECURITY.md).
