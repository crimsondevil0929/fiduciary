# Changelog

All notable changes to `interlock-fiduciary` are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
