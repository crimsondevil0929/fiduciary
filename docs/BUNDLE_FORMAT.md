# The audit bundle format (`fidb/1`, and sealed: `fidb/2`)

This document specifies the bytes of an audit bundle, the file that
`fiduciary export-bundle` writes and `fiduciary verify-bundle` checks
(`FIDUCIARY_SPEC.md` §9). It is written so that a verifier in another language
needs nothing else, apart from the formats the bundle carries from its sources:

- FIDU1 documents and records (`FIDUCIARY_SPEC.md` §3);
- AgentGov ledger entries, checkpoints and ARC1 receipt bundles (AgentGov's
  `RECEIPTS_SPEC.md`);
- ILOK1 records and escrow chains (interlock's `ESCROW_SPEC.md`).

`vectors/fidu1/bundles/` holds 34 golden bundles: four valid (one of them
sealed), and the others each failing one or more checks. `index.json` records,
for each one, what it is verified with and its verdict (§6).

*Amended in 1.4.0:* a **sealed** bundle (`fidb/2`, §3.4) carries one lineage's
records whole, and of the log's other records their places and hashes only.
Everything else in this document holds for it too.

## 1. The container

A bundle is an uncompressed POSIX tar (USTAR). Its rules:

- Every member is a regular file at a relative path: no directories, links,
  devices, absolute paths or `..`.
- The members are in byte-wise sorted order of their paths, each path at most
  once.
- Every header has mode `0644`, uid and gid `0` with empty owner names, and
  mtime `0`.

So one lineage over one range exports to identical bytes. A verifier reads the
tar strictly. A file that is no tar, or holds anything but regular files under
relative paths, is unreadable (exit 2).

## 2. The manifest

`manifest.json` is canonical FIDU1 JSON (sorted keys, no insignificant
whitespace, no floats; §3.1 of the spec), with no trailing newline:

```json
{
  "v": "FIDU1",
  "type": "bundle",
  "format": "fidb/1",
  "exporter": "fiduciary 0.5.0",
  "canonical_digests": true,
  "lineage": "<the mandate_id exported>",
  "lineages": ["<it, and every descendant, sorted>"],
  "subjects": ["<their subject scopes, sorted>"],
  "range": {"from": 1, "to": 42},
  "ledger": {"first": "<entry 1's hash>", "cut": 118},
  "members": [{"path": "...", "type": "...", "sha256": "<hex>"}],
  "sig": {"alg": "ed25519", "key_id": "...", "signature": "..."}
}
```

The fields:

- **`members`** lists every other member, sorted by path, with its type (§3)
  and the SHA-256 of its bytes. A member the manifest does not list, or a
  listed member that is missing, fails V1.
- **`canonical_digests`** says whether the exporter's receipts carry checker
  digests independent of the process's hash seed (interlock U-1). It sets how
  strictly V4 holds receipts to F6-8. When it is set, a receipt anchored before the
  lineage's first receipt whose set-bearing digests are canonical was issued by an
  Interlock before U-1: its set-bearing checkers are checked by name, and the report
  notes it. A receipt anchored after that first canonical one is held to every digest.
  *(Added in 1.0.0: a lineage served before the upgrade to an Interlock with U-1 still
  verifies.)*
- **`range`** is the part of the FIDU1 log the bundle makes claims about
  (V10, V12). `log.jsonl` always holds the whole prefix `1..to`, because a
  decision in range is taken again from the state that every earlier record
  built.
- **`ledger.cut`** is the ledger sequence of the anchor of record `to`. The
  ledger checkpoint covers exactly `1..cut`.
- **`sig`** is optional: a FIDU1 signature of the manifest without `sig`,
  domain `bundle`, by the service key. When present it must verify under the
  service key (V9); when absent, the report notes it. Nothing else relies on it,
  because every member is checked against its digest and then on its own terms.

## 3. The members

| Path | Type | Holds |
|---|---|---|
| `charter/<digest>.json` | `charter` | every charter version the log names |
| `charter/approvals/<digest>.json` | `approval` | approvals of charters and of key suspensions |
| `mandates/<digest>.json` | `mandate` | every version of the lineage, its ancestors and its descendants, and every other version the log names |
| `mandates/approvals/<digest>.json` | `approval` | approvals of mandates and revocations |
| `tools/<digest>.json` | `tool` | every tool binding a mandate in the bundle pins |
| `tickets/<digest>.json` | `ticket` | every ticket the log names |
| `approvals/<digest>.json` | `approval` | approvals of tickets |
| `resolutions/<digest>.json` | `resolution` | proposed resolutions the log names |
| `warrants/<digest>.json` | `warrant` | every warrant the log names |
| `intake/<n>.bin` | `intake` | the raw bytes of each intake item a record answers (`approval.recorded`, `approval.refused`), `n` zero-padded to 10 digits |
| `log.jsonl` | `log` | FIDU1 records `1..to`, one per line, exactly as stored |
| `ledger/entries.jsonl` | `ledger` | the footprint's ledger entries, each with its inclusion proof |
| `ledger/checkpoint.json` | `ledger` | an AgentGov checkpoint of entries `1..cut`, signed by the ledger's checkpoint key |
| `ledger/cosignatures.json` | `ledger` | witnesses' cosignatures of that checkpoint |
| `receipts/<receipt_id>.json` | `receipt` | an ARC1 `ReceiptBundle` for each of the lineage's action receipts, and each delivery receipt of one |
| `receipts/transitions.json` | `receipt` | the receipt log's key transitions |
| `ilok1/runtime.jsonl` | `ilok1` | the lineage's ILOK1 runtime records (recovery, extension) |
| `ilok1/operators.jsonl` | `ilok1` | operator intents on the lineage's warrants or messages, their outcomes, and every key registration and install record |
| `escrow/<mandate_id>.chain` | `escrow` | each lineage's escrow chain, as interlock writes it, up to the cut |
| `deployment.json` | `deployment` | what compilation reads from the deployment (§3.2) |
| `keys.json` | `keys` | every public key the bundle verifies under (§3.3) |

Some rules hold for every member:

- A document's path names its own FIDU1 digest, and the document must digest to
  it. Documents are stored as the service stored them, signed, in canonical
  JSON.
- A `.jsonl` member is empty, or lines that each end in `\n`, with no empty
  line.
- An ILOK1 line is canonical JSON of the record's `to_json()`. A record signed
  at signature version 2 (`"sv": 2` in its `sig`: interlock's U-2, for a key
  service that limits what it signs) is read and verified by an Interlock that
  has it. Where the verifier's Interlock lacks it, the bundle cannot be read
  there (exit 2, not `malformed`): the verifier names the record and the
  release that reads it.
- Escrow chain lines are interlock's own encoding, which a verifier loads with
  interlock's chain reader.

**F9-1.** No member carries row data, payloads, tool arguments, model prompts or
responses, or secrets. The sources carry digests of those, and the bundle
carries what the sources carry.

### 3.1 Ledger entries

Each line of `ledger/entries.jsonl`:

```json
{"entry": <LedgerEntry.to_audit_record()>, "index": <sequence - 1>, "proof": ["<hex>", ...]}
```

`proof` is the RFC 9162 inclusion proof of `leaf_hash(entry_hash)` at `index` in
the tree of entries `1..cut`. The footprint holds every entry in a transaction
that touches one of these:

- a subject scope or a satellite of one (`admits_scope`);
- the `fiduciary` anchors (`FIDU1 ...` on scope `fiduciary`);
- an ILOK1 anchor of a record the bundle carries.

### 3.2 `deployment.json`

```json
{
  "ledger": "<the deployment's ledger: entry 1's hash>",
  "tables": {"<table>": ["<column>", ...]},
  "sinks": [{"name": "...", "operations": [{"name": "...", "compensation": null, "schema": null}],
             "cost_per_call": "0.01", "idempotency": "header", "max_payload_bytes": 65536,
             "not_after_us": 0, "max_attempts": 0, "backoff_base_us": 0, "backoff_cap_us": 0,
             "unknown_outcome": "...", "kind": "..."}],
  "directives": {"<sha256>": "<text>"},
  "depths": {"<root funding scope>": 0}
}
```

The rules for it:

- It must parse into a deployment: tables, sinks (whole seconds for
  `not_after`), and directives. If it does not, V1 fails.
- The tools come from `tools/`.
- `depths` gives each root lineage's funding scope's depth in the ledger tree,
  which governance's `max_depth` counts from.

### 3.3 `keys.json`

```json
{
  "principals": {"<name>": "ed25519:<hex>"},
  "service": "ed25519:<hex>",
  "ledger": "ed25519:<hex>",
  "receipts": ["ed25519:<hex>"],
  "runtime": "ed25519:<hex>",
  "operators": {"<name>": "ed25519:<hex>"}
}
```

Each key is trusted in its own way:

- **Principals' keys** are the charters'. The pinned charter digest vouches for
  them, and `keys.json` must name exactly those (V2).
- **Every other key** is the exporter's statement. An auditor pins those keys
  with `--keys` (a file of this form, without `principals`), and the verifier
  then uses the pinned ones. Where `keys.json` names a key that differs from a
  pinned one, V1 fails. A role the auditor did not pin is verified under
  `keys.json`'s key, and the report says so.

### 3.4 `withheld.json` (`fidb/2` only)

*Added in 1.4.0.* A sealed bundle's manifest names `"format": "fidb/2"`. It is
cut from a sealed log, whose every record carries a `seal` of two tags and
whose every anchor memo repeats them (`FIDUCIARY_SPEC.md` F4-26, F9-7).
`log.jsonl` holds whole exactly the records the bundle's keys open, and
`withheld.json` holds the keys and every other record of the log's prefix, in
order, as its place and its hash:

```json
{
  "keys": {
    "all": "<64 hex>",
    "lineages": {"<the bundle's lineage, and each below it>": "<64 hex>"},
    "lines": {"<each lineage above it>": "<64 hex>"}
  },
  "records": [{"hash": "<64 hex>", "seq": 3}, {"hash": "<64 hex>", "seq": 4}]
}
```

Each record object has exactly those two members; the sequences rise, each
once, and none is also in `log.jsonl` (else V1). Together the two members are
the chain from record 1 to the range's last. A `fidb/2` bundle without a
readable `withheld.json`, or a `withheld.json` in a `fidb/1` bundle, fails V1.

A tag of key *k* at record *n* of log *L* is `HMAC-SHA-256(k, "fidb/2 seal\n" ‖
canonical([L, n]))`; a lineage key *k* also makes the tags of its line key
`HMAC-SHA-256(k, "fidb/2 line")`. The keys **open** a record when one of its
seal's tags is one they make at its place: `all` makes one tag; each of
`lineages` two, its own and its line key's; each of `lines` one.

The verifier holds a withheld record to three things, and to nothing else:

- **V9.** It sits in the chain at its place: the next record names its hash as
  `prev`, and no sequence is missing.
- **V10.** The ledger anchors it, with that hash, whether or not it is in range.
  The anchor is in `ledger/entries.jsonl`, under the signed checkpoint.
- **V10, its seal.** Its anchor's memo carries two tags, and the bundle's keys
  open neither. A withheld record they open is the bundle's own, and V10 names
  the lineage it belongs to.

And V10 holds the keys themselves: `all` opens record 1; each lineage's key
opens the record that issued its first version (each of `lines` makes a tag of
it); `lineages` names exactly the manifest's lineages and `lines` exactly the
lineages above them; no lineage `log.jsonl` shows issued below one of the
bundle's is missing; and every record `log.jsonl` holds carries a seal, the
one its anchor names, which the keys open.

Replay folds a withheld record past: the state's head moves to it, and nothing
else changes. The report says how many records were withheld.

## 4. Verification

`fiduciary verify-bundle <bundle> --charter <digest> [--keys <file>] [--witness <key>] [--skew <seconds>]`

The verifier runs V1 to V12 (`FIDUCIARY_SPEC.md` §9.3) and prints every check,
and every finding of each failing one:

```
V1 schema: ok
...
V7 completeness: FAILED (1)
  V7 ambient [break-glass]: operator record 1 (release) by key 4f0c... acted on the lineage's outbox messages without an authorizing warrant
...
note: ...
FAIL: exit 7
```

The rules of a run:

- **Exit code.** A finding's kind sets its exit code: `malformed` 3,
  `signature` 4, `authority` 5, `monotonicity` and `policy` 6, `ambient` 7,
  `evidence` 8, `revocation` and `time` 9. The run exits with the code of the
  first failing check in V order (within it, its first finding). It exits 0 on
  a pass, 1 on a usage error, and 2 on a bundle it cannot read.
- **Skipped checks.** A member that fails V1 is left out of every later check.
  Without a service key, nothing past V9 can be checked. Without the pinned
  charter, the log is not replayed.
- **Replay.** The decisions in the log are taken again by the service's own
  rules, over an in-memory store holding only what the bundle carries. A
  decision they refuse is classified by its record:
  - charters and suspensions are V2;
  - mandates and sessions are V3, and V3 `monotonicity` when the problem is a
    refinement;
  - approvals take their subject's check;
  - tickets, warrants and executions are V8, and V8 `time` when the problem is
    a deadline.
- **Offline (F9-3).** The verifier opens no socket and no database. It writes
  only escrow chains, to a temporary directory, for interlock's chain reader to
  load.

### 4.1 V7: what explains an act

Every ledger entry on the footprint must trace to one of these, or V7 names it
as ambient:

| Entry | Explained by |
|---|---|
| `CIRCUIT_TRIPPED` | itself: a halt grants nothing |
| `ILOK1 <log> <seq> <hash>` | a record of that log, sequence and hash in `ilok1/` |
| `interlock:<head16>` | a record of the lineage's escrow chain with that hash prefix |
| `FIDU1 session <id> ...` | a session of the lineage, inside the ledger window from its opening's anchor to its close's, on a scope it billed |
| `FIDU1 tool <name> <d16>` (0.4's form, before memos named their session) | a binding `<name>` with digest prefix `<d16>` that a version of the lineage pins, inside the window of a session of the lineage on a scope it billed, and before the ledger's first `FIDU1 session` memo. After that entry the form is ambient: nothing 0.5 or later writes it. *(Added in 1.0.0, so a lineage served by 0.4 exports a bundle that verifies.)* |
| a settle or void (`ref` set) | the hold it names, itself explained |
| `CIRCUIT_RESET` | its own memo, or the memo of the entry before it, is `FIDU1 warrant <d> reset <scope>`, `d` a warrant of the lineage and `scope` this entry's. *(The reset's own memo is AgentGov's U-5; an executor without it anchors the memo just before the reset. Both forms are read.)* |
| `FIDU1 bind <d>` | `d` a version of the lineage, and a line of its transaction named by a `mandate.bound` record |
| `extension <g> for quote <q>, approved by FIDU1 warrant <d>` | an `extension.granted` runtime record `g`, and `d` a warrant of the lineage |
| `carried into ... by extension <g>` | an `extension.granted` runtime record `g` |
| `recovery reserve for <s>` / `unused recovery reserve for <s> returned` | a `recovery.opened` / `recovery.closed` runtime record of `<s>/recovery` |
| `recovery step <n>: ...` | a `recovery.step` runtime record `n` |
| a funding of an extension scope | an `extension.granted` record funding it |

Beyond the ledger, three more rules apply:

- Every action receipt must name a record of the lineage's escrow chain.
- Every plan admitted on the chain must carry `FIDU1 tool <name> <digest16>` for
  a tool a version of the lineage pins.
- Every operator intent in `ilok1/operators.jsonl` must name a warrant of the
  lineage. If it doesn't, it is ambient, labeled `break-glass` when its key is
  one a charter declares so.

## 5. What a bundle does not claim (F9-2)

The report states these limits. A bundle does not claim:

- the outbox's full delivery logs;
- interlock's `verify_operators` over every row.

Operator acts are checked on the messages the lineage's tickets and warrants
name, and on the lineage's plans when the exporter had the outbox.

## 6. The golden bundles

`vectors/fidu1/bundles/index.json` is a list of entries:

```json
{"name": "v7-break-glass-release", "file": "v7-break-glass-release.fidb",
 "sha256": "...", "about": "...", "charter": "<pinned digest>",
 "keys": {...} | null, "witness": "ed25519:<hex>" | null, "skew_seconds": 5,
 "exit_code": 7, "checks": ["V7"], "findings": ["V7 ambient [break-glass]: ..."]}
```

An implementation that, for every entry, reaches `exit_code` and fails exactly
`checks` verifies bundles as this one does. Matching `findings`' text is this
implementation's own regression test.

The bundles are recorded, not generated from a seed. They are stamped by the
wall clock and held to a real ledger's time, and their lineages are random
UUIDs. `scripts/make_bundles.py` records them anew from the test scenarios.
