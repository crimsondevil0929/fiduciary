# Fiduciary

Signed mandates, quorum approvals and offline compliance proofs for AI agents, over
[AgentGov](https://pypi.org/project/interlock-agentgov/) (the budget ledger) and
[Interlock](https://pypi.org/project/interlock-escrow/) (the side-effect reference
monitor).

AgentGov says whether a scope may spend an amount, and Interlock says whether a plan's
measured consequence satisfies its checkers. Fiduciary says who decided those limits,
on whose authority, and who approved an exception. It records each answer as a signed
document that anyone holding the public keys can verify.

`docs/FIDUCIARY_SPEC.md` is the specification. This is **0.3.0**, Phase 3 of its
roadmap:
- Phase 1 (0.1.0) gave the FIDU1 format, the documents, the order on mandates, the log
  and its two stores, and the command line.
- Phase 2 (0.2.0) compiles a mandate into Interlock's checkers and AgentGov's policies,
  and binds it to a deployment.
- Phase 3 escalates what a mandate does not allow to its principals, and executes their
  decision exactly once.

The agent and MCP bridge are Phase 4, and the audit bundle Phase 5.

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

The executor signs interlock's operator records. Until U-2 ships, its key and the runtime
records key may not be AWS KMS keys:

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

`scripts/ilok1_sizes.py --check` measures the ILOK1 records the executor has interlock
sign, and requires the spec's table (§3.5) to match.

`vectors/fidu1/` holds the golden vectors: valid documents with their digests and
signing inputs, a chain of records, and invalid documents, each with the error it must
fail with. `scripts/make_vectors.py` regenerates them, and the tests require them
unchanged. An implementation in another language that reproduces them reads and signs
FIDU1 as this one does.

## License

Apache-2.0.
