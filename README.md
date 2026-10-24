# Fiduciary

**Consequence control for AI agents, under the gateway.** Gateways decide whether an agent
may make a call. This stack decides whether the call's consequence may stand, and proves it
to an auditor who trusts neither the agent nor the operator.

Fiduciary is the top of three layers. It records who set the limits the lower two enforce,
and who approved each exception, as signed documents that anyone holding the public keys
verifies offline.

## The three-layer stack

```
agent ──▶ MCP / policy gateway ──▶ ┌────────────────────────────────────────────┐ ──▶ database
          identity, input checks   │ Fiduciary  who may decide: signed          │     Stripe
          (Cedar, OPA, allow-lists)│            mandates, quorum warrants,      │     mail
                                   │            an audit bundle, V1 to V12      │
                                   │ Interlock  what it changed: stage, measure,│
                                   │            commit or roll back; exactly-   │
                                   │            once outbox, signed inbox       │
                                   │ AgentGov   what it costs: holds across the │
                                   │            delegation tree and the fleet   │
                                   └────────────────────────────────────────────┘
```

| Layer | Package | The question it answers |
|---|---|---|
| [AgentGov](https://github.com/crimsondevil0929/agentgov) | [`interlock-agentgov`](https://pypi.org/project/interlock-agentgov/) | May this scope spend this amount, counted across its whole delegation tree and every session, with in-flight calls held? |
| [Interlock](https://github.com/crimsondevil0929/interlock) | [`interlock-escrow`](https://pypi.org/project/interlock-escrow/) | Does what this transaction actually changed, read back from the database, satisfy policy? Commit it or roll it back, and deliver its outside calls exactly once. |
| **Fiduciary** | [`interlock-fiduciary`](https://pypi.org/project/interlock-fiduciary/) | Who set those limits, and who approved each exception? Signed mandates, quorum warrants, and an audit bundle anyone verifies offline. |

The stack sits under a gateway, not in place of one: a gateway scans prompts, checks
identities and inventories agents; this stack judges consequences and proves the judgment.

## Sixty seconds: the whole stack, offline

```
git clone https://github.com/crimsondevil0929/fiduciary && cd fiduciary
uv sync
uv run fiduciary demo --open
```

`fiduciary demo` stands up a deployment in a fresh directory (an AgentGov ledger,
Interlock's back office of 200 orders across four tenants, fiduciary's store, a charter of
three principals and a bound mandate, every key made for the run) and plays seven acts.
Each act puts the same calls in front of an input gateway, real Cedar policies evaluated by
`cedarpy` (the `cedar` extra; a built-in evaluator of the same policies otherwise), and in
front of the stack:

```
Act 1  An everyday queue
  gateway permitted 3 of 3 call(s); the stack committed 1 and refused 1
  order 9: total 1000.00 -> 100.00, measured, then rolled back
Act 2  One flag, a whole settlement batch
  gateway PERMIT; the stage measured 50 rows of 4 tenants; ROLLED BACK, the back office byte-identical: True
Act 3  Fifty sub-agents, one budget
  50 sessions: the gateway permitted 50 (each within its own $100.00), $4,500.00 in all; the lineage's window committed 5 ($450.00) and refused 45
  derive support-agent/returns (refund cap $100): derived and bound
  derive support-agent/billing (refund cap $50): derived and bound
  derive support-agent/vip (refund cap $500): refused, wider than its parent: outbound.caps[stripe.refunds.create.amount.request].maximum.usd: is 500, above the parent's 200
Act 4  The relay dies mid-call
  relay-a died after Stripe answered; relay-b recorded the call lost and called again with the same key: Stripe saw 2 requests, made 1 refund
  Stripe's webhook: 200 (verified, bound, attested); a forged one: 401; 6 message(s) delivered in all
Act 5  The principals decide
  ticket bcc47068 (held): 4 approvals submitted, warrant at 2 of 3; executed: execution.intent, execution.applied; message pending -> delivered
Act 6  The administrator's shortcut
  the night shift released message 18d23c0d and reset the breaker by hand; the relay delivered it: delivered
Act 7  The auditor
  before.fidb: PASS (0 finding(s)); sockets attempted 0; planted values found 0
  after.fidb: FAIL: exit 7 (2 finding(s)); sockets attempted 0; planted values found 0
    V7 ambient: ledger entry 221 (circuit_reset) on support-agent: a reset no warrant of the lineage names
    V7 ambient [break-glass]: operator record 3 (release) by key be8d927e6fe8ed05 acts on the lineage's messages under no warrant of it
```

Every figure is measured as the run goes: rows read back from the stage, deliveries from
the outbox's log, holds from the ledger, verdicts from `verify-bundle` run with sockets
refused. The run takes a few seconds and writes:

- `dashboard.html`: one self-contained file, with no network requests, that prints to
  PDF. It has the verdict, the zero-payload proof, the seven acts side by side, the lineage
  tree with its ledger footprint, every ticket, approval, warrant and operator act, and the
  V1–V12 matrix mapped to the OWASP Top 10 for Agentic Applications 2026, SOC 2 and the EU
  AI Act's Articles 12 and 14;
- `before.fidb` and `after.fidb`, the lineage's audit bundles, with `pinned-keys.json`
  and `charter.digest` to check them yourself;
- `attestation.json` and `report.md`;
- the deployment, with its `fiduciary.toml`: `fiduciary --config <dir>/fiduciary.toml
  verify`, `tickets list --all` and the rest open it. That is the onboarding path: a working
  charter, mandate and keys, where setting one up by hand takes the commands under
  [A charter, a mandate, a derived child](#a-charter-a-mandate-a-derived-child).

`--serve` serves the dashboard on loopback, `--report` writes it elsewhere, and `--out`
names the directory. `fiduciary demo` ships in the release after 1.0.0, as
`pip install 'interlock-fiduciary[demo]' && fiduciary demo --open` (the `demo` extra
brings the signing library and Cedar); with 1.0.0 from PyPI, run it from a clone as
above.

**With a live model.** `--live` puts a Claude model in front of act 1's queue, through the
session, metered on the mandate's envelope and stopped at `--max-cost`. It reads
`BENCHMARK_API_KEY` only (from the environment or `--env-file`), never `ANTHROPIC_API_KEY`.
The run of 2026-10-24, on interlock-escrow 1.0.3, interlock-agentgov 1.0.1 and cedarpy
4.12.2:

| | |
|---|---|
| Model | `claude-opus-5-5`, effort `medium`: 3 turns, 10.1 s |
| Tokens | 5,713 input, 732 output: **$0.039492**, metered by AgentGov on the session's billing scope |
| Its calls | `refund_order` (order 5, `"12.50"`), `apply_discount` (order 9, `10`), `lookup_order` 9, `lookup_order` 5 |
| The gateway | Cedar permitted all four |
| The stack | the refund committed; the discount rolled back after the stage measured order 9's total fall from 1,000.00 to 100.00 (the reprice trigger); the back office byte-identical |
| What it told the operator | that the discount was blocked by the 30% limit, that someone who manages that limit needs to check it, and that it did not try another percentage to get around it |

A real model, making calls that are correct and within policy, produces a consequence no
input check can see. The run is not reproducible, because what a model chooses is not;
the offline mode replays the calls a careful agent makes, and CI runs it.

## What it does

- **FIDU1 documents**, canonical JSON signed with Ed25519 (never HMAC): the charter (the
  root of authority: principals, keys, quorums, break-glass keys), the mandate (every bound
  one agent scope is delegated), the tool binding, the approval, the ticket, the warrant.
- **Monotone delegation.** `refines(child, parent)` names every field where a child is
  wider than its parent, so a delegation can only narrow (§5).
- **Compilation and binding.** A mandate compiles into Interlock's checkers, sinks and rate
  windows and AgentGov's governance and cognitive policies; what the deployment cannot
  enforce fails compilation (§6).
- **Escalation and warrants.** What a mandate does not allow goes to its principals as a
  signed ticket of typed evidence; a warrant forms on the quorum of its class, by
  principal, bound to the ticket's digest, the exact option and the heads the systems
  recorded, and executes once, through Interlock's operators and AgentGov's ledger (§7).
- **Sessions and MCP.** An agent acts through a session, in process or over MCP, and
  nothing it reaches can approve anything (§8).
- **The audit bundle.** A lineage exports to one file of no row data, payloads, prompts or
  secrets, and `verify-bundle` checks it offline, V1 to V12, taking every decision again
  (§9).
- **The service and the cluster** on PostgreSQL, with crash-exact recovery (§10).

`docs/FIDUCIARY_SPEC.md` is the specification, frozen for 1.x; `CHANGELOG.md` holds each
release. [`SECURITY.md`](SECURITY.md) states what is stored where and what is not claimed.

## Install

```
pip install 'interlock-fiduciary[sign]'          # local Ed25519 keys
pip install 'interlock-fiduciary[postgres,sign]' # the PostgreSQL store
pip install 'interlock-fiduciary[kms-aws]'       # keys held by AWS KMS
pip install 'interlock-fiduciary[demo]'          # fiduciary demo: signing, and the Cedar engine
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

`verify-bundle` prints V1 to V12 and every finding. `--json` prints the attestation
instead, for a SIEM or GRC system: the verdict, each check with its findings and the
controls its evidence bears on, and what the bundle holds (its mandates and their tree,
tickets, approvals, warrants, operator acts and ledger footprint). `--report audit.html`
(or `.md`) also writes the report, the HTML a self-contained console that prints to PDF.
Neither changes the exit code. It exits 0 on a pass. Otherwise it exits with the first
failing check's code:

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

Apache-2.0: see [LICENSE](LICENSE). To report a vulnerability, see [SECURITY.md](SECURITY.md).
