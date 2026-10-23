# Fiduciary: mandates, warrants and compliance proofs

**Status:** implemented by `interlock-fiduciary` 1.0.0, and frozen for 1.x
([Conformance](#conformance-100)). Sections 1 to 9 are the contract. Section 10 is the
architecture `interlock-fiduciary` builds it into, and section 11 the roadmap it followed
from 0.1.0 to 1.0.0. Section 12 lists what the contract needs from `interlock` and
`agentgov` that they do not provide yet. [Conformance](#conformance-100) states what 1.0.0
implements, and each gap with its reason; [Traceability](#traceability) maps each
requirement to the phase that built it and the tests that prove it. Where the text was
amended as it was built, it says so, and in which release.
**Applies to:** `interlock-fiduciary` 1.x, against `interlock-escrow` 1.0.2 and
`interlock-agentgov` 1.0.0.
**Normative language:** MUST, MUST NOT, SHOULD, MAY per RFC 2119.

---

## Preamble: package boundary

`fiduciary` is a third sibling package and repository: `interlock-fiduciary` on PyPI,
imported as `fiduciary`. It is not a subpackage or an extra of either package below it, and
neither loads it.

```
    fiduciary  ──── imports ───▶  interlock   (permitted)
    fiduciary  ──── imports ───▶  agentgov    (permitted)
    interlock  ──── imports ───▶  fiduciary   (FORBIDDEN)
    agentgov   ──── imports ───▶  fiduciary   (FORBIDDEN)
```

Fiduciary drives both packages as a client. It holds what a deployment gives it:

- a write-capable AgentGov governor;
- interlock's stage role, through the engines it builds;
- one interlock operator key.

It depends on their public surfaces only, and sections 6.4 and 7.6 list every call. A
requirement here that seems to need a private name is wrong. It MUST be redesigned around
the public surface, or listed in section 12 as an upstream change. It is never worked
around.

### Why the boundary is drawn here

AgentGov holds the record of money. Interlock holds the write path and the record of
effects. Fiduciary holds decisions about authority: who may act, within what, and who
approved an exception. Fusing it with either package would put the component that grants
authority inside the trust boundary of the record that has to judge it.

Kept apart, every act fiduciary takes lands as evidence in logs it does not control: the
ledger, the escrow chain, the ARC1 receipt log and the ILOK1 operator log. The audit bundle
(section 9) holds fiduciary's own log to them.

## Terms

| Term | Meaning |
|---|---|
| Principal | A person or office that holds authority and an Ed25519 key, named in the charter. |
| Charter | The deployment's root of authority: its principals, their keys and its quorums. Its digest is pinned in configuration. |
| Agent | Model-driven code acting for a principal. It is untrusted: it may be wrong, and an adversary may steer it through its inputs. |
| Mandate | A signed delegation from principals to one agent scope. It states what the agent may do, within what bounds, and how its escalations are decided. |
| Lineage | All versions of one mandate, identified by `mandate_id`. The versions are hash-linked. |
| Subject | The AgentGov scope a mandate governs. |
| Satellite | A scope created beside the subject on its behalf: `{scope}/recovery`, or `{scope}/ext-N`. |
| Derivation | A child mandate created below a parent without principals' approval. It is valid because it can only narrow its parent (section 5). |
| Ticket | A signed record of an escalation: the boundary the agent hit, the typed evidence, and the resolutions on offer. |
| Approval | One principal's signed decision on one subject: a ticket's resolution, a mandate, a revocation, or a charter. |
| Warrant | A resolution backed by enough valid approvals, or by a mandated rule, and ready to execute exactly once. |
| Executor key | The interlock operator key fiduciary uses to act on the outbox, under a warrant. |
| Service key | The key fiduciary signs its own log, tickets and derived mandates with. |
| Bundle | An offline-verifiable export of a lineage's authority and evidence. |

---

# 1. Thesis and lifecycles

## 1.1 Thesis

Interlock answers whether a plan's measured consequence satisfies a set of invariants.
AgentGov answers whether a scope may spend an amount. Neither says who chose those
invariants and limits, or on whose authority. Neither says what should happen when an agent
reaches one. Today all three answers are code and convention:

- **Checkers live in application code, by design.** `EscrowRuntime.from_config` builds
  everything from `interlock.toml` except the checkers, which "stay in code, as policy is
  reviewed".
  - Nothing records which review approved which checker set.
  - A receipt names its checkers by a configuration digest, and no one can recompute that
    digest from a document a principal signed.
- **Grants are unauthenticated.** `BudgetGuard.grant` records `approved_by` as a string it
  "cannot authenticate", and its module says so.
- **One operator key suffices.** One operator key signs an interlock operator action.
  Nothing requires two people for a large refund or a late compensation.
- **Overrides have no surface.** A refused plan is final (E1-4). ESCROW_SPEC says an
  override must carry a waiver, and no surface exists for one.

Fiduciary makes each of these an artifact:

- **Authority** becomes a signed mandate. It compiles deterministically into the checkers,
  sinks, windows and budgets that interlock and agentgov already enforce.
- **An exception** becomes a ticket that k of n principals approve. The ticket is bound to
  measured evidence, not to the agent's account of it.
- **Proof** becomes a bundle an auditor verifies offline from public keys.

Three claims, stated so they can be falsified:

1. **Nothing acts on a mandated scope without authority that verifies.** Every plan, spend,
   delivery, operator action and breaker reset on a mandated scope traces to a mandate
   version in force or to a warrant. The bundle verifier names anything that does not.
2. **Delegation only narrows.** Only its issuers' quorum can widen a mandate. A derived
   mandate admits no plan its parent would refuse, and aggregate bounds hold across all of a
   mandate's descendants.
3. **A warrant executes once, on the state its approvers saw, before it expires.**

## 1.2 Mandate lifecycle

```
                        ┌──────────┐
       drafted, signed  │ PROPOSED │  no scope, no engine
       by its author    └────┬─────┘
                             │ issuance quorum of approvals         (derived: child ⊑ parent,
                             ▼                                        no approvals)
                        ┌──────────┐
                        │  ISSUED  │  recorded in the FIDU1 log
                        └────┬─────┘
                             │ bind: delegate the envelope,        AgentGov: delegate
                             │ compile, build the engine
                        ┌────▼─────┐
             ┌──────────│  BOUND   │◀──────────┐
             │          └──┬────┬──┘           │ amend (issuance quorum): version n+1,
             │             │    │              │ rebound at the next plan boundary;
             │             │    └──────────────┘ rebase (derived, still ⊑ its parent)
             │ parent      │
             │ narrowed    │ revoke (revocation quorum), or the term ends
             │ below it    ▼
        ┌────▼──────┐  ┌───────────────────┐
        │ SUSPENDED │  │ REVOKED / EXPIRED │  AgentGov: every scope of the lineage
        └───────────┘  └───────────────────┘  tripped; engine closed  (terminal)
```

- **F1-1.** A mandate MUST NOT govern anything before it is `ISSUED`. Nothing it governs
  exists before it is `BOUND`: no scope is delegated and no engine is built for a proposal.
- **F1-2.** A version is immutable. An amendment is a new version, and its `prev` names its
  predecessor's digest.
  - The log accepts version n+1 only while version n is the lineage's head, so a lineage
    cannot fork in the log.
  - If two versions share one `prev` and both carry a quorum, the issuers have equivocated,
    and the bundle verifier names it (V3).
- **F1-3.** An amendment takes effect at a plan boundary. The plan in flight is adjudicated
  under the version it was admitted under, and its receipt's `policy_epoch` says which.
- **F1-4.** A derived mandate is in force only while it is ⊑ its parent's version in force
  (section 5). When its parent is amended, the service checks each descendant against the
  new version:
  - a descendant still ⊑ it is rebased: a `mandate.rebased` record, with no approvals;
  - a descendant no longer ⊑ it is suspended.
- **F1-5.** Revocation and expiry are terminal for the lineage and its descendants. Both
  take effect at an AgentGov trip, so interlock's existing rules stop what is in flight
  (section 5.5).

## 1.3 Escalation lifecycle

```
   boundary hit  ┌────────┐  ticket.raised: service-signed, typed evidence, options
   ─────────────▶│  OPEN  │──────────────────────────────────────────────┐
                 └─┬───┬──┘                                              │ a mandated
     approvals     │   │ deadline passes, or the quorum                  │ rule holds
     reach k for   │   │ becomes unreachable                             │ over the
     one option    │   ▼                                                 │ evidence
                   │  ┌───────────────────┐                              ▼
                   │  │ LAPSED / DECLINED │  nothing executes   ┌──────────────┐
                   │  └───────────────────┘  (terminal)         │  WARRANTED   │
                   ▼                                            │  (by rule)   │
            ┌───────────┐                                       └──────┬───────┘
            │ WARRANTED │──────────────────────────────────────────────┤
            └───────────┘                                              │
                                                                       │ execution.intent
                                                                 ┌─────▼─────┐
                       act on interlock and AgentGov under       │ EXECUTING │
                       the warrant's digest                      └──┬─────┬──┘
                                                 execution.applied  │     │ the state moved,
                                                              ┌─────▼──┐ ┌▼────────┐
                                                              │EXECUTED│ │ REFUSED │
                                                              └────────┘ └─────────┘

   A process killed in EXECUTING leaves an intent with no outcome. resolve() reads what
   the warrant's digest reached, and records APPLIED or ABANDONED (section 7.5).
```

- **F1-6.** Silence is refusal. A ticket that reaches its deadline executes nothing:
  - a held message stays held;
  - a quote expires;
  - a refused plan stays refused.
- **F1-7.** A ticket never changes a verdict: a refused plan stays refused (E1-4). The
  resolutions that move past a refusal create a new artifact instead, which is adjudicated
  on its own:
  - a repair proposal, submitted as a new plan and adjudicated from scratch; or
  - a new mandate version, under which a new plan is staged and verified.

---

# 2. Threat model

## 2.1 Parties and what each holds

| Party | Holds | Trusted for |
|---|---|---|
| Principals | Ed25519 keys, held locally or in a key service | Their own decisions. No single principal is trusted. k of them together are the authority. |
| Agent | A session handle (in process) or an MCP connection (out of process). Never a database credential, a governor, a signing key or an approval surface. | Nothing. |
| Fiduciary service | The service key, the executor key, the runtime records key, a write-capable governor, the stage role (through its engines), and the receipt log's signer | Liveness, and not acting without a warrant. Every act it takes is checked offline against its warrant, so a service that acts without one is detected, not trusted. |
| Interlock and AgentGov | What ESCROW_SPEC §5.3 assigns them | What ESCROW_SPEC and agentgov's `ARCHITECTURE.md` and `docs/RECEIPTS.md` claim, and no more. |
| Key services (KMS, HSM, Vault) | Principals' and the service's private keys | Signing only on an authenticated request. A key service compromised for a principal is that principal compromised. |
| Database owner | Direct write access to every table | Nothing. The verifiers name every edit made around the functions. |
| Auditor | The pinned charter digest and the public keys | Running the verifier, which needs no database and no network. |

## 2.2 Adversaries

- **A1. The agent.** It tries to widen its authority:
  - call a tool it was not given;
  - write a table, tenant or sink outside its bounds;
  - smuggle SQL into an argument;
  - raise its own budget, or sub-delegate more than it holds;
  - approve its own escalation, or replay an approval.

  It also tries to mislead principals by writing the prose a ticket shows them.
- **A2. A minority of principals.** Fewer than k collude to execute a resolution, or one
  principal tries to count twice.
- **A3. A compromised fiduciary service.** It holds every credential the service holds and
  runs arbitrary code.
- **A4. The database owner.** It edits, deletes, reorders or truncates rows: documents, log
  records, approvals, projections, and interlock's and AgentGov's tables.
- **A5. A replayer.** It presents a valid approval, ticket, warrant or mandate in a context
  it was not made for: another ticket, another resolution, another deployment, or a later
  state.
- **A6. A key-service adversary.** It answers a signing request with a signature from
  another key or version, or with something that is not a signature.
- **A7. Approval fatigue.** The agent, or whatever steers it, raises tickets until a tired
  principal approves one. MFA-fatigue attacks have shown that this works on people.
- **A8. A network adversary.** It sits between an out-of-process agent and the bridge, or
  between a principal's client and the service.

## 2.3 Assumptions

- **Cryptography.** Ed25519 (RFC 8032), SHA-256 and SHA-512 are secure. Ed25519 is verified
  with `S < L` and a canonical `R`, as agentgov's verifiers do.
- **Encoding.** The canonical encoding is RFC 8785 over agentgov's value domain (section
  3.1), so every party hashes the same bytes.
- **The ledger.** AgentGov's ledger verifies (`verify_integrity`). Its ordering of entries is
  the one clock every fiduciary claim about time is stated against (section 9.4). The
  service's instants come from the service's clock: they are checked against the ledger's,
  never trusted on their own.
- **Interlock's measurement** is as ESCROW_SPEC states, including the requirements it lists
  as partial and unimplemented. Fiduciary bounds what interlock measures. It does not repair
  what interlock cannot see:
  - an unobserved table;
  - a cascade outside the observed set;
  - the external effect of a delivered request.

## 2.4 Guarantees

| # | Guarantee | Prevented by | Detected by | Residual |
|---|---|---|---|---|
| G1 | **No ambient authority.** Every plan, ledger entry, delivery, operator action and breaker reset in a mandated lineage's footprint traces to a mandate version in force or to a warrant. | The agent holds no credential (§8.4). The compiled engine is the bridge's only write path. | `verify-bundle` V7 (completeness) | A1, when the bridge is in process and the agent runs code (§8.4). Interlock operators and governors outside fiduciary can still act: their acts are named, not prevented. |
| G2 | **No unmandated checker weakening.** A plan of a mandated scope is admitted only by an engine that runs at least the compiled checker set of the version in force. | Fiduciary builds the engine from `compile()` alone | V4, per receipt | Until U-1, V4 checks the binding checker's configuration exactly and the others by name. |
| G3 | **Monotone delegation.** A derived mandate admits no plan, spend, request or escalation resolution that its parent would refuse. Windows and envelopes bound the whole subtree. | `refines()` at derivation and at rebase; AgentGov's delegation invariant | V3 | None known. §5.6 states what is proven, and how. |
| G4 | **Quorum.** An authority-granting resolution executes only with one of two bases: k valid approvals of exactly that resolution from k distinct roster principals, or a mandated rule whose predicate holds over the ticket's typed evidence. | The service forms warrants only this way | V8 | A3 can execute without a warrant. V7 and V8 detect it; nothing prevents it (the G1 residual). |
| G5 | **Once, on the approved state, before expiry.** | Two-phase execution; interlock's head checks; AgentGov's idempotency keys | V8 | Where Interlock lacks U-4, an outbox action can apply at a head that moved between fiduciary's check and the operator's own read; V8 names it. With U-4 the operator refuses it before signing anything. |
| G6 | **Tamper evidence.** Any edit, deletion, reordering, truncation or fork is detected offline, in fiduciary's log or documents, or in the interlock and AgentGov evidence they name. | — | V1 to V12 | The log's genesis, as with every self-hosted log (E3-5). The ledger anchors bound everything after it. |
| G7 | **Revocation stops the lineage.** After a revocation's trip is in the ledger, no plan of the lineage commits, and no message of it is delivered. | AgentGov's latching breaker; interlock E3-8, under which a halted scope does not commit; relays holding a halted scope's messages | V11 | A plan whose stage opened before the trip is reported as in flight, and does not commit. |
| G8 | **What you see is what you sign.** A principal's approval covers the digest of the exact bytes their client rendered. A ticket's summary is rendered from typed evidence, and the agent's own text is quarantined. | The principal client rules (§7.3) | V8, through the resolution digest | A principal who signs without reading. |

- **F2-1.** Every guarantee is a claim about evidence. A verifier holding the pinned charter
  digest and the public keys can check it. A property that can only be stated about a
  running process is not one of these guarantees.
- **F2-2.** A compromised service is held to account, not trusted.
  - Every act it takes on interlock or AgentGov MUST carry the digest of the warrant or the
    mandate version it acts under.
  - That digest MUST sit in a field that the system acted on signs or hashes (section 7.6).
  - An act without one is ambient authority, and the bundle verifier names it.

## 2.5 Non-guarantees

- **k or more colluding principals** are the authority. Their acts are recorded and
  attributable, never prevented.
- **Intent alignment.** Fiduciary bounds consequences, not purposes. It admits a plan that
  is within its mandate but wrong for the business, as interlock admits a correct but
  unwanted plan that is within its checkers.
- **Unescrowed tools.** A passthrough tool (§4.3) is metered and rate-bounded. Any side
  effects it has are not measured. A mandate that lists one trusts its binding's
  declaration that it is read-only.
- **Liveness under A3.** A compromised service can refuse to raise, execute or record
  anything.
- **Privacy from principals.** A ticket shows principals the evidence they decide on,
  including tenant identifiers and amounts. Principals are trusted readers.

---

# 3. FIDU1: framing

## 3.1 Value domain

FIDU1 documents are JSON values in ARC1's canonical domain. They are encoded with
`agentgov.receipts.canonical_bytes` and decoded with `loads_strict`.

| Kind | Form |
|---|---|
| JSON values | Strings, which must be valid Unicode; integers within `±(2**53 − 1)`; `true`, `false` and `null`; arrays; objects whose keys sort by UTF-16 code units. **No floats.** |
| Hashes | 64 lowercase hex characters |
| Key ids | 16 lowercase hex characters |
| Public keys | `ed25519:` followed by 64 lowercase hex characters |
| Signatures | 128 lowercase hex characters |
| Instants | `YYYY-MM-DDTHH:MM:SS.ffffffZ`, in UTC |
| Money | A decimal string with exactly eight places (`"12.50000000"`), AgentGov's quantum |
| Fractions and thresholds | A decimal string with at most six places (`"0.3"`, `"0.25"`), never a float |
| Identifiers | Canonical UUID strings |
| Names | `[a-z][a-z0-9_.-]{0,63}` |
| Scope ids | As AgentGov accepts them |
| Text | 1 to 512 characters, with no control characters |
| Sets | Arrays sorted ascending by UTF-16 code units, with no duplicates. A set out of order is malformed, never re-sorted. |

- **F3-1.** Decoding is strict, as ARC1's is. A document has exactly the fields its type
  names, and each has the right type. Every set is sorted and free of duplicates, and the
  fields agree with each other. An unknown field is an error: a verifier that tolerated one
  would be signing off on content it never read.

## 3.2 Envelope and signing input

Every FIDU1 document is one JSON object:

```json
{"v": "FIDU1", "type": "<type>", "...": "...",
 "sig": {"alg": "ed25519", "key_id": "<16 hex>", "signature": "<128 hex>"}}
```

The signature covers two things: the type's domain, and the SHA-512 of the document's
canonical bytes with `sig` reduced to `{"alg", "key_id"}`.

```
signing_input(doc) = DOMAIN[doc.type]
                   ‖ SHA-512( canonical_bytes( doc with sig := {"alg": …, "key_id": …} ) )
```

This is ARC1 1.2's version 2 construction (in agentgov, `schema._signing_input` with
`version=2`), reproduced over FIDU1's own domains. Fiduciary reproduces it rather than
calling it, because it depends on no private name.

- **F3-2.** FIDU1 has exactly one signature construction. There is no whole-document mode
  and no `sv` field, so no signature can be downgraded to a construction a key service
  limits. The domain carries the version.
- **F3-3.** Every signature is Ed25519. A document whose `sig.alg` is anything else is
  malformed. HMAC is refused outright, because whoever verifies an HMAC holds the secret and
  can forge it:
  - an HMAC approval proves nothing to anyone else;
  - a quorum of HMAC approvals checked by one service is a quorum of one.
- **F3-4.** A verifier MUST check a signature under the full public key that the charter or
  a log record pins for the signer. It MUST NOT accept a key on the document's say-so.
  `key_id` is a lookup hint, not an identity (F7-5). It is agentgov's
  `SHA-256("ARC1/ed25519/v1\n" ‖ key)[:16]`.

## 3.3 Digests and references

```
digest(doc) = SHA-256( canonical_bytes(doc) )      # the whole document, signature included
```

Every reference between documents, records, tickets, approvals and warrants is a digest.
Ed25519 signing is deterministic, but a signer can still produce two valid signatures over
one body by choosing its own nonces. One approval can therefore have two digests. That is
why authority is counted by principal, never by digest (F7-5).

## 3.4 Domains

| Type | Domain (prefix of the signing input) | Signed by |
|---|---|---|
| `charter` | `FIDU1/charter/v1\n` | its author, one of its principals |
| `mandate` | `FIDU1/mandate/v1\n` | its author: a principal (issued) or the service (derived) |
| `tool` | `FIDU1/tool/v1\n` | its author: the developer's key, or a principal's |
| `ticket` | `FIDU1/ticket/v1\n` | the service |
| `approval` | `FIDU1/approval/v1\n` | one principal |
| `warrant` | `FIDU1/warrant/v1\n` | the service |
| `record` | `FIDU1/record/v1\n` | the service |
| `bundle` | `FIDU1/bundle/v1\n` | the exporter, optionally |

- **F3-5.** No FIDU1 domain is a prefix of another, and none begins with `ARC1/` or
  `ILOK1/`. A FIDU1 signature therefore never verifies as an ARC1 or ILOK1 signature, and
  the reverse also holds.
- **F3-6.** Every domain is at most 32 bytes. Every signing input is therefore at most 96
  bytes, whatever the document's size. The longest domain today, `FIDU1/approval/v1\n`,
  gives an 82-byte input.

## 3.5 Key services and the 4,096-byte limit

AWS KMS signs Ed25519 in one mode only, `ED25519_SHA_512` over a `RAW` message of at most
4096 bytes (`agentgov.receipts.kms_aws`). Its other mode, `ED25519_PH_SHA_512`, is
Ed25519ph, whose signatures do not verify as Ed25519.

- **F3-7.** `AwsKmsSigner`, `HttpRemoteSigner` and `Ed25519Signer` MUST each be able to
  produce every FIDU1 signature. F3-6 guarantees this, since the message is at most 96
  bytes. Mandates, tickets and bundles of any size can all be signed, and so can quorum
  approvals from principals whose keys live only in KMS.
- **F3-8.** A signer is used only through agentgov's `Signer` protocol (`alg`, `key_id`,
  `sign`, `public_key`), or as an `AsyncSigner` through `as_async`. A signature from a
  remote signer is verified twice before it is recorded:
  - `RemoteSigner` verifies it under its pinned key before returning it;
  - fiduciary verifies it again, under the key the charter pins.
- **F3-9.** The limit does not stop at FIDU1. Fiduciary also writes ILOK1 records:
  - the executor's operator intents and outcomes;
  - its guard's quotes and grants.

  Interlock signs ILOK1 records whole: `ILOK1/record/v1\n` followed by the canonical record.
  Measured on interlock 1.0.2 by `scripts/ilok1_sizes.py`, from records interlock's own
  `Operator` writes under a `FIDU1 warrant <digest>` reason on the test deployment (stripe's
  `charges.capture` compensated by `refunds.create`, an operator named by its key id).
  `--check` holds this table to the script, and the tests run it:

  | ILOK1 record | Bytes signed, first item | Each further item | Fits in 4096 |
  |---|---|---|---|
  | `operator.intent`, `release` or `cancel` | 573 | 125 per target | 29 targets |
  | `operator.intent`, `compensate` | 854 | 403 per target | 9 targets |
  | `operator.applied` | 595 | 152 per row | 24 rows |

  The sizes grow with the names a deployment uses: the operator's, and each sink's and
  operation's. The bound of 8 targets below leaves a compensation one target of room for them.

  A cancel also cancels everything that waits for its target, so the number of rows an
  applied record names is not bounded by its targets. Through AWS KMS an action can sign its
  intent and act, and then fail to sign its outcome. Its intent is left unresolved, and
  `resolve()` cannot record the outcome either. So:
  - Where the installed Interlock signs ILOK1 records whole (before U-2), fiduciary MUST
    refuse to start with an `AwsKmsSigner` as its executor key or its runtime records key.
    Where it signs them at signature version 2 (U-2: the record's SHA-512, under 100 bytes
    whatever the record's size), it accepts one, and the log signs at version 2.
  - It MUST split outbox actions into intents of at most 8 targets, with any key and any
    Interlock: an execution's steps are part of its intent, which replay recomputes, so
    they never depend on the Interlock installed.

  *(Amended after the 1.0.0 freeze, which refused an AWS KMS key whatever the Interlock.)*

---

# 4. Documents

The field names, types and constraints below are normative. The examples are illustrative.

## 4.1 Charter

The charter is the deployment's root of authority. Configuration pins the digest of
version 1, as interlock's configuration pins `[operators.keys]`.

```json
{
  "v": "FIDU1", "type": "charter",
  "charter_id": "6f1c0c8e-3c1f-4f0e-9a51-2b8a7f0d9e11",
  "version": 1,
  "prev": null,
  "deployment": {"ledger": "<entry_hash of AgentGov entry 1>", "label": "acme-prod"},
  "principals": [
    {"name": "cfo",      "key": "ed25519:<64 hex>"},
    {"name": "head-ops", "key": "ed25519:<64 hex>"},
    {"name": "security", "key": "ed25519:<64 hex>"}
  ],
  "quorums": {"charter": 3, "issue": 2, "revoke": 1},
  "break_glass": ["<interlock operator key id>"],
  "issued_at": "2026-10-18T09:00:00.000000Z",
  "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}
}
```

- **F4-1.** `principals` is sorted by name. Names are unique, and so are keys: one key under
  two names would let its holder count twice (F7-5).
- **F4-2.** `deployment.ledger` is the hash of AgentGov entry 1: the same value agentgov's
  published ledger checkpoints name (`agentgov-ledger:<hash>`). Every document under the
  charter is bound to that ledger through it. A charter, mandate, ticket or approval from
  another deployment therefore does not verify here (A5).
- **F4-3.** How a version comes into force:
  - Version 1 is in force when configuration pins its digest and approvals over it are
    recorded from `quorums.charter` of its own principals.
  - Version n+1 is in force once approvals are recorded from `quorums.charter` of version
    n's principals.
  - A principal's key is rotated, and a principal added or removed, only by a new version.
- **F4-4.** `1 ≤ revoke ≤ issue ≤ charter ≤ len(principals)`. An act that reduces authority
  never needs more approvals than one that grants it (F7-8).
- **F4-5.** A principal's key can be suspended faster than a new version can be adopted.
  - Approvals from `quorums.revoke` principals over a `suspension` subject (§4.5) stop that
    key counting from the record that suspends it.
  - Suspension reduces authority, so it needs only the revocation quorum.
  - It can delay escalations, which is a cost to liveness. It cannot grant anything.
- **F4-6.** `break_glass` lists the interlock operator keys that act outside fiduciary on
  purpose: a human operator's emergency key. Their acts on a lineage are still ambient
  authority, and V7 labels them as break-glass instead of as unexplained.

## 4.2 Mandate

```json
{
  "v": "FIDU1", "type": "mandate",
  "mandate_id": "2d0b5f7e-8a43-4c2b-b1a4-0c4e2f9a7d31",
  "version": 3,
  "prev": "<digest of version 2>",
  "charter": {"charter_id": "6f1c…", "version": 1, "digest": "<hash>"},
  "parent": null,
  "issuance": "quorum",
  "subject": {"scope": "support-agent", "funded_from": "support",
              "label": "Tier-1 support refunds"},
  "term": {"not_before": "2026-10-18T00:00:00.000000Z",
           "not_after": "2027-01-01T00:00:00.000000Z"},
  "roster": {
    "principals": ["cfo", "head-ops", "security"],
    "quorums": {"amend": 2, "revoke": 1, "reduce": 1, "grant": 2, "reset": 2,
                "reset_safety": 3, "release": 1, "requeue": 1, "compensate": 1,
                "compensate_late": 2, "accept_repair": 1}
  },
  "budget": {"envelope": "250.00000000", "per_call": "0.50000000",
             "models": ["claude-opus-5-5", "claude-sonnet-5-5"], "max_output_tokens": 4096},
  "governance": {
    "max_calls": 60, "seconds": 60, "fleet_velocity": true,
    "cognitive": {"max_identical_repeats": 3, "similarity_threshold": "0.7",
                  "max_similar_streak": 3, "result_similarity_threshold": "0.3",
                  "max_cycle_period": 4, "min_cycle_repeats": 3,
                  "min_novelty_ratio": "0.02", "exempt_tools": []}
  },
  "data": {
    "tables": {"orders": ["update"], "refunds": ["insert"]},
    "tenants": "*",
    "declared_tenants": true,
    "max_rows": 20,
    "max_tenants": 1,
    "drawdown": [{"table": "orders", "column": "total", "per": "tenant", "max_drop": "0.3"}],
    "stated_footprint": {"tolerance": "2", "blocking": false},
    "agreements": [{"sink": "stripe", "operation": "refunds.create", "field": "amount",
                    "table": "refunds", "column": "amount", "measure": "inserted",
                    "key": null}],
    "facts": []
  },
  "outbound": {
    "sinks": {"stripe": {"operations": ["refunds.create"], "max_attempts": 10,
                         "not_after_seconds": 900, "max_payload_bytes": 16384}},
    "max_per_plan": 5,
    "per_sink": {"stripe": 5},
    "caps": [{"sink": "stripe", "operation": "refunds.create", "field": "amount",
              "maximum": {"usd": "200.00"}, "per": "request", "currency_field": "currency"}],
    "recipients": [],
    "tenants": {"max_tenants": 1, "require_tenant": true, "fields": {}}
  },
  "windows": [
    {"name": "refund_dollars",
     "measure": {"kind": "request_sum", "sink": "stripe", "operation": "refunds.create",
                 "field": "amount"},
     "span_seconds": 86400, "limit": "2000", "per": "lineage"}
  ],
  "tools": {"statements": "bound",
            "bindings": [{"name": "lookup_order", "digest": "<hash>"},
                         {"name": "refund_order", "digest": "<hash>"}]},
  "recovery": {"reserve": "5.00000000", "step_estimate": "0.50000000", "max_steps": 4,
               "directives": [{"id": "stop-retrying", "sha256": "<hash>",
                               "answers": ["thrashing"]}],
               "revoke_on": ["runaway", "thrashing"], "revocable": null,
               "token_ceiling": 2048, "guidance": true, "channel": "system",
               "funding": null},
  "extension": {"max_grant": "50.00000000", "max_total": "150.00000000",
                "margin": "0.25", "ttl_seconds": 3600,
                "auto": {"max_grant": "5.00000000", "max_total": "15.00000000"}},
  "escalation": {"max_open": 5, "ttl_seconds": 86400,
                 "auto": [{"id": "trim-refused", "class": "refused",
                           "resolution": "accept_repair",
                           "when": {"exhaustive": true, "max_dropped": 1}}]},
  "delegation": {"max_depth": 1, "max_children": 4, "envelope": "50.00000000"},
  "issued_at": "2026-10-18T09:30:00.000000Z",
  "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}
}
```

- **F4-7.** `issuance` is one of two values:
  - `"quorum"`: signed by a principal, and established by approvals. A root needs
    `charter.quorums.issue` approvals; a child needs its parent's `roster.quorums.amend`.
    Either may also be established by an `amend` warrant (§7.6).
  - `"derived"`: signed by the service, and established by `refines(self, parent) = ∅`
    (section 5).
- **F4-8.** A root mandate (`parent = null`) is always `"quorum"`, and its
  `subject.funded_from` is a scope the charter's principals control. A child's
  `subject.funded_from` equals its parent's `subject.scope`.
- **F4-9.** Constraints on the roster:
  - `roster.principals` ⊆ the charter's principals.
  - Every quorum is between 1 and the roster's size.
  - `quorums.reduce` (cancel, decline, reject) ≤ `quorums.revoke` ≤ every granting quorum.
  - For a root, `quorums.amend` ≥ the charter's `quorums.issue`. A root's roster cannot
    widen the root with fewer approvals than issued it.
- **F4-10.** `windows[].per` is `"lineage"` or `"tenant"`. There is no `"scope"`. A
  per-scope window would count each derived child and each satellite separately, which is
  exactly the split section 5.3 forbids.
- **F4-11.** `tools.statements` takes two values:
  - `"bound"` admits only statements a pinned tool binding carries (§6.3,
    `bound_statements`);
  - `"any"` admits statements the agent authors, adjudicated by the checkers as interlock
    does today.
- **F4-12.** The canonical document is at most 65,536 bytes. The bound caps parsing work.
  It is not a signing constraint (F3-6).

## 4.3 Tool binding

A tool binding is what the agent may call, as a principal reviews it. The agent supplies
parameters only; the statements are the developer's.

```json
{
  "v": "FIDU1", "type": "tool",
  "name": "refund_order",
  "description": "Refund part or all of one order.",
  "input": {"type": "object", "additionalProperties": false,
            "required": ["amount", "order_id", "payment_intent"],
            "properties": {
              "order_id": {"type": "integer", "minimum": 1},
              "amount": {"type": "string", "pattern": "^[0-9]{1,6}\\.[0-9]{2}$"},
              "payment_intent": {"type": "string", "pattern": "^pi_[A-Za-z0-9]{8,64}$"}}},
  "kind": "plan",
  "tenant": "session",
  "effects": [
    {"id": "debit", "kind": "update", "table": "orders",
     "statement": "UPDATE orders SET total = total - :amount WHERE id = :order_id AND tenant = :tenant",
     "parameters": {"amount": "$.amount", "order_id": "$.order_id", "tenant": "$session.tenant"},
     "stated_rows": 1, "after": []},
    {"id": "record", "kind": "insert", "table": "refunds", "after": ["debit"],
     "statement": "INSERT INTO refunds (order_id, tenant, amount) VALUES (:order_id, :tenant, :amount)",
     "parameters": {"order_id": "$.order_id", "tenant": "$session.tenant", "amount": "$.amount"},
     "stated_rows": 1}
  ],
  "requests": [
    {"id": "stripe-refund", "after": ["record"], "sink": "stripe",
     "operation": "refunds.create",
     "payload": {"payment_intent": "$.payment_intent", "amount": "$.amount", "currency": "usd"},
     "compensation": null}
  ],
  "price": "0.00000000",
  "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}
}
```

- **F4-13.** `input` uses the JSON Schema subset that interlock's sink schemas use.
  Arguments are validated against it before anything is built, and an argument outside it
  refuses the call. That includes:
  - an unknown property;
  - a float;
  - a string that fails its pattern;
  - an integer outside its range.
- **F4-14.** Parameters are bound only from validated arguments (`$.<name>`) and from the
  session (`$session.tenant`, `$session.trajectory`).
  - The agent never supplies a statement, a table, a sink, an operation, or a tenant the
    session does not already hold.
  - A statement names its parameters as its substrate's driver binds them: `:name` for
    SQLite, `%(name)s` for psycopg on PostgreSQL. A statement uses one style, never both, and
    never a positional `?`. A binding is therefore written for one database.
  - `tenant: "session"` stamps every effect's `tenant_id` with the session's tenant. With
    `data.declared_tenants`, that refuses a row of any other tenant
    (`TenantIsolation(declared=True)`).
- **F4-15.** `kind: "passthrough"` binds a tool that fiduciary does not escrow, such as a
  read-only lookup or an upstream MCP tool.
  - It is metered at its `price`, through AgentGov.
  - It is counted by the cognitive breaker, and bounded by the governance velocity.
  - It carries no `effects` or `requests`. Section 2.5 states its limit.
- **F4-16.** The mandate pins each binding's digest. Changing a tool's statement or schema
  changes its digest, and the session refuses the binding until a mandate version pins the
  new digest. A code change to a tool is a change of authority, so it goes through the same
  quorum.

## 4.4 Ticket

```json
{
  "v": "FIDU1", "type": "ticket",
  "ticket_id": "c1d0…",
  "mandate": {"mandate_id": "2d0b…", "version": 3, "digest": "<hash>"},
  "class": "extension",
  "raised_at": "2026-10-18T10:02:11.120391Z",
  "not_after": "2026-10-18T11:02:11.120391Z",
  "observed": {"ledger": {"sequence": 18233, "head": "<hash>"},
               "log": {"seq": 412, "hash": "<hash>"}},
  "evidence": {
    "quote": {"id": "9a7e…",
              "record": {"log": "fiduciary-runtime", "seq": 88, "hash": "<hash>"},
              "needed": "0.42000000", "requested": "12.50000000",
              "spent": "237.11000000", "available": "0.08000000",
              "estimate": "milestones", "expires_at": "2026-10-18T11:02:11.120391Z"},
    "progress": {"committed": 41, "rows": 63,
                 "receipts": {"log": "fiduciary", "tree_size": 1201, "root": "<hash>"}}
  },
  "options": [
    {"resolution": {"action": "grant", "quote": "9a7e…", "amount": "12.50000000"},
     "digest": "<hash>", "quorum": 2},
    {"resolution": {"action": "decline", "quote": "9a7e…"},
     "digest": "<hash>", "quorum": 1}
  ],
  "agent": {"statement": "Need a bit more budget to finish the last three refunds."},
  "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}
}
```

- **F4-17.** `evidence` is typed per class (section 7.1). It holds ids, digests, counts,
  amounts and instants read from interlock and AgentGov, never prose. Every digest in it
  names an artifact the bundle can carry.
  `observed` names the heads the evidence was read at, and both are checked:
  - `log` is the FIDU1 log's head, the record the ticket's own `ticket.raised` follows.
    Replay refuses a ticket that observed any other.
  - `ledger` is the ledger's entry count and head hash. `verify`, given the ledger, names a
    ticket whose entry the ledger does not hold with that hash, or that does not precede
    the anchor of the ticket's record.
- **F4-18.** `agent.statement` is the only agent-authored text in a ticket, clipped to 512
  characters.
  - It enters no decision, no rule, no option, and no digest except the ticket's own.
  - A principal client shows it apart, labeled as the agent's claim (F7-3).
- **F4-19.** An option's `digest` is `SHA-256(canonical_bytes(resolution))`. A resolution
  names everything it does:
  - the action;
  - every target, with the state the ticket observed it at: a message's delivery-log head,
    a quote's record hash, a proposal's plan hash;
  - every amount.
- **F4-20.** A principal MAY approve a narrowing of an option instead: the same action, a
  subset of its targets, and an amount no larger. A narrowing is a resolution of its own,
  with its own digest. Approvals of different resolutions never add up.

## 4.5 Approval

```json
{
  "v": "FIDU1", "type": "approval",
  "approval_id": "e3f9…",
  "charter": {"charter_id": "6f1c…", "version": 1},
  "principal": "head-ops",
  "subject": {"type": "ticket", "digest": "<ticket digest>"},
  "resolution": "<resolution digest>",
  "decision": "approve",
  "issued_at": "2026-10-18T10:05:40.004211Z",
  "not_after": "2026-10-18T10:35:40.004211Z",
  "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}
}
```

- **F4-21.** `subject.type` is one of:
  - `ticket`;
  - `mandate` (an issuance or an amendment);
  - `revocation` (of a mandate digest);
  - `charter`;
  - `suspension` (of a principal's key). Its digest is that of the object
    `{"v": "FIDU1", "type": "suspension", "charter_id", "principal", "key"}`.

  `resolution` is a digest for a ticket, and `null` for every other subject: the subject is
  then itself what is approved.
- **F4-22.** `decision` is `approve` or `decline`. An approval is at most 1,024 canonical
  bytes.
- **F4-23.** `not_after` is the principal's own bound on the approval. It never extends a
  ticket's deadline or a quote's expiry. A warrant expires at the earliest of the three
  (§4.6).

## 4.6 Warrant

```json
{
  "v": "FIDU1", "type": "warrant",
  "warrant_id": "77b2…",
  "ticket": {"ticket_id": "c1d0…", "digest": "<hash>"},
  "resolution": {"action": "grant", "quote": "9a7e…", "amount": "12.50000000"},
  "resolution_digest": "<hash>",
  "basis": {"approvals": ["<approval digest>", "<approval digest>"]},
  "formed_at": "2026-10-18T10:05:41.310022Z",
  "not_after": "2026-10-18T10:35:40.004211Z",
  "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}
}
```

- **F4-24.** `basis` takes one of two forms:
  - `{"approvals": [...]}`: the digests of exactly the approvals counted, sorted;
  - `{"rule": {"mandate": "<digest>", "id": "<rule id>"}}`: for an autonomous resolution
    (§7.4).

  A verifier recomputes the basis from the documents. It never trusts the warrant's claim
  that the basis holds.
- **F4-25.** `not_after` is the minimum of:
  - the ticket's `not_after`;
  - every counted approval's `not_after`;
  - any expiry the resolution's target carries: a quote's `expires_at`, or a compensation's
    deadline unless the resolution is `compensate_late`.

## 4.7 Records: the FIDU1 log

Fiduciary's own acts are records in a hash-chained, signed log. It is built as ILOK1 is
(`interlock.records`), with FIDU1's framing:

```json
{"v": "FIDU1", "type": "record", "log": "fiduciary", "seq": 412,
 "kind": "warrant.formed", "issued_at": "…Z", "body": {"…": "…"},
 "prev": "<hash of record 411>",
 "sig": {"alg": "ed25519", "key_id": "…", "signature": "…"}}
```

| Kind | Its body names |
|---|---|
| `charter.adopted` | the charter digest, and the approvals counted |
| `principal.suspended` | the principal, the key, and the approvals counted |
| `mandate.issued` | the mandate digest, its `prev`, and the approvals or warrant counted (for a derived mandate, `derived` and the parent digest) |
| `mandate.bound` | the digest, the AgentGov delegation entry, the engine's chain, and the compiled digest |
| `mandate.rebased` | the descendant, and its new parent version |
| `mandate.suspended`, `mandate.revoked`, `mandate.expired` | the digest, the approvals for a revocation, and the AgentGov trip entries |
| `ticket.raised` | the ticket digest |
| `approval.recorded` | the approval digest, the principal, and the decision |
| `approval.refused` | the submission's digest, and why it never counts |
| `warrant.formed` | the warrant digest |
| `ticket.lapsed`, `ticket.declined` | the ticket digest |
| `execution.intent` | the warrant digest, its steps in order, and each step's precondition |
| `execution.applied`, `execution.refused`, `execution.abandoned` | the intent; and per step what it reached: ILOK1 intent and outcome hashes, ledger entry ids, plan hashes and receipt ids |
| `session.opened`, `session.closed` | a bridge session: its mandate version, tenant, trajectory and billing scope; at its close, every scope it billed, in order |
| `session.refused` | a call the session refused before anything was built or metered: the refusal's code, and the tool only when a version the session runs pins it, or it is a built-in |

- **F4-26.** Hashing and anchoring follow ILOK1:
  - A record's hash is the SHA-256 of the canonical bytes of the whole signed record.
  - The first record's `prev` is 64 zeros.
  - Each record is anchored into AgentGov as a zero-value `ANCHOR` entry on the scope
    `fiduciary`, with the memo `FIDU1 <log> <seq> <hash>`.

  A log truncated or rewritten after its records were anchored disagrees with the ledger
  (V10).
- **F4-27.** The log holds digests. The documents it names are stored beside it,
  content-addressed, and each is checked against its digest when read. Row data, payloads
  and transcripts never enter the log or any document (E3-11's posture).

---

# 5. Monotone delegation

## 5.1 The order

`child ⊑ parent` ("the child refines the parent") is a field-wise order on mandates.
`refines(child, parent)` returns every field where the order fails. It is empty exactly
when `child ⊑ parent`.

- **F5-1.** ⊑ is reflexive and transitive: each field's order is a partial order, and ⊑ is
  their product. A chain of derivations is therefore bounded by its root, at any depth.
- **F5-2.** A derived mandate is valid iff all three hold:
  - `delegation_problems(child, parent_in_force) = ∅`: `refines` is empty, and so are the
    three lineage rows of §5.2 (marked *lineage*), which relate a child to its parent
    rather than ordering two mandates' bounds. They are kept out of ⊑ because no mandate
    is its own child, and ⊑ is reflexive;
  - `parent.delegation` is not null;
  - the parent's derivation budget (`max_depth`, `max_children`) allows it.

  Validity rests on that predicate, not on the service's signature. The signature attributes
  the derivation and orders it. A compromised service that mints a derived mandate mints
  one no wider than its parent.
- **F5-3.** What bounds an amendment depends on where the mandate sits:
  - A root mandate's amendment is bounded by the charter's authority alone. Its issuers may
    widen it, which is why it needs their quorum.
  - A child's amendment is bounded by its parent: whoever issues it, it is ⊑ the parent's
    version in force.

  Only an amendment of the parent makes room for a wider child.

## 5.2 Field rules

| Path | Compiles to (section 6) | `child ⊑ parent` iff |
|---|---|---|
| `term.not_before`, `term.not_after` | the session's validity | `not_before ≥`, `not_after ≤` |
| `subject.funded_from` | the AgentGov delegation | *lineage:* equals `parent.subject.scope` |
| `roster.principals` | who may approve | ⊆ |
| `roster.quorums.<granting class>` | approvals required | ≥ (with ⊆ above, every child quorum is a parent quorum) |
| `roster.quorums.revoke`, `.reduce` | approvals required | ≤ (a child is never harder to stop) |
| `budget.envelope` | `delegate(funded_from, scope, envelope)` | ≤ `parent.budget.envelope`; *lineage:* ≤ `parent.delegation.envelope` |
| `budget.per_call`, `budget.max_output_tokens` | the `Interceptor` ceilings `hold` and `max_output_tokens` | ≤ |
| `budget.models` | the bridge's model allowlist | ⊆ |
| `governance.max_calls`, `.seconds` | `GovernancePolicy.max_calls_per_window`, `.window_seconds` | `max_calls ≤` and `seconds ≥` (any shorter window lies inside a longer one, so the child's bound implies the parent's) |
| `governance.fleet_velocity` | `GovernancePolicy.fleet_velocity` | true if the parent's is |
| `governance.cognitive.max_identical_repeats`, `.max_similar_streak`, `.min_cycle_repeats` | `CognitivePolicy` | ≤; and `min_cycle_repeats` ≥ 2 while the parent's `max_cycle_period` and `min_cycle_repeats` are both ≥ 2, since AgentGov's cycle detector does not run below 2 |
| `governance.cognitive.similarity_threshold`, `.result_similarity_threshold` | `CognitivePolicy` | ≤ (a lower threshold counts more pairs as stagnant) |
| `governance.cognitive.max_cycle_period` | `CognitivePolicy` | ≥ (longer cycles are looked for too) |
| `governance.cognitive.min_novelty_ratio` | `CognitivePolicy` | ≥ |
| `governance.cognitive.exempt_tools` | `CognitivePolicy` | equal: a child that judged a call its parent exempts would see it between two others, and a repeat the parent counts would break (F5-11 found it) |
| `data.tables` | `TableAllowlist`, `NoDelete`, `bound_statements` | tables ⊆; per table, operations ⊆ |
| `data.tenants` | `tenant_allowlist` | ⊆; `"*"` only under `"*"` |
| `data.declared_tenants` | `TenantIsolation(declared=…)` | true if the parent's is |
| `data.max_rows` | `BlastRadius` | ≤ |
| `data.max_tenants` | `TenantIsolation` | ≤ |
| `data.drawdown` | `ColumnValueGuard`, `TenantDrawdownGuard` | every parent guard `(table, column, per)` is kept, with `max_drop ≤` |
| `data.stated_footprint` | `StatedFootprint` | `tolerance ≤`; blocking if the parent's is |
| `data.agreements`, `data.facts` | `CrossEffectAgreement`, `FactAgreement` | ⊇: every parent rule, verbatim |
| `outbound.sinks` | `SinkAllowlist`, the narrowed `SinkRegistry` | sinks ⊆; per sink, operations ⊆, `max_attempts ≤`, `not_after_seconds ≤`, `max_payload_bytes ≤` |
| `outbound.max_per_plan`, `outbound.per_sink` | `OutboundCount` | ≤ (absent means unbounded, and is allowed only where the parent's is absent) |
| `outbound.caps` | `PayloadAmountCap` | every parent cap is kept; currencies ⊆; each `maximum ≤` |
| `outbound.recipients` | `RecipientAllowlist` | every parent rule is kept: `fields ⊇`, `domains ⊆`, `addresses ⊆`, tenant domains ⊆ per tenant, `subdomains` only if the parent allows it |
| `outbound.tenants` | `OutboundTenantIsolation` | `max_tenants ≤`; `require_tenant` if the parent's is |
| `windows` | `RateWindow`, namespaced (§5.3) | inherited, never listed: a child names only windows it adds |
| `tools.statements` | `bound_statements` | `"bound"` if the parent's is |
| `tools.bindings` | the session's tools | ⊆, by name and digest |
| `recovery` | `RecoveryPolicy` | null, or every bound tighter (see the list below this table) |
| `extension` | `BudgetGuard`, and fiduciary's caps | null, or every bound tighter (see the list below this table) |
| `escalation.max_open`, `.ttl_seconds` | ticket bounds | ≤ |
| `escalation.auto` | autonomous resolution | each child rule tightens the parent rule with its id: the same class and resolution, and every `when` bound at least as strict |
| `delegation` | derivation | null; or `max_depth ≤`, `max_children ≤`, `envelope ≤`; *lineage:* `max_depth ≤ parent.max_depth − 1` |

A non-null child `recovery` is tighter only if the parent's `recovery` is not null and every
one of these holds:

- `reserve ≤`, `step_estimate ≤`, `max_steps ≤`;
- `directives ⊆`, by id and digest;
- `revoke_on ⊇`;
- `revocable` is null (every granted tool), or ⊇ a non-null parent set;
- `token_ceiling ≤`, and not null if the parent's is not;
- `guidance` is on only if the parent's is;
- `channel` and `funding` are equal.

A non-null child `extension` is tighter only if the parent's `extension` is not null and
every one of these holds:

- `max_grant ≤`, `max_total ≤`;
- `margin ≤`, `ttl_seconds ≤`;
- `auto` is null, or the parent's `auto` is not null and each of the child's bounds ≤ the
  parent's.

## 5.3 Aggregates

A field-wise order bounds each plan, but not a sum. Five children each allowed $2,000 a day
can refund $10,000 a day between them. Three aggregates matter, and each is bounded across
the subtree.

- **F5-4. Money.** Every lineage scope is funded by `delegate`, so AgentGov's delegation
  invariant holds: no descendant spends or re-delegates more than its ancestors granted it,
  at any depth (agentgov ARCHITECTURE B.5). Extensions are bounded twice:
  - by `extension.max_total`;
  - by the parent's balance, since `BudgetGuard.grant` delegates them beside the scope, from
    its parent.
- **F5-5. Windows.** A mandate's window `w` compiles to an interlock `RateWindow`:
  - it is named `f<h>_<w>`, where `h` is the first 16 hex characters of
    `SHA-256(mandate_id ‖ "\n" ‖ w)`;
  - `per` is `"global"` for `"lineage"`, and `"tenant"` for `"tenant"`;
  - every descendant's engine is compiled with every ancestor's windows, unchanged.

  Interlock keeps one history per window name and key. Every plan of the subtree therefore
  adds to the ancestor's single history, and the ancestor's limit bounds the subtree's
  total, measured exactly under interlock's window locks. Two more consequences:
  - The name is derived from `mandate_id`, which does not change between versions, so an
    amendment never resets a window's history. A mandate cannot escape its window by being
    re-issued.
  - If two compiled names collide, their histories merge. Each window then holds the merged
    history to its own limit, which is stricter, never looser.
- **F5-6. Children and depth.** The service counts `delegation.max_children` and
  `max_depth` from the log, at derivation. Together they bound how many lineages a subtree
  holds. The money bounds them too (F5-4).

## 5.4 Rebasing and revocation

- **F5-7.** When a parent is amended, the service evaluates `refines` for every descendant,
  depth first. This happens in the same log transaction that records the amendment.
  - A descendant still ⊑ the new parent gets `mandate.rebased`.
  - A descendant no longer ⊑ the new parent gets `mandate.suspended`. Its scopes are tripped
    (§5.5) until a new child is derived under the new version.
- **F5-8.** A mandate may be revoked by any one of these:
  - its own roster's revocation quorum;
  - the revocation quorum of any ancestor in force;
  - the charter's revocation quorum.

  Revocation cascades to every descendant.

## 5.5 What revocation does, in interlock's and AgentGov's terms

Revocation, expiry and suspension each trip scopes in one AgentGov transaction:

- the subject scope, and each of the lineage's satellite scopes, are tripped;
- the reason is `FIDU1 revoked <digest>`, or `expired`, or `suspended`.

Satellites are the subject's siblings in AgentGov's tree, not its children, so tripping the
subject alone would not halt them. The trips have four consequences:

- **The subtree halts.** The subject's whole subtree is halted, because AgentGov's breaker
  latches over a subtree.
- **No commit.** A plan in flight does not commit. Interlock re-reads the breaker
  immediately before the substrate's commit, and refuses a halted scope (E3-8, E3-9).
- **No delivery.** A message of the lineage is held at delivery, not sent, because the
  relay checks the scope's breaker before every call.
- **The engine closes** at the plan boundary.

- **F5-9.** A revocation takes effect at the ledger sequence of its trip. The bundle
  verifier holds every receipt and delivery of the lineage to that sequence (V11).

## 5.6 Semantic monotonicity

The table in §5.2 is a syntactic sufficient condition. What it must imply is semantic:

> For every `child ⊑ parent` and every `(plan, diff)`: if the child's compiled checkers
> admit the plan, the parent's compiled checkers admit it.

**Why it holds.** Within one checker, it holds by construction wherever a field compiles to
a checker whose predicate is monotone in that field. A higher row limit admits everything a
lower one does, and a larger table set admits everything a smaller one does.

Across checkers, it holds because interlock unions every checker's violations and blocks on
any of them (E2-14, E2-15). Adding a checker therefore never admits a plan that one fewer
checker would refuse. A child that keeps every parent rule and tightens its parameters
admits a subset.

- **F5-10.** Fiduciary MUST test the semantic property, not only the syntactic one.
  Interlock's checkers are pure functions of `(plan, diff)` (E2-12), so the property is
  testable without a database.
  - A property test generates parents, narrowings and diffs (rows, tenants, outbound
    requests, window measures), and asserts the implication for every compiled checker
    type.
  - A second test widens one field at a time and asserts that `refines` names it.
  - If a checker's predicate turns out to be non-monotone in a parameter, the table is
    wrong and is changed.
- **F5-11.** The same property is stated for the cognitive breaker. On generated call
  sequences, a child's `CognitivePolicy` trips on every trajectory the parent's does.
  AgentGov's detectors are deterministic given their inputs, so this too is testable
  offline.

---

# 6. Compilation

## 6.1 Purity

```python
def compile(lineage: Sequence[Mandate], *, deployment: Deployment) -> CompiledMandate: ...
```

`lineage` is the mandate's version in force, and every ancestor's version in force, root
first. `deployment` is a snapshot of:

- the sink registry vouched for by interlock's last signed install (`operator.installed` in
  the operator log);
- the observed tables;
- the AgentGov ledger's genesis hash;
- the tool binding documents the mandate pins.

- **F6-1.** `compile` is pure: no I/O, no clock, no randomness, no environment. Two calls on
  equal inputs return equal results, in any process and under any `PYTHONHASHSEED`.
- **F6-2.** `CompiledMandate.digest` is the SHA-256 of the canonical JSON of the compiled
  description: every constructor below with its arguments, with sets sorted. It is
  fiduciary's own encoding and depends on no `repr`.
- **F6-3.** Compilation fails closed. These all make `compile` fail, and it lists every
  reason:
  - a table the substrate does not observe;
  - a sink or operation the installed registry lacks;
  - a pinned tool binding with no matching document;
  - a window name longer than 63 characters once namespaced.

  Nothing is bound.

## 6.2 What a mandate compiles to

Interlock 1.0.2 ships 17 checker types (`interlock.invariants.BUILT_IN_CHECKERS`). A mandate
compiles to every one that applies. The engine adds a `RateWindowCheck` for each window, and
fiduciary adds three checkers of its own (§6.3).

| Compiled | Constructed as | When |
|---|---|---|
| `TruncationGuard()` | — | always |
| `NoSchemaChange()` | — | always |
| `BlastRadius(max_rows)` | `data.max_rows` | always |
| `TenantIsolation(max_tenants, declared=…)` | `data.max_tenants`, `data.declared_tenants` | always |
| `TableAllowlist(tables)` | `sorted(data.tables)` | always |
| `NoDelete(granted_tables)` | the tables whose operations include `delete` | always |
| `StatedFootprint(tolerance=, severity=)` | `BLOCKING` if `blocking`, else `ADVISORY` | when present |
| `ColumnValueGuard(table, column, max_drop_fraction=)` | each guard with `per: "table"` | per guard |
| `TenantDrawdownGuard(table, column, max_drop_fraction=)` | each guard with `per: "tenant"` | per guard |
| `CrossEffectAgreement(sink, operation, field=, table=, column=, measure=, key=)` | `data.agreements` | per rule |
| `FactAgreement(kind, field=, table=, column=, key=, exempt=)` | `data.facts` | per rule |
| `SinkAllowlist({sink: operations})` | `outbound.sinks` | when any sink is mandated |
| `OutboundCount(max_per_plan, per_sink=)` | `outbound` | when bounded |
| `PayloadAmountCap(sink, operation, field=, maximum=, per=, currency_field=)` | `outbound.caps` | per cap |
| `RecipientAllowlist(sink, operation, fields=, domains=, addresses=, tenant_domains=, subdomains=, kind=)` | `outbound.recipients` | per rule |
| `OutboundTenantIsolation(fields=, max_tenants=, require_tenant=)` | `outbound.tenants` | when present |
| `RateWindow(name, span, limit, measure, per)`; the engine adds its `RateWindowCheck` | every window of the lineage, namespaced (F5-5) | per window |
| `fiduciary_mandate` | §6.3 | always |
| `tenant_allowlist` | §6.3 | unless `data.tenants` is `"*"` |
| `bound_statements` | §6.3 | unless `tools.statements` is `"any"` |

Fractions enter interlock's float parameters (`max_drop_fraction`, `tolerance`) as
`float(Decimal(s))`. The strings have at most six places, so the values round-trip exactly.

Beyond the checkers:

| Compiled | Constructed as |
|---|---|
| `SinkRegistry` | Each mandated sink's installed `SinkSpec`, narrowed: its operations cut to the mandate's, and `max_attempts`, `not_after` and `max_payload_bytes` lowered to the mandate's. `cost_per_call`, `idempotency`, `unknown_outcome` and the schemas stay as installed: the price is the registry's, never the mandate's. |
| `GovernancePolicy` | `max_calls_per_window`, `window_seconds` and `fleet_velocity` from the mandate; `max_depth` is the subject's depth plus `delegation.max_depth`; `trip_on_overdraft=True`, `trip_on_exhaustion=True` |
| `CognitivePolicy` | the mandate's cognitive fields, and AgentGov's defaults for every other field |
| `Interceptor` bounds | `hold ≤ per_call`, `max_output_tokens ≤ budget.max_output_tokens`, and the model allowlist |
| `RecoveryPolicy` | `reserve`, `step_estimate`, `max_steps`, `revoke_on`, `revocable`, `token_ceiling`, `guidance`, `channel` and `funding`; and the directives whose `sha256` matches the deployment's directive texts |
| `BudgetGuard` parameters | `margin` and `ttl`. Fiduciary enforces `max_grant` and `max_total` itself, before calling `grant`. |
| `ReceiptIssuer` | `issuer="fiduciary:<mandate_id>"` and `policy_epoch=version`, over fiduciary's receipt log |

- **F6-4.** `TruncationGuard` and `NoSchemaChange` are compiled into every mandate, and no
  field controls them. A mandate cannot omit them, so neither can any child.
- **F6-5.** `GovernancePolicy` is exact where a session has a governor of its own.
  - On PostgreSQL any number of governors can share one ledger, so each session opens a
    governor with its compiled policy.
  - On SQLite, one writer serves each ledger file, so sessions share one governor. Where a
    strictest value exists across the bound mandates (`max_depth`, and the booleans), the
    governor runs it. The bridge enforces each session's velocity itself, and the
    governor's own limit is then the backstop.

## 6.3 Fiduciary's checkers

Each of fiduciary's checkers has three properties:

- **It is pure.**
- **It holds only strings, integers and sorted tuples.** Its `repr` is therefore the same in
  every process, and so is the configuration digest interlock's receipts record for it.
- **It implements `hint`.** The agent is told which constraint refused it, as interlock's
  built-in checkers do.

| Name | Holds | Blocks when |
|---|---|---|
| `fiduciary_mandate` | `mandate_id`, `version`, the mandate digest, the compiled digest, and the admitted scopes (the subject and its satellites) | `plan.scope_id` is not an admitted scope |
| `tenant_allowlist` | the allowed tenants | a measured row or an outbound request carries a tenant outside them |
| `bound_statements` | `(kind, target, SHA-256(statement))` for every effect of every pinned binding | an effect is not one that a binding carries |

- **F6-6.** `fiduciary_mandate` is compiled into every engine fiduciary builds. Interlock
  records its configuration digest in every receipt's `decision.checkers`. That digest
  commits the receipt to the mandate digest and the compiled digest, and it works today,
  without U-1.

## 6.4 Binding to a deployment

`bind(version)` runs four steps.

1. **Read every precondition** before anything is written:
   - the version is `ISSUED` and within its term;
   - its charter is in force;
   - `deployment.ledger` matches entry 1 of the ledger;
   - `funded_from` exists and is not halted;
   - `compile` succeeds;
   - the governor's `policy` trips on overdraft and on exhaustion;
   - the governor's `max_depth` reaches the subject: the compiled
     `GovernancePolicy.max_depth` less the mandate's `delegation.max_depth`. The governor is
     one per ledger, so it cannot hold each lineage to its own depth. Derivation enforces
     the depth below the subject instead, since a child deeper than its parent allows does
     not refine it (§5.2).
2. **Delegate the envelope:**
   `governor.delegate(funded_from, scope, envelope, memo="FIDU1 bind <digest>", idempotency_key="fidu1-bind-<digest>")`.
   A retried bind writes nothing twice.
3. **Build the engine:**
   `EscrowEngine(substrate, checkers=compiled, chain=EscrowChain(<chains>/<mandate_id>.chain), anchor=…, receipts=ReceiptIssuer(…, policy_epoch=version), sinks=compiled registry, windows=compiled windows, inbox=…)`.
   There is one chain per lineage, resumed across versions. Interlock checks a repair
   against its own chain's `REPAIR_PROPOSED` record, so a repair proposed under version n can
   still be submitted under version n+1, where it is adjudicated from scratch.
4. **Record `mandate.bound`**, naming the delegation entry, the chain and the compiled
   digest.

**Rebinding.** Binding a later version of a bound lineage runs the same preconditions.
Then:
- a larger envelope delegates the difference from `funded_from` to the next free
  `{scope}/ext-N`, because AgentGov delegates to a scope once;
- a smaller one releases the difference from the subject, up to what it has available;
- the engine takes the new compiled policy at its next plan boundary (F1-3).

`mandate.bound` records what is delegated in all, and the entries written.

A process's engines follow the log, wherever the binding was written: before each session
call, and at each turn of the service, the binder reads the log's head and, when it has
moved, rebinds each engine whose lineage is bound to another version, opens one for each
lineage newly bound, and closes the engine of each lineage that no longer governs. So an
amendment bound from the command line, or by another node, reaches a running session at
its next call. *(Amended in 1.0.0: before, only the binder that wrote the binding rebound
its own engines, and a long-running bridge kept adjudicating under the version it opened
with.)*

An amendment of a lineage that is bound, or that has descendants, is issued by the binder
only. It issues the version, rebases or suspends the descendants in the same log
transaction (F5-7), and rebinds what is bound, parents first. Issued alone, it would leave
the binding on a version no longer in force.

The surface consumed:

| Call | Purpose |
|---|---|
| `interlock.wiring.open_substrate`, `open_governor`, `open_anchor`, `operator_signer`, `trusted_keyring`, `live_keyring` | Build the parts as interlock's runtime and daemon do |
| `interlock.config.InterlockConfig` (`sink_registry`, `tables`, `windows`, `inbox_keyring`, `key_roots`) | The deployment snapshot `compile` reads |
| `interlock.engine.EscrowEngine` (`execute`, `repair`, `recover`, `facts`) | The write path, one engine per lineage |
| `interlock.receipts.ReceiptIssuer`, `checker_records` | Issuing receipts, and recomputing what a receipt should record |
| `interlock.settlement.Settler(..., partition=True)` | Settling the deliveries of fiduciary's engines into fiduciary's receipt log |
| `interlock.operators.OperatorLog`, `Operator` (`release`, `cancel`, `requeue`, `compensate`, `resolve`) | Outbox actions under a warrant (§7.6) |
| `interlock.extension.BudgetGuard` (`authorize`, `grant`, `decline`); `interlock.recovery.RecoveryRuntime`, `RecoveryPolicy`, `Trip`, `breaker_reason`, `spent_out` | Quotes, recovery and halts |
| `interlock.deliveries.operations` (`held`, `heads`, `snapshot`, `compensables`) | Reading the outbox for triggers |
| `agentgov.BudgetManager` (`delegate`, `release`, `anchor`, `trip`, `reset`, `transaction`, `halted_by`, `control_events`, `audit_trail`, `entries`, `node`, `scopes`, `available`, `policy`, `publish_checkpoint`) | The money, and the clock |
| `agentgov.GovernancePolicy`, `CognitivePolicy`, `CognitiveBreaker`, `Interceptor`, `govern` | Governing the session's model calls |
| `agentgov.receipts.signing` (`Signer`, `Verifier`, `Keyring`, `Ed25519Signer`, `Ed25519PublicKey`, `RemoteSigner`, `HttpRemoteSigner`, `as_async`, `parse_key`, `verifier_of`); `agentgov.receipts.kms_aws.AwsKmsSigner` | Every signature |
| `agentgov.receipts` (`canonical_bytes`, `loads_strict`, `ReceiptLog`, `ReceiptBundle`, `verify_bundle`, `MerkleTree`, `verify_inclusion`) | Encoding, receipts and proofs |

## 6.5 Evidence that the compiled policy ran

- **F6-7.** Every receipt of a mandated scope MUST show all three:
  - `issuer = "fiduciary:<mandate_id>"`;
  - `decision.policy_epoch` equal to a version of that lineage that was in force at the
    receipt's ledger anchor;
  - a `fiduciary_mandate` checker record whose configuration digest equals what
    `checker_records` computes for that version's compiled `fiduciary_mandate`.
- **F6-8.** Once U-1 lands, every checker record the compiled set implies MUST be present,
  with that configuration digest. Extra records are allowed, since an extra checker only
  tightens (§5.6). A lineage's receipts issued before its deployment's Interlock had U-1
  precede, in the ledger, its first receipt with canonical set digests: those are held to
  the rule below, and noted; every receipt after it, to this one. *(Amended in 1.0.0.)*

  Until U-1, checkers whose configuration holds a set are checked by name only:
  `TableAllowlist`, `NoDelete`, `SinkAllowlist`, `RecipientAllowlist`, and the others that
  keep a `frozenset`. Their digests vary with the hash seed. Measured on interlock 1.0.2:
  - `table_allowlist` over four tables gave four different digests under four seeds;
  - `sink_allowlist` gave two.

---

# 7. Escalation, quorum and warrants

## 7.1 Triggers

| Class | Raised when | Read through | Typed evidence | Resolutions |
|---|---|---|---|---|
| `refused` | a session's plan is refused, and `EscrowEngine.repair` returns a proposal | `StageResult`, `Repair` | the refused receipt's id and digest; `blocked_by`; the proposal's plan hash; `kept` and `dropped`, with ids and causes; `exhaustive`; `trials` | `accept_repair`, `reject` |
| `held` | a message of the lineage is `held`: its scope was halted when a relay came to deliver it | `deliveries.operations(...).held(scope)` and `heads`; `governor.halted_by`; `interlock.recovery.breaker_reason` and `spent_out` | each message's id, head, sink, operation and payload hash; the halting scope, its reason, and whether that reason is `spent_out` | `release` (with `reset` first when the halt is spent out), `cancel` |
| `dead` | a message of the lineage is dead | the outbox's `snapshot` | the message's id, head and attempts, and the class of its last outcome | `requeue`, `cancel` |
| `undo` | the agent asks to undo a committed plan (`fiduciary_request`), or a principal opens one | `compensables(plan_id)` | the plan id; each delivered original's id, head, deadline and compensation operation; whether any of them is late | `compensate`, `compensate_late`, `reject` |
| `extension` | `BudgetGuard.authorize` returns an `ExtensionRequest`, directly or through `RecoveryRuntime.hold` or `RecoveryExhaustedError` | the quote, and its ILOK1 `extension.quoted` record | the quote's id and record hash; `needed`, `requested`, the spend, the estimate method, and `expires_at`; the receipt-log checkpoint its proof of work names | `grant`, `decline` |
| `amend` | the agent asks for a bound it lacks (`fiduciary_request`), or a principal proposes a version | the proposed mandate | its digest; `refines(proposed, current)` read both ways, as the diff a principal reviews | `amend`, `reject` |

- **F7-1.** One ticket per distinct evidence. A trigger whose typed evidence equals an open
  ticket's is folded into that ticket. It is never raised again.
- **F7-2.** `escalation.max_open` bounds a mandate's open tickets. A trigger past the bound
  raises no ticket, and its boundary stands:
  - the agent's plan stays refused;
  - its message stays held;
  - its quote stays unanswered.

  A ticket storm costs the agent its own progress, and costs principals nothing (A7).

## 7.2 Classes of resolution

| Resolution | Class | Auto-resolvable |
|---|---|---|
| `accept_repair` | neutral: the proposal is a sub-plan, adjudicated again from scratch | yes |
| `reject`, `cancel`, `decline` | reducing (`quorums.reduce`) | yes |
| `requeue` | granting: a message is sent again | yes, a bounded number of times per message |
| `release` of a message whose halt has cleared | granting | no |
| `reset` of a spent-out halt | granting | no |
| `reset` of any other halt (`reset_safety`) | granting | **never** |
| `compensate` | granting: a new request leaves, and a credit is booked | no |
| `compensate_late` | granting: a stale undo (E4-5) | **never** |
| `grant` | granting: new money | up to `extension.auto` |
| `amend` | granting | **never** |

## 7.3 Quorum

An approval **counts** toward resolution R of ticket T when, and only when, all six of these
hold:

1. Its signature verifies under the full public key that its named charter version pins for
   its `principal` (F3-4).
2. That principal is in the roster of the mandate version T names, and the key was not
   suspended by any record before the one that records this approval. For a revoked mandate,
   the roster is that of the nearest ancestor in force; for a root, it is the charter's.
3. `subject` is `{"type": "ticket", "digest": digest(T)}`, and `resolution` is `digest(R)`.
   R is an option of T, or a narrowing of one (F4-20).
4. `decision` is `approve`.
5. It was recorded (`approval.recorded`) before T's `not_after` and before its own
   `not_after`.
6. It is its principal's latest decision on T recorded before the warrant formed.

- **F7-3.** A principal's client MUST render a ticket from the exact canonical bytes whose
  digest it signs, never from a summary the service supplies. It renders the typed evidence
  in fixed templates, and shows `agent.statement` apart, labeled as the agent's unverified
  claim (G8).
- **F7-4.** The agent's channel has no approval surface. No MCP tool, bridge method or
  session handle submits, forwards or relays an approval. Approvals arrive only through the
  principal intake (§10.2), and they count only by signature. An agent that reaches the
  intake must still forge a principal's signature.
- **F7-5.** Distinctness is by roster principal.
  - Approvals from one principal count once, however many there are.
  - Approvals under two key ids that collide count once.
  - The roster binds each name to one full public key, and the charter forbids one key under
    two names (F4-1).
- **F7-6.** A warrant forms the moment counted approvals reach the quorum of R's class in
  the version T names. The service records `warrant.formed` in the same log transaction as
  the approval that completes it. Approvals recorded after that are still recorded, and
  change nothing.
- **F7-7.** A ticket becomes `DECLINED` when declines make the quorum unreachable for every
  option: the roster's size, less the principals who declined, falls below each option's
  quorum. It becomes `LAPSED` at its deadline. A warranted ticket also lapses when its
  warrant reaches its `not_after` with no `execution.intent`: nothing will execute it
  *(amended in 1.0.0; before, such a ticket stayed warranted)*. Either state is terminal
  (F1-6).
- **F7-8.** Reducing resolutions need `quorums.reduce` approvals. Granting ones need their
  class's quorum, and every granting quorum is at least `quorums.revoke` (F4-9). Stopping is
  never harder than starting.

## 7.4 Autonomous resolution

A mandate's `escalation.auto` rules let the service resolve a ticket without any principal.
The principals have already approved those resolutions: the rule is part of the mandate
their quorum issued.

```json
{"id": "trim-refused", "class": "refused", "resolution": "accept_repair",
 "when": {"exhaustive": true, "max_dropped": 1}}
```

- **F7-9.** A rule is a pure predicate over the ticket's typed evidence, in a closed
  vocabulary per class:

  | Class | Vocabulary |
  |---|---|
  | `refused` | `exhaustive`, `max_dropped`, `causes ⊆` |
  | `dead` | `max_requeues`, `outcome ∈` |
  | `extension` | `max_amount`, with `extension.auto.max_total` over the version's life |

  No rule reads `agent.statement`, or anything outside the ticket.
- **F7-10.** A warrant formed by a rule has `basis.rule`, and the verifier recomputes the
  predicate over the ticket (V8). Section 7.2 marks some resolutions **never**; they have no
  rule vocabulary, so no rule can name them.
- **F7-11.** An autonomous `grant` is bounded twice:
  - per quote, by `extension.auto.max_grant`;
  - over the version, by `extension.auto.max_total`. This total is counted from the log, so
    a restart does not reset it.

## 7.5 Execution

No transaction spans the outbox, the operator log and the ledger. Execution is therefore
not atomic across systems. What holds instead is exactly-once execution with exact
resolution. It runs in two phases, as interlock's operators act:

1. **`execution.intent`.** The record holds the warrant's digest, its steps in order, and
   each step's precondition as the ticket observed it: a head, a quote's record hash, a plan
   hash, a scope's halt. It is written before anything is touched, and before the warrant's
   `not_after` by the service's clock.
2. **Act**, step by step. Each step carries the warrant's digest wherever the system it
   touches signs or hashes it (§7.6). A step whose precondition no longer holds is not
   taken.
3. **The outcome**, one of two records:
   - `execution.applied`: each step reached what it named;
   - `execution.refused`: a precondition failed, or a system refused, and nothing after that
     step ran.

- **F7-12.** A warrant has at most one `execution.intent`; the log refuses a second.
  Underneath it, a second act is refused by interlock's heads, by AgentGov's idempotency
  keys and by the guard's single answer.
- **F7-13.** `resolve()` runs at start, and before every execution. For every intent with no
  outcome, it reads what the warrant's digest reached, then appends `applied` or
  `abandoned`. It looks in four places:
  - ILOK1 intents whose `reason` names the digest, and their outcomes;
  - ledger entries whose memo names it;
  - extension records whose `approved_by` names it;
  - the escrow chain's record of the proposal's plan hash.

  Nothing is guessed. A step with no evidence did not happen. A step whose ILOK1 intent is
  unresolved is first resolved by interlock's `Operator.resolve`. Evidence that is not
  settled yet leaves the intent open for a later run: an ILOK1 intent with no outcome, an
  escrow commit intent the engine's recovery has not answered, or an operator log another
  process holds.

  An execution holds a claim on its warrant (`execution:<digest>`) from its intent to its
  outcome: a file lock on SQLite, and a session advisory lock on PostgreSQL. A killed
  process loses it. `resolve()` skips an intent whose claim a live process holds, since
  that process is still acting on it.
- **F7-14.** A quote is an `extension.quoted` record of the runtime log. Where Interlock
  adopts quotes (U-3), a `BudgetGuard` opened over that log holds every quote in it that no
  record answers and that has not expired: a restart, or the writer role moving to another
  node, loses none, and a quote is answered once, whichever guard answers it. Where it does
  not, a guard holds only the quotes it issued, in memory; a step that needs a quote this
  process no longer holds is `refused`, with that reason, its ticket lapses, and the agent
  asks again. A repair proposal is held in memory either way, since it carries row data
  (F4-27): an `accept_repair` step whose proposal a restart lost is refused, and its ticket
  lapses, in the same way.
- **F7-15.** Where Interlock takes the heads (U-4), the executor gives the operator the
  heads the ticket observed, and an outbox step at a head that has moved since is refused
  before anything is signed: `execution.refused`, no ILOK1 intent names the warrant, and
  the message is free for a new ticket. Where it does not, after each outbox step fiduciary
  compares the heads the ILOK1 intent signed with the heads the ticket observed. A mismatch
  means the action reached a state no principal saw. It is recorded in
  `execution.applied`, and V8 names it, as it names one in any log written so.

## 7.6 Each resolution against interlock and AgentGov

| Resolution | Executed as | Where the warrant's digest is bound |
|---|---|---|
| `accept_repair` | `engine.execute(repair.proposal)`, unchanged | The record names the proposal's plan hash, and its receipt names the refused receipt as `repair_of`. |
| `release` | `Operator.release(ids, reason="FIDU1 warrant <digest>")` | The ILOK1 `operator.intent` body, which the executor signs and AgentGov anchors |
| `reset` | in one `governor.transaction()`: where AgentGov takes a memo (U-5), `reset(scope, memo="FIDU1 warrant <digest> reset <scope>")`; otherwise `anchor("fiduciary", <that memo>)`, then `reset(scope)` | The `CIRCUIT_RESET` entry's own memo; or the anchor entry immediately preceding it, since nothing interleaves inside a transaction. One entry names the warrant either way, and a verifier reads both. |
| `cancel` | `Operator.cancel(id, reason="FIDU1 warrant <digest>")` | ILOK1, as for `release` |
| `requeue` | `Operator.requeue(id, reason="FIDU1 warrant <digest>")` | ILOK1 |
| `compensate`, `compensate_late` | `Operator.compensate(ids, plan_id=, late=, reason="FIDU1 warrant <digest>", registry=compiled sinks)` | ILOK1. The settler credits the compensation under that authority. |
| `grant` | `BudgetGuard.grant(quote, approved_by="FIDU1 warrant <digest>", amount=)` | The AgentGov `fund` or `delegate` memo (`extension … approved by FIDU1 warrant <digest>`), and the ILOK1 `extension.granted` record |
| `decline` | `BudgetGuard.decline(quote, declined_by="FIDU1 warrant <digest>", reason=)` | ILOK1 `extension.declined` |
| `amend` | `mandate.issued` for the approved version, rebound at the next plan boundary | The FIDU1 log |
| `reject` | recorded only | The FIDU1 log |

- **F7-16.** `Operator` is opened for one execution, and closed after it. The operator log
  has one writer at a time, and interlock's vacuum and human operators open it too. A claim
  held elsewhere delays the step and fails nothing.
- **F7-17.** Interlock operators and AgentGov governors outside fiduciary keep their powers.
  Some acts on the lineage's footprint name no warrant:
  - an operator intent without `FIDU1 warrant`;
  - a `CIRCUIT_RESET` with no anchor before it;
  - a delegation into a lineage scope with no mandate memo.

  Such an act is ambient authority. The bundle names it (V7), and lists it as break-glass
  when the actor's key is in the charter's `break_glass` (F4-6).

---

# 8. The bridge

## 8.1 Sessions

```python
from fiduciary import Fiduciary

fid = Fiduciary.from_config("fiduciary.toml")
with fid.session("support-agent", tenant="acme") as session:
    client = session.govern(anthropic.Anthropic())   # metered, breaker-guarded, model-allowlisted
    tools = session.tool_definitions()               # the Messages API tool list, from the pinned bindings
    ...
    result = session.call("refund_order", {"order_id": 42, "amount": "19.99",
                                           "payment_intent": "pi_3Q1x9f2bK8"})
    # result.committed, result.feedback (sanitized), result.ticket (when it escalated)
```

Out of process:

```
fiduciary mcp serve --mandate support-agent --tenant acme              # stdio
fiduciary mcp serve --mandate support-agent --tenant acme --http :8765  # Streamable HTTP
fiduciary mcp proxy --mandate support-agent -- <upstream MCP server command>
```

A session is one mandate version in force, one tenant (or none), one trajectory and one
billing scope. It is recorded (`session.opened`) before its first call.

- **F8-1.** A session never exposes the engine, the substrate, the governor, the outbox or
  any signer. Its methods are `call`, `govern`, `tool_definitions`, `tickets` and `close`.
- **F8-2.** `session.call(name, arguments)` works in four steps:
  1. The binding must be one the version pins.
  2. The arguments must validate against its schema.
  3. The plan is built with `PlanBuilder` from the binding:
     - `scope_id` is the billing scope;
     - `trajectory_id` is the session's;
     - `intent` is `FIDU1 tool <name> <digest[:16]>`;
     - each effect's `tenant_id` comes from the session.
  4. The lineage's engine runs the plan.

  The result carries interlock's sanitized feedback (`AgentFeedback`, `RepairFeedback`). It
  never carries the operator's evidence: the diff, the verdict, other tenants, or window
  contents.
- **F8-3.** A refused plan, a quote and the end of a recovery escalate as §7.1 says, and the
  result says so: it names the ticket's id, its class and its deadline. It never says the
  bound's value, or who the approvers are.
  - A warrant that a rule forms as the ticket is raised is executed in the session's own
    process, which holds the proposal or the quote it names (F7-14).
  - A session waiting on its `extension` ticket asks once: a halt while the ticket is open, or
    its warrant unexecuted, names that ticket again and quotes nothing more.
- **F8-12.** A session refuses a call before anything is built or metered, and records the
  refusal (`session.refused`), when:
  - no binding the lineage's version in force pins has its name, and it is no built-in
    (`unpinned`);
  - its arguments fall outside the binding's schema or the canonical domain, or exceed 16 KiB
    as canonical JSON (`arguments`);
  - the binding stamps the session's tenant, and the session has none (`tenant`);
  - a recovery step revoked it (`revoked`);
  - it is past `governance.max_calls` in `governance.seconds`, counting model calls and tool
    calls alike (`velocity`);
  - a model call names a model the mandate does not list, or one with no configured price
    (`model`);
  - a trigger finds the lineage at `escalation.max_open` (`max_open`).

  The record holds no agent text: a tool name is recorded only when a version the session
  runs pins it. A plan the engine refuses is recorded by interlock instead, in its ARC1
  receipt and its escrow chain.

## 8.2 MCP

- **F8-4.** `tools/list` returns the version's pinned bindings, each with its `description`
  and `input` schema exactly as the binding document holds them. It also returns two
  built-in tools, and nothing else:
  - `fiduciary_ticket_status`: read-only. It reports a ticket's state, by id, for this
    session's tickets.
  - `fiduciary_request`: raises an `amend` or `undo` ticket, with a statement of at most 512
    characters.
- **F8-5.** `tools/call` is `session.call`. A refusal is a result with `isError: true` and
  the sanitized feedback as its text. A malformed request is a JSON-RPC error, and no plan is
  built.
- **F8-6.** Over Streamable HTTP:
  - each session is bound to one bearer token, issued when the session opens and valid for
    that session alone;
  - the listener speaks TLS, or listens on loopback only;
  - a token authorizes the mandate's tools for its session, and nothing else (A8).

  The bridge serves one endpoint, `POST /mcp`, and answers with `application/json`. It
  refuses a request with no token or another token (401), one naming another session's
  `Mcp-Session-Id` (404), any other path (404) or method (405), a body past 1 MiB (413),
  and a browser request whose `Origin` is not loopback (403), so a loopback listener is
  not reachable by DNS rebinding. `fiduciary mcp serve --http` writes the token to
  `--token-file` (mode 0600), or to standard error.
- **F8-7.** `proxy` fronts an upstream MCP server. An upstream tool is exposed only where
  the mandate pins a `passthrough` binding naming it, and every call to it is metered and
  counted (F4-15). An upstream tool the mandate does not pin is not listed, and a call to it
  is refused.

## 8.3 Model calls, halts and recovery

`session.govern(client)` wraps the SDK client with AgentGov's `govern` and `Interceptor` on
the billing scope. It applies the compiled `CognitiveBreaker`, the `hold` and
`max_output_tokens` ceilings, and the model allowlist. A halt is handled by the runtime
interlock already provides:

```
 AgentGov halt / interlock refusal ──▶ Trip.of(error) / Trip.refused(result)
        │
        ▼
 RecoveryRuntime(governor, scope, compiled RecoveryPolicy, runtime records, guard=BudgetGuard)
        │ recover(): one rung per step, append-only, recorded before the call
        ▼
 reserve spent ──▶ RecoveryExhaustedError carrying a quote ──▶ extension ticket (§7.1)
        │                                                          │ warrant: grant
        ▼                                                          ▼
 the halt stands                                 RecoveryRuntime.extend(grant): bill the new scope
```

- **F8-8.** The session's billing scope moves only to a scope the lineage owns: its recovery
  scope, or an extension's `{scope}/ext-N`, through `RecoveryRuntime.extend`. Each move is
  recorded, and `fiduciary_mandate` admits exactly those scopes.
- **F8-9.** A safety halt (one whose reason is not `spent_out`) is never answered with
  money. Interlock's `BudgetGuard` refuses to quote it, and fiduciary raises no extension
  ticket for it. Its only exits are recovery's rungs and a `reset_safety` warrant.
  - A recovery that has answered a safety halt ends with its reserve. When the reserve runs
    out, interlock's recovery may still sign a quote for more; fiduciary never escalates it.
  - With no recovery policy, a spent-out halt is quoted on the billing scope directly, and a
    grant moves billing to the `{scope}/ext-N` it funded.
  - The lineage's recovery runtime is opened at its first halt and shared by every session
    that halts while it is open. The last of those sessions to close closes it: an unsettled
    step's hold is voided, and the unused reserve returns to the scope that funded it.
  - Interlock's runtime follows one transcript, so while one session is in recovery another
    session's step is refused; that session's halt stands, though its calls bill the shared
    reserve.
  - A halt after the lineage's recovery has closed opens it again, where Interlock reopens
    a closed recovery (U-6): a fresh reserve, funded as the first was, on
    `{scope}/recovery/ext-N`, which sessions bill from then on and `fiduciary_mandate`
    admits; the ladder begins again; and only this recovery's steps decide whether it
    answers a safety halt. Where it does not, a later halt is answered as if the lineage
    had no recovery policy.

## 8.4 Trust boundary

- **F8-10.** In process, the bridge shares an address space with credentials.
  - An agent whose model only chooses tool calls and arguments is bounded by the bridge.
  - An agent that executes code it writes is not bounded by it. Such an agent MUST be
    bridged out of process: over MCP, via stdio to a separate process, or via HTTP.

  `Fiduciary.session` refuses to start in process unless the configuration says
  `in_process = "tool-calls-only"`, so the choice is written down.
- **F8-11.** Out of process, the agent's process holds an MCP connection and its session
  token, and nothing else of fiduciary's.

---

# 9. The audit bundle

## 9.1 Contents

`fiduciary export-bundle --mandate <id> [--from <seq>] [--to <seq>]` writes a bundle for a
lineage and its descendants. `docs/BUNDLE_FORMAT.md` specifies its bytes.

| Member | Holds |
|---|---|
| `manifest.json` | every member's path, type and SHA-256; the lineage; the range; the ledger cut; the exporter |
| `charter/` | every charter version, and its approvals |
| `mandates/`, `tools/` | every version of the lineage, of its ancestors and of its descendants; their approvals; the tool bindings they pin |
| `tickets/`, `approvals/`, `warrants/` | every ticket of the lineage; every approval recorded on one, counted or not; every warrant |
| `log.jsonl`, `intake/` | the FIDU1 log from record 1 to the range's last: each decision in range is taken again from the state every earlier record built. The intake items its approvals answer. |
| `ledger/` | every AgentGov entry on the lineage's footprint, meaning its scopes, its satellites and the `fiduciary` anchors. Each entry comes with an RFC 9162 inclusion proof against a ledger checkpoint of entries 1 to the anchor of the range's last record, signed by the ledger's checkpoint key (log id `agentgov-ledger:<entry 1>`), and that checkpoint's cosignatures. |
| `receipts/` | every ARC1 action and delivery receipt of the lineage, as a `ReceiptBundle` with its inclusion proof and checkpoint; the receipt log's key transitions; witness cosignatures |
| `ilok1/` | the operator log's records that name a warrant of the lineage; the install records that vouch for the registry; key registrations and revocations; the lineage's runtime records (recovery and extension) |
| `escrow/` | the lineage's escrow chain records, which are digests only. They place each stage in ledger order. |
| `deployment.json` | what compiling the lineage reads from the deployment: tables, sinks, directives, and its funding scopes' depths |
| `keys.json` | every public key the bundle is verified under |

- **F9-1.** The bundle carries no row data, payloads, transcripts or secrets. It carries
  only what its source logs carry, which is digests (F4-27, E3-11).
- **F9-2.** A bundle is self-contained for the claims it makes. It does not make claims that
  need the live database, such as the outbox's full delivery logs, or interlock's
  `verify_operators` over every row. Its report names those claims. The exporter MAY attach
  interlock's verifier reports, and the bundle verifier labels them as the exporter's
  attestation, not as proof.

## 9.2 Format

The bundle is an uncompressed POSIX tar, with members in sorted order, mode 0644, owner 0
and mtime 0. One lineage over one range therefore exports to identical bytes.
`manifest.json` is canonical JSON, and an exporter MAY sign it as a FIDU1 `bundle` document
with the service key. The verifier never relies on that signature: it checks every member
against its digest, then on the member's own terms. A signature that is present must verify
(V9).

## 9.3 `fiduciary verify-bundle`

```
fiduciary verify-bundle acme-support.fidb --charter <pinned digest> [--keys keys.json] [--witness <key>] [--skew <seconds>]
```

The auditor supplies the charter digest, from outside the bundle. Principals' keys are the
charters' own, and the pinned digest vouches for them. The service, ledger checkpoint,
receipt, runtime and operator keys are not in any charter: the auditor pins them with
`--keys`, and `keys.json` must agree with what is pinned (V1). A role left unpinned is
verified under `keys.json`'s key, and the report says it is the exporter's statement.

| Check | Holds when | Fails as | Exit |
|---|---|---|---|
| V1 schema | every member parses strictly (F3-1) and matches its manifest digest | `malformed` | 3 |
| V2 charter | the chain from the pinned digest verifies: approvals, quorums, suspensions | `authority` | 5 |
| V3 lineage | versions link; each issued version carries its quorum; each derived one is ⊑ its parent in force; no equivocation; terms nest | `authority` / `monotonicity` | 5 / 6 |
| V4 compiled policy | `compile` over the bundle's documents gives the compiled digest each `mandate.bound` names, and every receipt meets F6-7, and F6-8 when the manifest says the exporter's checker digests are canonical (U-1) | `policy` | 6 |
| V5 receipts | agentgov's `verify_bundle` passes offline for each receipt: signature, inclusion, witness, key schedule | `signature` / `evidence` | 4 / 8 |
| V6 ledger | each entry's inclusion proof verifies against a checkpoint signed by the ledger's checkpoint key; each memo the other checks rely on is in a verified entry | `evidence` | 8 |
| V7 completeness | every ledger entry on the footprint, every receipt, and every ILOK1 operator act on the lineage's messages is explained (see below). Anything else is named, and labeled break-glass when its key is declared so. | `ambient` | 7 |
| V8 warrants | each warrant's basis is recomputed (§7.3, §7.4); each is executed once; its intent precedes its `not_after`; each step's evidence matches the resolution's targets, heads and amounts | `authority` / `time` | 5 / 9 |
| V9 records | the FIDU1 log verifies: sequence, links, and signatures under the service key; a signed manifest's signature holds | `signature` | 4 |
| V10 anchors | every FIDU1 record in range is anchored, and every anchor names a record the log holds with that hash | `evidence` | 8 |
| V11 revocation | no receipt of the lineage commits, and no delivery is made, after its revocation's trip sequence. Plans in flight at the trip, by `STAGE_OPENED` order, must show no commit. | `revocation` | 9 |
| V12 ordering | every instant the service wrote agrees with the ledger order of its anchor, within the configured skew | `time` | 9 |

For V7, an act is explained when it traces to one of:

- a bind;
- a plan's settlement, traced to its receipt;
- a session's metered call;
- a recovery or extension record;
- a warrant's step.

The other exit codes:

- exit 0 is a pass;
- exit 1 is a usage error;
- exit 2 is a bundle the verifier could not read.

A failing run prints every check, not only the first that fails. It exits with the code of
the first failing check in the order V1 to V12.

- **F9-3.** `verify-bundle` needs no network and no database. Beyond the standard library,
  it imports only `fiduciary`, `interlock` and `agentgov`. The format is specified so that
  an implementation in another language needs nothing else. `vectors/fidu1/` holds valid
  and invalid documents, records and bundles; each invalid one names the check and exit code
  it must fail with.

## 9.4 Time

Every claim about order is a claim about AgentGov ledger sequence:

- each FIDU1 record's anchor;
- each receipt's `anchors.agentgov`;
- each escrow record's `agentgov_sequence`.

As ESCROW_SPEC §3.4 shows, an anchor proves a lower bound: a record cannot predate the head
it names. The anchor written after it in the ledger is an upper bound. Instants inside
documents are the writer's claim. They are held to that order within a skew (V12), and
never trusted alone.

---

# 10. Architecture of `interlock-fiduciary`

## 10.1 Components

| Component | Module | Does | Writes |
|---|---|---|---|
| FIDU1 | `fiduciary.fidu1` | Encoding, envelopes, domains; signing and verifying through agentgov's signers; strict parsing | — |
| Documents | `fiduciary.documents` | `Charter`, `Mandate`, `ToolBinding`, `Ticket`, `Approval` and `Warrant`, as frozen dataclasses with `to_json` and `from_json` | — |
| Order | `fiduciary.refines` | `refines(child, parent)`: the §5.2 table as code | — |
| Compiler | `fiduciary.compiler` | `compile`, `CompiledMandate`, and fiduciary's three checkers | — |
| Log and store | `fiduciary.log`, `fiduciary.store`, `fiduciary.sqlite`, `fiduciary.postgres` | The FIDU1 log, documents, projections and intake | the log; AgentGov anchors |
| Binder | `fiduciary.binding` | `bind`, rebind, rebase, suspend, revoke, expire | delegations, trips; engines |
| Escalation | `fiduciary.escalation` | Tickets from triggers; intake; quorum; warrants; rules | tickets, warrants |
| Executor | `fiduciary.executor` | Two-phase execution, and `resolve` | ILOK1 operator records; ledger entries; extension records |
| Bridge | `fiduciary.bridge`, `fiduciary.mcp` | Sessions, tool bindings, and the MCP server and proxy | sessions; plans, through the engines |
| Bundle | `fiduciary.bundle` | `export_bundle`, `verify_bundle` | bundles |
| Service | `fiduciary.service`, `fiduciary.cli` | `fiduciary serve`: the binder, escalation, executor, settler, intake and bridge under one supervisor | health; metrics |

## 10.2 Stores: SQLite and PostgreSQL

Both stores hold the same tables and enforce the same rules, and every test runs on both.

| Table | Holds | Written by |
|---|---|---|
| `documents` | digest, type, canonical bytes | the service; insert only |
| `log` | `(log, seq)`, hash, prev, kind, record bytes | the service; append only, at `head + 1` with `prev = head` |
| `intake` | submitted approvals as received; untrusted | the intake role; insert only |
| `mandates`, `tickets`, `approvals`, `warrants`, `executions`, `scopes`, `sessions` | projections of the log | the service, in the same transaction as the record each follows from |

- **F10-1.** The log is the truth, and the projections follow from it. `fiduciary verify`
  folds the log and compares the result with the projections. A difference means a
  projection was edited around the service, and `verify` names it.

  A service keeps the state it has folded from the log, not the records, and folds only
  the records appended since. It reads from the record its state ended at, which must be
  the one the store holds there, and the first new record must follow it, by sequence and
  hash; a log that is not so is read again whole. A state once read never changes: what
  follows is folded into a copy. The state answers each question a decision asks (what is
  open, what is unexecuted, who decided what) from an index, so a turn of the service
  costs what is due, not what the log holds. `verify` and `verify-bundle` read
  everything, every time. *(Amended after the 1.0.0 freeze. 1.0.0 kept every record it
  had read, and folded the whole log for each question: a turn, and each tool call,
  took time in proportion to the log.)*
- **F10-2.** The database enforces append-only:
  - On PostgreSQL, the service role adds to `documents` and `log` only through functions
    that check the link and the sequence. No role may `UPDATE` or `DELETE` either table.
  - On SQLite, triggers refuse both.

  An owner who writes around this produces a log that does not verify, or one that disagrees
  with its anchors (V9, V10).
- **F10-3.** The database holds no secret. Keys live in files beside the configuration, or
  in key services named by `[signers.<name>]`, as interlock's do.
- **F10-4.** There are three roles:
  - `fiduciary_service`: appends to the log, and writes the projections.
  - `fiduciary_intake`: inserts into `intake`, and nothing else.
  - `fiduciary_audit`: reads.

  The principal API connects as intake. It can submit an approval, but cannot record one.
  The service verifies each submission, then records it (`approval.recorded`) or refuses it
  (`approval.refused`).

- **F10-18.** A store upgrades in place. Its `meta` row names its schema version: 1 (0.1),
  2 (0.2), 3 (0.3) or 4 (0.4 to 1.x). Opening an older store with a later release adds what
  that release's schema has (tables, functions, grants), and raises the version, in one
  transaction; a newer store than the release is refused. Records are FIDU1 and never
  change: the log a 0.x release wrote verifies under 1.x, its projections fold as they did,
  and its lineages export bundles that verify. *(Proven by stores each 0.x release wrote
  itself, `tests/upgrades/`, on SQLite and PostgreSQL. A plan that 0.2 or 0.3 committed,
  before the bridge existed, is named by V7: no pinned tool's call admitted it.)*

## 10.3 Concurrency

- **F10-5.** Each FIDU1 log has one writer at a time.
  - On SQLite, that is `BEGIN IMMEDIATE`.
  - On PostgreSQL, it is an advisory lock on the schema, taken by every write transaction
    and held to its end.
  - Every decision is taken from the state read under that lock, in the transaction that
    writes its records: what it read is still the head when it writes. A turn with nothing
    to write takes no lock: it reads first, and locks only for what is due. *(Amended in
    1.0.0. The binder's `bind`, `issue`, `revoke` and `expire` read their state before the
    lock, so two processes could both expire a lineage; and every idle turn of the intake
    took the lock once per submission ever received.)*
  - In a cluster, one node leads the writer role (interlock's `cluster.Leadership`). A node
    that is gone loses the role when the database ends its session.
- **F10-6.** Locks are taken in one order:
  1. the FIDU1 writer;
  2. an engine's stage;
  3. the interlock operator log's claim;
  4. the outbox's rows;
  5. AgentGov's writer lock.

  No step takes an earlier lock while holding a later one. AgentGov's transaction wraps only
  the ledger calls of one step (§7.6, `reset`), never a call to another system.
- **F10-7.** An engine stages one plan at a time (interlock), and a lineage has one engine.
  Sessions of one lineage queue for it, and their plans are adjudicated in arrival order.
- **F10-8.** Executions of different warrants run concurrently. If two touch the same
  message, interlock serializes them: each acts only at the head its operator intent signed,
  so the second is refused.

## 10.4 Crash points

The service has named checkpoints, as interlock's operators and settler do: `raised`,
`recorded`, `formed`, `intent`, `acted:<n>` (after each step) and `applied`. The crash
tests:

1. run the service as a child process;
2. kill it at each checkpoint, for every resolution class, on both stores;
3. restart it.

They require:

- **F10-9.** `resolve` reaches the one correct terminal state from what the systems hold.
- **F10-10.** Exactly once: no warrant has any of these twice:
  - an ILOK1 intent naming it;
  - a ledger entry naming it for one step;
  - an extension grant;
  - a commit of its proposal.
- **F10-11.** Nothing is lost:
  - an approval in `intake` before the crash is recorded or refused after it;
  - a ticket raised before the crash is still open after it, or has lapsed by its deadline.

## 10.5 Configuration

```toml
# fiduciary.toml
database   = "postgresql://fiduciary_service@db/acme"  # or a SQLite path
ledger     = "postgresql://fiduciary_service@db/agentgov"  # the deployment's AgentGov ledger
interlock  = "interlock.toml"                          # the deployment fiduciary drives
in_process = "no"                                      # or "tool-calls-only" (F8-10)

[binding]
tools      = ["tools/refund.json", "acme.tools:lookup"] # tool bindings: files or module:name
directives = ["directives/stop-retrying.txt"]          # keyed by the SHA-256 of their bytes
chains     = "chains"                                  # each lineage's <mandate_id>.chain

[charter]
file   = "charter-v1.json"
digest = "<sha256 of charter version 1>"               # the root of authority

[service]
signer = "kms-fiduciary"                               # [signers.<name>], or key = "service.key"

[executor]
key    = "executor.key"                                # an [operators.keys] key; AWS KMS only with U-2
log    = "operators.ilok1"                             # else interlock.toml's [operators] log
outbox = "postgresql://interlock_admin@db/acme"        # the outbox, as the installer (or the SQLite file)

[runtime]
records = "runtime.jsonl"                              # the ILOK1 log of recovery and extension records
key     = "runtime.key"                                # AWS KMS only with U-2

[receipts]
log    = "fiduciary-receipts.jsonl"
signer = "kms-receipts"                                # ARC1 1.2 signs digests: any key service

[bridge]
http         = "127.0.0.1:8765"
skew_seconds = 5

[bridge.handlers]                                      # each passthrough tool's implementation
lookup_order = "acme.tools:lookup_order"

[bridge.agents.support-desk]                           # whom `fiduciary serve` opens sessions for
mandate      = "<mandate_id or subject scope>"
tenant       = "acme"
token_sha256 = "<sha256 of the agent's bearer token>"  # the digest only: no secret (F10-3)
max_sessions = 4

[serve]                                                # seconds
every_seconds = 1                                      # a turn of the writer and the executor
triggers_seconds = 10                                  # the outbox read for held and dead messages
settle_seconds = 5                                     # deliveries receipted
idle_seconds = 900                                     # a silent bridged session is closed
drain_timeout_seconds = 30
restart_min_seconds = 0.5                              # a failed part backs off from here,
restart_max_seconds = 30                               # doubling to here

[metrics]
listen = "127.0.0.1:9465"                              # GET /metrics and /healthz; a private address

[cluster]                                              # PostgreSQL only (§10.7)
node = "fiduciary-0"                                   # or FIDUCIARY_NODE, or serve --node
heartbeat_seconds = 1
session_timeout_seconds = 10

[bridge.pricing.claude-opus-5-5]                       # dollars per million tokens, for models
input = "4"                                            # AgentGov does not price: an allowlisted
output = "20"                                          # model with no price is never called
cache_read = "0.20"
cache_write = "5"
```

The executor reads interlock's operator log under the keyring `interlock.toml` trusts (its
`[operators.keys]`, and the keys its log registered since), and the executor key must be one
of them.

## 10.6 Command line

| Command | Does |
|---|---|
| `fiduciary charter init`, `show`, `propose`, `adopt` | Write charter versions and adopt them |
| `fiduciary mandate draft`, `check`, `diff`, `compile`, `propose`, `issue`, `bind`, `derive`, `amend`, `revoke`, `expire`, `show` | Manage mandates. `check` runs `refines` against the parent. `compile` prints the compiled policy and its digest. `derive` signs a child with the service key and issues it. `amend` issues an approved version through the binder (§6.4). `expire` ends every lineage past its term. |
| `fiduciary tool digest <module:binding>` | Print a binding's digest, for pinning: a file, or `module:name` (a binding `fiduciary.Tool` built in code) |
| `fiduciary approve <ticket or document> --key <file>` or `--signer <name>` | Sign an approval on the principal's own machine, from the rendered bytes. Needs no database credential. |
| `fiduciary tickets list`, `show` | Show open tickets and their evidence, rendered as F7-3 requires |
| `fiduciary warrant show`, `fiduciary resolve` | Inspect warrants; resolve interrupted executions |
| `fiduciary serve [--node N] [--bridge HOST:PORT] [--metrics HOST:PORT]` | Run the service under a supervisor (§10.7): the writer (binder, intake and bridge), triggers, settler, executor and metrics, alone or as a node of a cluster. `SIGHUP` opens every key again; `SIGTERM` drains and stops. |
| `fiduciary mcp serve`, `proxy` | Run the MCP bridge: one session per process, over stdio, or Streamable HTTP with `--http HOST:PORT` (`--token-file`, `--tls-cert`, `--tls-key`). The process also judges the intake and executes the warrants formed, since it holds the quotes and proposals they name (F7-14). |
| `fiduciary export-bundle`, `verify-bundle`, `verify` | §9, and live verification of the store |

## 10.7 The service

`fiduciary serve` runs each part as a service of interlock's supervisor. Each part's step
runs on a thread of its own. A step that raises closes the part, which opens again after an
exponential backoff (`restart_min_seconds`, doubling to `restart_max_seconds`). `SIGTERM`
drains each part once and closes them in order. `GET /healthz` answers 200 while every part
runs (or stands by), and 503 otherwise, with each part's state, steps, failures and
counters, the roles this node leads, and the bridge's address. `GET /metrics` exports
interlock's per-part metrics and fiduciary's: the log's records, lineages by status,
tickets by status, warrants by execution, the intake pending, sessions open, and records
written by part. Every label comes from a closed set: never a lineage, a tenant or a key.

| Part | Does, each turn | Where, in a cluster |
|---|---|---|
| `writer` | Holds the node's engines (and their chains), guards, recoveries, runtime log and the HTTP bridge's sessions. Brings its engines up to the log (§6.4); lapses what is due and judges the intake; expires lineages past their term; executes the formed warrants only it can (below); anchors what the ledger missed; closes idle sessions | the writer role's node |
| `triggers` | Raises `held` tickets for scopes with held messages, and `dead` tickets for dead messages (§7.1), over the lineages in force. A message a live ticket names (open, or warranted and not yet executed) raises nothing | beside the writer |
| `settler` | Interlock's settler over each lineage's chain and receipt issuer: delivery receipts (§9.1) | beside the writer |
| `executor` | `resolve()` (F7-13), then every formed warrant that touches only interlock and AgentGov | the executor role's node |
| `metrics` | `/metrics`, `/healthz`, and a sample of the log | every node |

- **F10-12.** A warrant is executed where what it needs is. An `accept_repair` stages on the
  lineage's engine, an `amend` rebinds it, and a `grant` or `decline` answers a quote the
  lineage's guard holds (adopted from the runtime log where Interlock adopts quotes, U-3)
  and writes the answer to the runtime log, whose one writer is the writer's process. These
  are executed by the writer. Every other
  resolution is executed by the executor. Either way, the warrant's claim keeps it to one
  execution (F7-12).
- **F10-13.** The bridge opens sessions for the agents `[bridge.agents]` names. An agent
  presents a bearer token whose SHA-256 the configuration holds. Each `initialize` without
  an `Mcp-Session-Id` opens a session of its own, up to `max_sessions`, and is answered
  with its id. `DELETE /mcp` with the id ends it. A session silent for `idle_seconds` is
  closed. A token that opens nothing is refused with 401, a lineage not in force with 403,
  and an agent at its bound with 429.
- **F10-14.** `SIGHUP` opens every key again, from the files and key services the
  configuration names now, and swaps each in place. The engines and the sessions keep
  running. The service, runtime and receipt keys may not change: the log, the runtime
  records and the checkpoints are each verified under one key. A reload that names another
  such key is refused, and the service keeps the keys it has. The executor key may rotate,
  to any key interlock's operators trust. The agents are read again: the sessions of an
  agent no longer named, or named with another token, are closed.

**A cluster** (`[cluster]`, PostgreSQL only) is several `fiduciary serve` processes over one
store and one ledger. Each is a node, joined through interlock's `cluster.ClusterNode`: its
session holds the node's advisory lock for the process's life, and the roles it leads.

- **F10-15.** One node leads `fiduciary-writer`; the writer, the triggers and the settler
  run there. One node leads `fiduciary-executor`; the executor runs there, on connections of
  its own and a binder with no engines. A node on standby holds no chain, no runtime log, no
  receipt log and no listener: the writer opens them when its node takes the role, and lets
  go of all of them the moment the node does not lead it.
- **F10-16.** A node is gone when the database ends its session: at once when its process is
  killed; after `session_timeout_seconds` of silence when it freezes. Every connection a
  node opens is named for it (`interlock-<part>@<node>`). At each heartbeat, every other
  node ends each connection, to the cluster's database, of a node whose lock no session
  holds. So a transaction a frozen node holds the writer's lock in, and a warrant's claim
  it holds, end then. Its connections to other databases (the back office) end their
  transactions once idle for the same bound.
- **F10-17.** Leadership decides which node works, never what keeps the work correct. Every
  decision is taken under the writer's lock (F10-5), and every execution under its warrant's
  claim, which the executor confirms before each act. A node that thaws mid-step finds its
  connections ended and its claim gone, and acts no more. What it left is resolved from what
  the systems hold (F7-13).
- **F10-19. What a frozen node delays, and what bounds it.** The escrow chains, the runtime
  log, the receipt log and the operator log are files. Each has one writer at a time, held
  by an advisory lock beside it for the writing process's life, and no database can fence
  a file. So:
  - The files are on storage every node reaches, whose advisory locks every node's kernel
    honors: a local filesystem, when the nodes are processes of one host, or a network
    filesystem whose locks hold across hosts (NFSv4's). Never a copy on each node: two
    writers of one chain would fork it.
  - A frozen writer keeps the files' claims until it thaws or dies. Killed, it lets go of
    them with its process, at once. Thawed, it finds its session ended at its next
    heartbeat or write, and lets go of everything before its next turn (F10-17).
  - Meanwhile the node that took the writer role leads it, and waits for them. Each turn
    it asks for the files again; it stays healthy (`/healthz` 200, its writer `running`,
    counting `claims_held`), serves no session, and logs once which process holds them.
    It never backs off, so it takes them up within a turn of their release. A writer whose
    opening fails part-way lets go at once of the chains it had opened.
  - Nothing is written twice: the frozen node's database transactions are ended at the
    session timeout (F10-16), and what it left is resolved from what the systems hold
    (F7-13).
  - The deployment bounds the delay: a supervisor (systemd's watchdog, Kubernetes'
    liveness probe) that asks each node's `/healthz` with a timeout, and kills a node that
    does not answer. A frozen process answers nothing; the node waiting for it answers 200.

  *(Amended after the 1.0.0 freeze. 1.0.0's new leader failed its writer's turn on a file
  claimed elsewhere, backed off up to `restart_max_seconds`, and answered 503, inviting a
  supervisor to restart the one healthy node; and a writer whose opening failed part-way
  held the chains it had opened until they were collected.)*

---

# 11. Roadmap: 0.1.0 to 1.0.0

Each phase is a release, merged once its exit criteria hold under all of these:

- SQLite, and PostgreSQL 14 to 18;
- Python 3.11 to 3.14;
- coverage floors of 95% for lines and 90% for branches, as interlock's.

No phase depends on a later one. A phase that needs an upstream change (§12) names its
fallback until that change ships.

### Phase 1 — 0.1.0: FIDU1 and the mandate

**Builds.**
- The modules `fidu1`, `documents` (charter, mandate, tool, approval) and `refines`.
- The FIDU1 log on both stores, with append-only enforcement and AgentGov anchors.
- The commands `charter`, `mandate check`, `diff`, `show`, `tool digest`, `approve` and
  `verify`.

**Proves.**
- **Golden vectors** (`vectors/fidu1/`). Valid documents and records come with their digests
  and signing inputs; invalid ones name the error each must raise. They are generated once
  from the reference implementation, then frozen.
- **Signing.** Every signature is made three ways:
  - through `Ed25519Signer`;
  - through `HttpRemoteSigner`, against a fake key service;
  - through `AwsKmsSigner`, against a fake KMS client that enforces `RAW`, 4096 bytes and
    the key spec.

  Every signing input is at most 96 bytes, and each verifies under the pinned key only.
- **Strict parsing.** Every single-field mutation of every valid vector is refused: a field
  dropped, added or retyped, a set reordered, a float.
- **`refines`.** Hypothesis shows that ⊑ is reflexive and transitive, that every generated
  narrowing is ⊑, and that every single-field widening is named, by field.
- **The log, on both stores.** `verify` and the anchors name an edit, deletion, reordering,
  truncation or fork of any record.

**Exit.** The FIDU1 framing and the mandate schema are reviewed and frozen for 0.x. Any
change after that bumps a domain's version.

### Phase 2 — 0.2.0: compilation and binding

**Builds.**
- `compile` and `CompiledMandate`, fiduciary's checkers, the narrowed registry, the
  namespaced windows, and the governance and cognitive policies.
- `bind`, rebinding at plan boundaries, derivation, rebasing, suspension, revocation and
  expiry.
- The commands `mandate compile`, `bind`, `derive`, `amend`, `revoke` and `expire`.

**Needs.** U-1. Without it, V4 runs partially (F6-8), and the phase says so.

**Proves.**
- **Semantic monotonicity** (F5-10), over generated mandates and diffs, for every compiled
  checker type, with no database. The cognitive variant (F5-11) runs over generated call
  sequences.
- **Determinism.** `compile` in subprocesses under eight hash seeds gives one compiled
  digest, and, once U-1 has landed, one set of checker records.
- **Refusals.** On SQLite and PostgreSQL substrates, a bound mandate refuses each class of
  out-of-mandate plan: table, operation, tenant, row count, drawdown, sink, sink operation,
  cap, recipient and statement.
- **Aggregates.** Two derived children together cannot pass their parent's window, and the
  count is exact under concurrency.
- **Windows and amendments.** Window history survives an amendment, and the amendment
  applies at the next plan boundary.
- **Revocation during a stage.** The plan does not commit (E3-8), the lineage's messages are
  held, and a satellite scope is halted too.

### Phase 3 — 0.3.0: escalation, quorum and warrants

**Builds.**
- The six trigger classes, intake, quorum, warrants and autonomous rules.
- Two-phase execution of every resolution against interlock and AgentGov, and `resolve`.
- Lapse and decline, and the ticket bounds.
- The commands `tickets`, `warrant` and `resolve`, and the executor-key guard of F3-9.

**Needs.** Each upstream change has a fallback until it ships:
- U-2, to allow an AWS KMS executor. Until it ships, one is refused.
- U-3, to keep extension tickets across a restart. Until it ships, they lapse (F7-14).
- U-4, to close the head gap. Until it ships, the gap is named (F7-15).

**Proves.**
- **Quorum, by property test.** Generated rosters, quorums and approval sets include:
  - duplicates, wrong keys and suspended keys;
  - approvals for another ticket, another resolution or another deployment;
  - approvals that are expired, recorded late, or superseded by the same principal.

  A warrant forms iff §7.3 says it does.
- **Crash consistency.** The child process is killed at every checkpoint of every resolution
  class, on both stores, and F10-9 to F10-11 hold.
- **Exactly once under contention**, with two service processes, replayed approvals, and an
  approval arriving as the warrant forms.
- **Tamper.** Each of these is named: a forged approval; an approval moved to another
  ticket; a deleted approval; a reordered log; an edited projection.
- **Fatigue.** A ticket storm past `max_open` raises nothing, and leaves the agent's
  boundaries standing.

### Phase 4 — 0.4.0: the bridge

**Builds.**
- `Fiduciary` and its sessions.
- The `Tool` helpers and binding digests, and plans built from bindings.
- The governed model client, and the flow from a halt to recovery to an extension.
- The MCP server (stdio and Streamable HTTP) and proxy, and the two built-in tools.

**Proves.**
- **End to end, on both stores, against agentgov's `DummyLLM`.** A session commits, is
  refused, is repaired by rule, runs out of money, recovers, is quoted, is granted by two
  approvals through a fake KMS, and finishes.
- **MCP.**
  - `tools/list` holds only the pinned bindings and the built-ins.
  - Arguments outside a schema are refused before a plan exists.
  - No approval surface is reachable.
  - A session token opens one session.
- **An adversarial agent suite.** Each of these is refused, and the refusal recorded:
  - SQL in an argument;
  - another tenant's id;
  - a float, or an oversized string;
  - an unpinned tool;
  - a forged approval in a tool result;
  - a ticket storm.
- **A live acceptance suite** against the Anthropic API, in the manner of interlock's
  customer acceptance matrix. It is cost-capped and opt-in, and its scenarios are phrased as
  business tasks and policy compliance only.
- **The README's example** runs as written, and is short.

### Phase 5 — 0.5.0: the audit bundle

**Builds.**
- `export-bundle`, and `verify-bundle` with V1 to V12 and their exit codes.
- Ledger inclusion proofs against published, cosigned ledger checkpoints.
- The bundle format document, for independent verifiers.
- Golden bundles in `vectors/fidu1/bundles/`.

**Proves.**
- **Tamper sweep.** For each golden bundle, each of these fails with a named check, never a
  crash and never a pass:
  - deleting, duplicating or reordering any member;
  - flipping any byte of a sampled set that covers every member type.
- **Ambient authority.**
  - V7 names each of these, written around fiduciary on a lineage's footprint: a
    delegation, a reset, an operator release, a plan.
  - V8 names a warrant one approval short, and a warrant executed after its `not_after`.
  - V11 names a commit after revocation.
- **Offline.** Verification runs with the network disabled and no database reachable.
- **Identical exports.** One lineage and range export to identical bytes twice.

### Phase 6 — 1.0.0: production

**Builds.**
- `fiduciary serve` under a supervisor: restart with backoff, key reload on `SIGHUP`,
  `/healthz`, and metrics.
- Cluster leadership for the writer and the executor, on PostgreSQL.
- Upgrade paths from every 0.x store.
- The conformance section of this document.

**Proves.**
- **A soak run**, minutes long, against live PostgreSQL. It runs:
  - several agents across lineages and derived children;
  - escalations approved through a fake KMS by simulated principals;
  - forged and replayed approvals;
  - a node killed and a node frozen;
  - edits made to the database around the service.

  Afterwards the database, the ledger and the logs show that every invariant held: no warrant
  executed twice, no quorum short, no window passed, the ledger balanced, every forgery
  refused, and every edit named. `tests/test_soak.py` runs it scaled down.
- **A security audit script**, by the method of interlock's `security_audit.py`. No secret,
  payload, transcript or agent text reaches a hash, a document or a log, except the clipped
  `agent.statement`.
- **A mutation-testing pass** over `fidu1`, `refines`, `compiler`, quorum counting and
  `verify_bundle`, as interlock's epics ran. Every surviving mutant is killed, or explained.
- **FIDU1 is frozen for 1.x.** Anything a 1.x release writes verifies under every later 1.x
  release.

**Exit.**
- Every requirement in [Traceability](#traceability) is implemented, or is listed as partial
  or unimplemented with its reason.
- Each of U-1 to U-4 has shipped upstream, or the conformance section states the gap it
  leaves.

---

# 12. Upstream prerequisites

These are what fiduciary's contract needs from the packages below it, and what interlock
1.0.2 and agentgov 1.0.0 do not provide. Each is a change to their public surface, made in
that package. None is worked around here.

Fiduciary reads which of them the installed packages provide from their code
(`fiduciary._upstream`), uses each where it is present, and holds to the fallback in the
last column where it is not. So one release runs on interlock 1.0.2 and agentgov 1.0.0, and
on the releases that close these. *(Amended after the 1.0.0 freeze, as each was made
upstream: Conformance gives where.)*

| # | Package | Gap, as measured or read | Needed by | Where it is not installed |
|---|---|---|---|---|
| U-1 | interlock | `receipts.checker_records` digests a checker's configuration from the `repr` of its slots. A `frozenset` of strings has a `repr` whose order depends on the hash seed: on 1.0.2, `table_allowlist` over four tables digested four ways under four seeds. A receipt's `decision.checkers` therefore cannot be recomputed in another process. The fix is to encode sets sorted. It changes no plan, diff or verdict hash, only checker configuration digests, which were never reproducible. | F6-8, V4 (Phase 2) | Fixed on interlock `main` (crimsondevil0929/interlock#20, under `[Unreleased]`); ships in the next release. Until fiduciary requires it, V4 checks `fiduciary_mandate` exactly, and the other checkers by name. |
| U-2 | interlock | ILOK1 records are signed whole. Through AWS KMS, an `operator.applied` record fails past about 24 rows and a `compensate` intent past about 9 targets (§3.5's measurement), and a cancel's cascade is unbounded (F3-9). The fix is to sign ILOK1 records at a digest version, as ARC1 1.2 does. | F3-9 (Phase 3) | Executor and runtime keys on AWS KMS are refused, and outbox actions are chunked to 8 targets. |
| U-3 | interlock | `BudgetGuard` keeps quotes in memory, and grants only those it issued, so a restart loses every open quote. The fix is to adopt unanswered, unexpired quotes from the record log. | F7-14 (Phase 3) | Extension tickets lapse on a restart. |
| U-4 | interlock | `Operator` reads each target's head itself when it signs its intent, so a caller cannot require the heads its principals saw. The fix is to accept `expected_heads=`, and to refuse on a mismatch before signing. | G5, F7-15 (Phase 3) | The mismatch is recorded and named, not prevented. |
| U-5 | agentgov | `reset(scope)` always writes the memo `operator reset`, so nothing in the entry can name a warrant. The fix is to accept `memo=`. | §7.6, `reset` | The warrant's anchor immediately precedes the reset, in one transaction. |
| U-6 | interlock | A `RecoveryRuntime` whose `recovery.closed` record exists refuses to adopt its scope, and nothing funds `{scope}/recovery` again, so a lineage recovers once. The fix is to let a closed recovery be reopened, funding a fresh reserve under a new opening record. | F8-9 (Phase 4, amended in 0.5.0) | After the lineage's recovery closes, a spent-out halt is quoted on the billing scope, and any other halt stands. |

---

## Open questions

These are listed because they are unresolved, not because they are minor.

- **Prevention, not only detection, of a compromised service.** A3 can execute without a
  warrant; V7 and V8 name it only afterwards. Prevention would need the systems underneath to
  check warrants themselves:
  - interlock's database verifying k signatures before an operator act;
  - AgentGov refusing a reset without a warrant.

  Whether that belongs in those packages, and at what cost, is unanswered.
- **Quorum liveness.** Deadlines, time zones and principals' availability decide whether
  escalation is usable. The defaults (`ttl_seconds`, quorum sizes) are placeholders, and need
  measuring against a real approval workload.
- **Autonomous rule thresholds are uncalibrated.** `max_dropped`, `max_requeues` and
  `auto.max_grant` are policy inputs with no measured basis. They need the same treatment
  as interlock's radius limits and agentgov's cognitive threshold: a corpus, both error
  classes counted, and the rule swept.
- **What principals should see.** A ticket shows tenants and amounts. Whether a multi-party
  roster needs per-principal redaction, and how that would compose with what-you-see-is-
  what-you-sign, is open.
- **Semantic monotonicity is tested, not proven.** F5-10 checks the implication over
  generated cases. A per-checker proof that each predicate is monotone in its parameters
  would be stronger, and is not written.
- **Passthrough tools.** By design, nothing measures their effects. Whether any common MCP
  tool can be shown to be read-only, rather than declared so, is unanswered.
- **Cross-deployment mandates.** A mandate spanning two ledgers, or two interlock
  deployments, is out of scope: a charter binds one ledger.
- **Anchor volume.** Every FIDU1 record is a ledger entry. On a busy deployment that is a
  measurable share of the ledger. Batching anchors under a Merkle root would trade latency
  for it.

## Conformance (1.0.0)

`interlock-fiduciary` 1.0.0 implements this document, with the gaps below. Each is stated
with the reason for it, and what holds meanwhile.

**Frozen for 1.x.** Anything a 1.x release writes verifies under every later 1.x release:

- FIDU1's framing (§3): the value domain, the signing construction, and the domains
  `FIDU1/<type>/v1` of `charter`, `mandate`, `tool`, `ticket`, `approval`, `warrant`,
  `record` and `bundle`;
- each document's schema (§4), and every record kind the log holds (§4.7);
- the audit bundle's format, `fidb/1` (`docs/BUNDLE_FORMAT.md`), and its exit codes;
- the store's schema, version 4, which every 0.x store upgrades to in place (F10-18).

A change to any of them is a new domain version (`FIDU1/<type>/v2`) or a new bundle format
(`fidb/2`), which a later release reads beside the old. The golden vectors
(`vectors/fidu1/`) and the golden bundles (`vectors/fidu1/bundles/`) hold every release to
this.

**The upstream gaps (§12).** Each is implemented upstream: U-1 on interlock's `main`
(#20), U-2, U-3, U-4 and U-6 on its branch `feat/fiduciary-prerequisites`, and U-5 on
agentgov's branch `feat/reset-memo`, none of them released yet. Fiduciary uses each where
the installed package provides it, and its suite runs both ways: on interlock 1.0.2 and
agentgov 1.0.0, and on those branches.

| # | With it | Without it, as on interlock 1.0.2 and agentgov 1.0.0 |
|---|---|---|
| U-1 | V4 checks every checker's digest, for the receipts from a lineage's first canonical one on. | V4 checks `fiduciary_mandate` exactly, and the other checkers by name (F6-8). A bundle whose manifest says `canonical_digests` is checked strictly, but for receipts that precede the lineage's first canonical one, which are noted. Verified against interlock `main`: every 0.x store's bundle passes. **Partial until fiduciary requires an Interlock with it.** |
| U-2 | An AWS KMS executor or runtime key signs ILOK1 records at signature version 2 (F3-9). | An AWS KMS executor or runtime key is refused. Outbox actions are chunked to 8 targets either way. |
| U-3 | A quote survives a restart, and the writer moving to another node (F7-14). | A quote lives in the memory of the process that made it, and a restart lapses its ticket. A repair proposal does either way (F4-27). The writer executes the warrants that need them (F10-12). |
| U-4 | An outbox step at a head that moved after the ticket is refused before anything is signed (F7-15, G5). | It is recorded in `execution.applied` and named by V8: named, not prevented. |
| U-5 | A reset's own entry names its warrant (§7.6). | The warrant's anchor precedes the reset in one AgentGov transaction. |
| U-6 | A lineage's recovery opens again on a fresh reserve at a halt after it closed (F8-9). | A lineage recovers once: after its recovery closes, a spent-out halt is quoted on the billing scope, and any other halt stands. |

**Limits, by design or measured.**

- **What a bundle claims** (F9-2). It does not claim the outbox's delivery logs, or
  interlock's `verify_operators`. Operator acts on the lineage's messages are found through
  its tickets and warrants, and its plans when the exporter has the outbox.
- **Plans before the bridge.** A lineage that ran plans outside any bridge, as 0.2 and 0.3
  could only, exports a bundle whose V7 names each such plan (F10-18).
- **A frozen writer** keeps its chains', runtime log's, receipt log's and operator log's
  file claims until it thaws or dies, and the next leader leads and waits for them,
  healthy (F10-19). A supervisor that kills an unresponsive node bounds the wait. Takeover
  of a killed writer is at once.
- **Velocity is per session** (F6-5). Sessions of one lineage are bounded together by the
  governor's policy.
- **PostgreSQL versions.** 16 is verified, locally. CI runs 14, 16 and 18; the repository
  has no remote yet, so CI has never run.
- **Open questions** (below) stay open: prevention, rather than detection, of a
  compromised service; quorum liveness; rule thresholds; per-principal redaction; proofs,
  rather than tests, of semantic monotonicity.

**Phase 6's proofs** (§11), each a file of this repository:

- the service and the cluster: `tests/test_service.py`, `tests/test_cluster.py` (a leader
  killed; a leader frozen, fenced and thawed; a node's roles in process);
- the upgrades: `tests/test_upgrade.py` over `tests/upgrades/`, stores each 0.x release
  wrote itself, on SQLite and PostgreSQL;
- the soak: `scripts/live_stress_test.py` (minutes long), `tests/test_soak.py` (scaled
  down);
- the security audit: `scripts/security_audit.py`, `tests/test_security_audit.py`;
- the mutation pass over `fidu1`, `refines`, `compiler`, quorum counting and
  `verify_bundle`: `docs/MUTATION_PASS.md`.

## Traceability

The table lists every requirement, the phase that built it, the test kind that proves it,
and its status in 1.0.0 with the test files. Unless a row says otherwise, every row runs on
SQLite and on PostgreSQL. The test kinds:

- *golden*: the vectors in `vectors/fidu1/`;
- *property*: a Hypothesis test;
- *crash*: the subprocess kill tests;
- *tamper*: the edit-and-verify tests;
- *soak*: the Phase 6 run.

| Requirements | Phase | Proven by | 1.0.0 |
|---|---|---|---|
| F1-1, F1-2: the lifecycle; no fork | 1, 2 | property: the log accepts n+1 only at head n. tamper: a fork is named (V3). | Implemented. `test_store.py`, `test_tamper.py` |
| F1-3: amendment at a plan boundary | 2 | integration: the plan in flight keeps its `policy_epoch` | Implemented; engines follow the log (§6.4). `test_binding.py`, `test_bridge_edges.py` |
| F1-4, F5-7: rebasing and suspension | 2 | integration over a three-level lineage | Implemented. `test_binding.py` |
| F1-5, F5-8, F5-9, G7: revocation stops the lineage | 2, 5 | integration: the E3-8 refusal, held messages, satellites tripped. tamper: V11. | Implemented. `test_binding.py`, `test_bundle_checks.py` |
| F1-6, F1-7: silence refuses; verdicts stand | 3 | lapse tests, per class | Implemented; a warrant expired unexecuted lapses its ticket (F7-7). `test_escalation_quorum.py`, `test_tickets_warrants.py` |
| F2-1, F2-2, G1: no ambient authority | 3, 5 | V7 ambient-injection tests; soak | Implemented. `test_bundle_checks.py`, `test_soak.py` |
| F3-1: strict decoding | 1 | golden; per-field mutation sweep | Implemented. `test_vectors.py`, `test_fidu1.py`, `test_documents.py` |
| F3-2 to F3-8: framing; Ed25519 only; inputs ≤ 96 bytes; KMS | 1 | golden signing inputs; fake KMS and fake key service; HMAC refused | Implemented. `test_signers.py`, `test_vectors.py` |
| F3-9: ILOK1 sizes under KMS | 3 | executor guard test, both ways; chunking test; the measured table, regenerated by script; with U-2, an operator record of any size through KMS | Implemented where U-2 is installed; else the refusal. `test_executor.py`, `test_ilok1_sizes.py` |
| F4-1 to F4-6: the charter | 1 | golden; property: adoption and suspension quorums | Implemented. `test_authority.py`, `test_vectors.py` |
| F4-7 to F4-12: the mandate schema | 1 | golden; strict parsing | Implemented. `test_documents.py`, `test_vectors.py` |
| F4-13 to F4-16: tool bindings | 1, 4 | golden; the adversarial agent suite | Implemented. `test_tools.py`, `test_adversarial_agent.py` |
| F4-17 to F4-20: tickets | 3 | evidence tests per class; narrowing tests | Implemented. `test_tickets_warrants.py` |
| F4-21 to F4-25: approvals and warrants | 1, 3 | golden; the quorum property test | Implemented. `test_escalation_quorum.py`, `test_decide.py` |
| F4-26, F4-27: the log and its anchors | 1 | tamper: truncation and rewriting; the anchor check | Implemented. `test_tamper.py`, `test_security_audit.py` |
| F5-1 to F5-3, G3: the order | 1 | property: preorder; narrowings; widenings named | Implemented. `test_refines.py` |
| F5-4 to F5-6: aggregates | 2 | concurrent children against one window; delegation bounds | Implemented. `test_binding.py`, `test_soak.py` |
| F5-10, F5-11: semantic monotonicity | 2 | property, offline, for every compiled checker type; the cognitive variant | Implemented (tested, not proven: Open questions). `test_monotonicity.py` |
| F6-1, F6-2: `compile` is pure | 2 | eight hash seeds give one digest | Implemented. `test_determinism.py` |
| F6-3: `compile` fails closed | 2 | an unknown table, sink or binding; a long window name | Implemented. `test_compiler.py` |
| F6-4, F6-5: always-on checkers; governance | 2 | inspection of the compiled set; per-session governors on PostgreSQL | Implemented; velocity is per session. `test_compiler.py`, `test_bridge.py` |
| F6-6 to F6-8, G2: evidence that the policy ran | 2, 5 | V4 over golden bundles; after U-1, every record | Partial: U-1. `test_bundle_vectors.py`, `test_compiler.py` |
| F7-1, F7-2: one ticket per evidence; storms | 3 | the fatigue test | Implemented. `test_executor.py`, `test_service.py` |
| F7-3, G8: what you see is what you sign | 3, 4 | the principal client renders from the signed bytes; the quarantine test | Implemented. `test_cli_tickets.py` |
| F7-4: no approval surface for the agent | 4 | inspection of the MCP and session surfaces; the adversarial suite | Implemented. `test_mcp.py`, `test_adversarial_agent.py` |
| F7-5 to F7-8, G4: quorum | 3 | property | Implemented. `test_escalation_quorum.py`, `test_soak.py` |
| F7-9 to F7-11: autonomous rules | 3 | V8 recomputes each rule's predicate; auto totals across a restart | Implemented. `test_bundle_checks.py`, `test_executor.py` |
| F7-12, F7-13, G5: once, and resolved | 3 | crash; contention | Implemented. `test_crash_recovery.py`, `test_contention.py`, `test_soak.py` |
| F7-14, F7-15: a lost quote; the head gap | 3 | the restart test, both ways; the head-move test: named without U-4, refused unsigned with it | Implemented where U-3 and U-4 are installed; else the fallbacks. `test_executor.py`, `test_bundle_checks.py`, `test_crash_recovery.py` |
| F7-16, F7-17: operator log claims; break-glass | 3, 5 | claim contention; V7's break-glass labeling | Implemented. `test_executor.py`, `test_bundle_checks.py` |
| F8-1 to F8-3: sessions | 4 | end to end; the sanitized feedback test | Implemented. `test_bridge.py`, `test_bridge_lifecycle.py` |
| F8-4 to F8-7: MCP | 4 | protocol conformance tests over stdio and HTTP; the proxy's refusal | Implemented; agents' sessions over HTTP (F10-13). `test_mcp.py`, `test_cli_mcp.py`, `test_service.py` |
| F8-8, F8-9: the billing scope; safety halts | 4 | the recovery and extension flow; a safety halt is never quoted; a recovery closed and halted again, both ways, and its bundle | Implemented where U-6 is installed; else a lineage recovers once. `test_bridge.py`, `test_bridge_edges.py`, `test_bundle.py` |
| F8-12: refusals before anything is built | 4 | the adversarial agent suite; the session record replay tests | Implemented. `test_adversarial_agent.py`, `test_sessions.py` |
| F8-10, F8-11: the trust boundary | 4 | in process, a session refuses to start without the setting | Implemented. `test_bridge.py` |
| F9-1: no row data | 5, 6 | the security audit | Implemented. `test_security_audit.py`, `test_bundle_export.py` |
| F9-2, F9-3: self-contained, offline, specified | 5 | the offline run; golden bundles; byte-identical exports | Implemented, with the limits above. `test_bundle_tamper.py`, `test_bundle_vectors.py` |
| F10-1 to F10-4: the stores | 1, 3 | the projection fold; append-only enforcement on both stores; the intake role | Implemented. `test_store.py`, `test_postgres.py`, `test_upgrade.py` |
| F10-5 to F10-8: concurrency | 3, 6 | contention tests; cluster takeover; soak | Implemented. `test_contention.py`, `test_cluster.py`, `test_soak.py` |
| F10-9 to F10-11: crash points | 3 | crash | Implemented. `test_crash_recovery.py` |
| F10-12 to F10-17, F10-19: the service and the cluster | 6 | process tests: a part restarted, a key reload refused, a leader killed and frozen; the new leader waiting, healthy | Implemented. `test_service.py`, `test_cluster.py`, `test_soak.py` |
| F10-18: upgrades in place | 6 | stores each 0.x release wrote, on both stores | Implemented. `test_upgrade.py` |
