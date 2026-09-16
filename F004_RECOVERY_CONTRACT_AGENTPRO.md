# F-004 — Durable Cross-Store Recovery Contract

Status: IMPLEMENTED
Version: 1.0
Date: 2026-09-16

## 1. Sources of truth

This contract derives from:

- `MASTER_CANON_AGENTPRO.md` §§75–84;
- `ARCHITEKTURA_AGENTPRO.md` §§44, 101–102, 114;
- `ADR-0001.md` §7 and §18;
- `ADR-00XX_NARRATIVE_STATE_ENGINE.md` §§24–25;
- `CANONICAL_CHANGE_CONTRACT_AGENTPRO.md` §§9, 11–12.

It does not change those decisions. A canonical entity set still commits atomically
inside `project.db`. Later propagation to a file artifact or `series.db` is a
separate recoverable operation. No global ACID transaction is claimed.

## 2. Owner and identity

`project.db` is the sole owner of a cross-store operation. The stable
`operation_id` binds project, book, optional series, optional run and step,
operation type, source identity, expected versions, provenance, artifact path
and content hash, plus the optional series snapshot semantic hash.

Reusing an `operation_id` with a different input hash is rejected. A technical
retry or recovery uses the original durable payload and does not create a new
logical operation.

## 3. Durable record

The owner stores a versioned `cross_store_operation.v1:<operation_id>` record in
existing project metadata. It contains the required identity and scope, owner,
input hash, expected versions, planned and completed writes, deterministic
status, retry count, last error class, timestamps, provenance, audit references,
lineage and the minimum payload required to repeat idempotent writes.

Statuses are limited to `PENDING`, `IN_PROGRESS`, `AUDIT_PENDING`, `COMMITTED`
and `NEEDS_INTERVENTION`.

## 4. Ordered writes and recovery

The protocol performs:

1. durable intent in `project.db`;
2. project propagation checkpoint in the same owner store;
3. file preparation, flush and fsync, atomic replace, then hash verification;
4. optional existing idempotent `SeriesRepository.close_volume` transaction;
5. logical commit marker in the owner;
6. final audit record and terminal status in one local `project.db` transaction.

Each attempt serializes decisions through `BEGIN IMMEDIATE` in the owner store.
Recovery reads the durable record, verifies existing effects by identity or hash,
and performs only missing idempotent steps. An unexpected artifact hash,
unsupported record version or conflicting durable identity changes the status to
`NEEDS_INTERVENTION`; recovery never guesses or overwrites an ambiguous result.

## 5. Audit and scope limits

Lineage distinguishes initial execution, technical retry, recovery, errors and
manual escalation. The final audit is stored in existing project metadata as
`cross_store_audit.v1:<operation_id>`; this is not a second audit engine.

The optional series step covers the existing volume-closing snapshot operation.
It does not implement full SERIES canonical changes or close F-006. This contract
does not remove `DERIVED_REBUILD_UNSUPPORTED`, start GAP-014, or weaken proposal,
impact, guard, approval or final-validation requirements.
