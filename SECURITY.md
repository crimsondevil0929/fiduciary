# Security

Fiduciary holds the service key that signs its log, the executor key that signs
Interlock's operator records, a write-capable AgentGov governor, and, through its
engines, Interlock's stage role on the application database. This document states what
it stores and where, what it trusts and what it checks, and what it does not claim, so a
platform or security team can decide without reading the source. The threat model in full
is `docs/FIDUCIARY_SPEC.md` §2 (parties, adversaries A1 to A8, guarantees G1 to G8,
non-guarantees).

## Reporting a vulnerability

Please report a suspected vulnerability privately rather than in a public issue: open a
[GitHub security advisory](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository. Expect an acknowledgement within a week.

Include a reproduction and `fiduciary --version`. If the issue involves a bundle, a
ticket or a log, redact tenant identifiers and amounts first: the report should not be the
leak.

Fixes land on `main` and ship in the next 1.x release. There is no backport branch for
earlier releases.

| Version | Supported |
|---|---|
| 1.0.x (latest) | yes |
| < 1.0 | no |

## Data map: what is stored, and where

| Where | What it holds | Row data, payloads, prompts or secrets? |
|---|---|---|
| The store (SQLite file, or a PostgreSQL schema) | Signed FIDU1 documents (charters, mandates, tool bindings, approvals, tickets, warrants), the hash-chained log, the intake, projections | **No row data, payloads, prompts or secrets.** Tickets carry the typed evidence principals decide on: tenant ids, amounts, message ids. An agent's own statement on a ticket is kept apart, quarantined, and shown as unverified |
| The AgentGov ledger | Envelopes, holds, spends and the anchors of every log record | No: amounts and fixed memos (AgentGov's `SECURITY.md`) |
| Escrow chains, receipt, runtime and operator logs | Digests, signatures, record hashes, operators' typed reasons | No |
| An audit bundle (`.fidb`) | The lineage's documents, log, ledger footprint with inclusion proofs, receipts, ILOK1 records and escrow chains | **No (F9-1).** `scripts/security_audit.py` plants a canary of every sensitive kind and fails if one reaches a hash, a column, a file or a bundle member; `fiduciary demo` scans both of its bundles for every row, payload, prompt and secret it holds |
| Configuration (`fiduciary.toml`) and key files | Paths, DSNs, key files or KMS key ids | **Secrets, yes**: key files are written readable by their owner only. Protect them as you protect the database credential |
| A report (`verify-bundle --report`, the demo's `dashboard.html`) | The attestation: what the bundle holds, summarized | As the bundle. The demo's dashboard also shows the demo's own tool arguments and, with `--live`, the model's reply |

## Trust boundaries

- **The agent holds nothing that can approve anything** (F7-4, F8-1). In process it holds
  a session handle; out of process, an MCP connection with a bearer token per session.
  An agent that runs code it writes is bridged out of process (F8-10).
- **No single principal is the authority.** Approvals count by principal, and only those
  that verify under the charter's keys; a warrant forms only on its quorum, bound to the
  ticket's digest and the exact option (G4, G8).
- **A compromised service is detected, not trusted** (F2-2). Every act it takes on
  Interlock or AgentGov carries the digest of the warrant or mandate it acts under, in a
  field that system signs or hashes, and `verify-bundle` V7 and V8 name an act without one.
- **The database owner is trusted for nothing.** The stores refuse to update or delete the
  log, the documents and the intake; on PostgreSQL there are three roles (service, intake,
  audit). Edits around those functions are found by `fiduciary verify` and V1 to V12.
- **The auditor needs only the charter's digest and the public keys**, both from outside
  the bundle. The verifier opens no socket and no database (F9-3).
- **Signatures are Ed25519 only.** HMAC is refused, because whoever can verify an HMAC can
  forge it. Keys may be held by AWS KMS (with an Interlock that signs ILOK1 at signature
  version 2).

## What it does not claim

- **Prevention of a compromised service or administrator.** Their acts are detected after
  the fact from signed records, and named with their key (break-glass keys labeled so):
  tamper-evident, not tamper-proof. The log's genesis is trusted as every self-hosted log's
  is; the ledger anchors bound everything after it.
- **k or more colluding principals** are the authority, recorded and attributable.
- **Intent.** Fiduciary bounds consequences, not purposes (§2.5).
- **What Interlock cannot see**: an unobserved table, a cascade outside the observed set,
  the external effect of a delivered request (§2.3, Interlock's `SECURITY.md`).
- **Prompt and output scanning.** Fiduciary does not inspect prompts or tool output for
  injected instructions or leaked data; a gateway does. It decides whether a consequence
  may stand, and proves the decision offline.
