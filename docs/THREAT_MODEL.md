# Threat model, on one page

This is what Fiduciary, Interlock and AgentGov protect against, what they do not, and what
each claim rests on. The full model is §2 of [FIDUCIARY_SPEC.md](FIDUCIARY_SPEC.md); every
guarantee there is traced to its tests.

## Where it sits

A gateway in front of an agent decides whether it may call a tool. This stack sits on the
write path behind the gateway. It stages each change the agent's tool would make, measures
what the change actually does, checks the measurement against a signed mandate, and then
commits it once or puts it back. Every decision is a signed record that an auditor can
verify offline.

The agent holds no database credential, no signing key and no approval surface. It holds a
session, over MCP or in process.

## What it protects against

| Threat | What happens | Checked offline by |
|---|---|---|
| **A prompt-injected or careless agent** calls a tool it was given, with arguments that look fine. A trigger, cascade, webhook, hook or rule then does far more. | The change is measured before it commits: the rows a trigger wrote, the files a hook generated, the replicas a webhook multiplied. A change beyond the mandate's bounds (tables, tenants, rows, totals, windows, budgets) is refused, and nothing lands. | V4 (each receipt was judged by the mandated checkers) and V5 (the receipts) |
| **An agent that tries to widen its own authority**, by calling a tool it was not given, writing outside its tables or tenants, raising its own budget, or approving its own request. | It has no credential to do any of it. An exception is a ticket that principals decide. The agent's own text is shown to them apart, as an unverified claim. | V3 (delegation), V7, V8 |
| **One approver, or a minority**, acting alone. | An authority-granting decision takes k of n distinct principals' signatures over the exact bytes they were shown: 2 of 3 by default. | V8 (every warrant had its quorum) |
| **A replayed approval, ticket or warrant**, in another context or after its state moved. | Each is bound to its ticket, deployment, the heads it was decided on, and an expiry. | V8, V12 |
| **The database owner or a compromised service** editing, deleting, reordering or forging records, or acting with no warrant. A break-glass release or a by-hand reset is one example. | Not prevented. Every such act is detected: the logs are hash-chained, signed, and anchored in a ledger whose checkpoint is signed. | V1 to V12. V7 names each act with no mandate or warrant behind it, labelled `[break-glass]` when the charter declared that key |
| **A vendor or system that says one thing and does another**, or a change made around the agent with the same account. | The system's own log is matched against the receipts in a bundle. Each action is explained by a receipt, or named as a phantom. | `fiduciary reconcile-actions` |

## What it does not protect against

- **A compromised host or service, in real time.** An attacker with the service's keys and
  code can act without a warrant, or refuse to act at all. The acts are detected afterwards
  (V7, V8). Nothing prevents them, and nothing guarantees liveness.
- **Enough colluding principals.** k of them are the authority. Their decisions are recorded
  and attributable, and are never second-guessed.
- **Reads, and anything sent through them.** Only writes are staged. A read tool is metered
  and rate-limited, but what it returns, and where the agent sends it, is not measured. Data
  that leaves through a read channel is outside this model.
- **Unescrowed tools and unobserved tables.** A passthrough tool the mandate trusts to be
  read-only is trusted. A table no `TableSpec` observes is denied to the stage on SQLite and
  by grants on PostgreSQL. A cascade into one is refused unless an operator acknowledged it,
  and every stage that runs with that gap says so.
- **What a remote system does after the request (class R), and changes outside class P and
  V.** For an outbound request, only the request is measured. The vendor's effect is its own
  word, checked afterwards against its log. On Kubernetes and Terraform (class P), the
  change measured is the system's own plan. A controller that acts later, outside that plan,
  is not measured. On a FHIR server (class V), only what the read-back sees is checked and
  put back. A rule that changes records the plan never read is caught only if the read-back
  covers them.
- **A purpose the mandate allows but the business does not want.** The stack bounds
  consequences, not intent.
- **A principal who signs without reading.** The ticket shows the evidence and quarantines
  the agent's text, but cannot make anyone read it.

## The four measurement classes

Each receipt says how its change was measured, and the verifier reports how many of each a
bundle holds.

| Class | Measured | Trust boundary | Where |
|---|---|---|---|
| **T**, transactional | every row the plan touched, captured inside the transaction, before commit | The database. A write outside the observed tables is refused before it runs. | SQLite, PostgreSQL |
| **P**, plan-bound | the system's own plan of the change, applied only if nothing moved since (a compare-and-swap) | The system's planner: the Git diff, the API server's dry run, the Terraform plan. Later actions by controllers are outside it. | Git, Kubernetes, Terraform |
| **V**, predicted and verified | a prediction, written conditionally, then read back. A difference is put back and receipted `compensated`. | The read-back. What it does not read, it does not check. | FHIR servers |
| **R**, request-only | the request, exactly once through a transactional outbox | The vendor's word. Delivery is receipted and attested by the relay, and the vendor's log is reconciled against it. | Outbound API calls |

## Key custody

| Key | Held by | If it is stolen |
|---|---|---|
| Each principal's key | that principal, on their own machine or in a KMS. `init` writes all three locally, so move them. | It is one vote. With k of them, an attacker is the authority, and every act is still recorded. |
| Service key (signs the log and the manifest) | the service | The attacker can write records the service would write. Acts with no warrant are still named by V7 and V8, and past records stay anchored. |
| Ledger checkpoint key | the service | Checkpoints can be signed, but every entry stays hash-chained, and a witness's cosignature (`--witness`) pins each checkpoint. |
| Receipt key, relay keys | the service and each relay | Receipts and attestations can be forged going forward. A bundle names its keys, and an auditor pins them (`--keys`). |
| Break-glass key | an operator | It acts by design. Each act is named `[break-glass]` in V7. |
| Seal key (sealed bundles) | the service | Another party's records could be read in a sealed bundle. It cannot hide a party's own records from its bundle (V10). |

## What offline verification guarantees

`fiduciary verify-bundle <bundle> --charter <digest> [--keys <file>]` needs no database and
no network. Given the charter digest and public keys pinned from somewhere other than the
bundle, it proves:

- every record, document and receipt is intact, ordered and signed;
- every act on the lineage's ledger, outbox and escrow chain traces to a mandate version or
  a warrant, or is named;
- every warrant had its quorum, on the state it was decided on, before it expired;
- every relay delivery is signed, included and bound to the plan that committed it.

It does not prove anything about time beyond the ledger's own order, anything a record does
not carry, or anything about a key the auditor did not pin. A bundle cannot vouch for
itself. The console's in-browser check covers the container, the signatures, the chain, the
seals, the anchors and the deliveries, as part of V1, V5, V9 and V10. It is a partial check,
not a verdict.
