# Fiduciary

Signed mandates, quorum approvals and offline compliance proofs for AI agents, over
[AgentGov](https://pypi.org/project/interlock-agentgov/) (the budget ledger) and
[Interlock](https://pypi.org/project/interlock-escrow/) (the side-effect reference
monitor).

AgentGov says whether a scope may spend an amount, and Interlock says whether a plan's
measured consequence satisfies its checkers. Fiduciary says who decided those limits,
on whose authority, and who approved an exception. It records each answer as a signed
document that anyone holding the public keys can verify.

`docs/FIDUCIARY_SPEC.md` is the specification. This is **0.1.0**, Phase 1 of its
roadmap: the FIDU1 format, the documents, the order on mandates, the log and its two
stores, and the command line. Compiling a mandate into Interlock's checkers is Phase 2.
Escalation tickets and warrants are Phase 3, the agent and MCP bridge Phase 4, and the
audit bundle Phase 5.

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

`fiduciary approve` prints the document it signs and asks before signing it (G8). A
principal signs on their own machine, and needs no database credential beyond the intake
role.

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

`vectors/fidu1/` holds the golden vectors: valid documents with their digests and
signing inputs, a chain of records, and invalid documents, each with the error it must
fail with. `scripts/make_vectors.py` regenerates them, and the tests require them
unchanged. An implementation in another language that reproduces them reads and signs
FIDU1 as this one does.

## License

Apache-2.0.
