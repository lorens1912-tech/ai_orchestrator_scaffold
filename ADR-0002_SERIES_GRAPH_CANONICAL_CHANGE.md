# ADR-0002 — SERIES Graph and Canonical Change

## Status

**ACCEPTED**

Acceptance date: 2026-09-16

## Context

`SeriesRepository`, `SeriesAccessContext` and `series.db` already own explicitly
shared series state. The existing Impact Analysis and graph traversal operate
only on `project.db`, so a protected SERIES canonical change cannot pass the
required pre-commit impact gate. A PROJECT fallback would violate storage and
scope isolation.

## Decision

1. `series.db` is the sole owner of SERIES canonical state and SERIES-scoped
   `EdgeRecord` rows.
2. PROJECT and SERIES use the same `EdgeRecord`, `GraphNodeRef`, traversal policy
   and traversal implementation through scope-aware repository adapters. No
   second graph engine is introduced.
3. `SeriesRepository` persists SERIES edges in a versioned schema, validates an
   explicit `SeriesAccessContext` and registered membership, and performs
   traversal only inside its bound `series.db`.
4. SERIES canonical change follows proposal, impact, guard, approval, final
   validation, local commit and audit. Proposal state, current canonical records,
   edge basis, version history, receipt and audit are stored in `series.db`.
5. The authoritative SERIES record set and its local audit commit in one SQLite
   transaction. Retry returns the existing receipt for the same proposal and
   proposal hash.
6. Approval binds proposal ID/hash, initiating project, SERIES scope and ID,
   impact basis, current canonical/graph basis, operator credential and
   `authorization_ref`. A changed record, graph, proposal or credential makes the
   previous approval unusable.
7. PROJECT fallback for SERIES is forbidden. F-004 applies only to operations
   that actually cross a storage boundary; it is not used for this local commit.

## Preserved constraints

- `CURRENT_PROTECTION_UNKNOWN` remains fail-closed.
- `DERIVED_REBUILD_UNSUPPORTED` remains in force.
- GAP-014 is not started.
- Existing PROJECT graph and canonical-change behavior remains unchanged.

## Consequences

`series.db` requires a controlled schema migration adding its local `edges`
table and lookup indexes. Operator endpoints may resolve a proposal to an
authorized SERIES repository, but all reads and writes still require the
initiating project's registered series membership. Cross-series and
PROJECT/SERIES reads are rejected before graph or canonical state access.
