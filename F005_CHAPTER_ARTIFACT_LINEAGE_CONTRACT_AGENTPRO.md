# F-005 — Chapter Artifact Lineage v2 Contract

Status: IMPLEMENTED
Version: 2.0
Date: 2026-09-16

## 1. Sources

This contract derives directly from:

- `MASTER_CANON_AGENTPRO.md` §§12, 72–76;
- `ARCHITEKTURA_AGENTPRO.md` §§85–91, 97–102;
- `ADR-0001.md` storage and cross-store transaction boundaries;
- `CANONICAL_CHANGE_CONTRACT_AGENTPRO.md` §§8–9 and §12;
- `F004_RECOVERY_CONTRACT_AGENTPRO.md`;
- `ROADMAPA_AGENTPRO.md` Stage 12.

It does not change the accepted architecture or canonical-change contract.

## 2. Artifact and versions

The production `chapter_XXX.json` remains the single chapter artifact. Schema
version 2 retains its current-version compatibility fields and adds an ordered
`versions` collection. WRITE, REWRITE and EDIT create consecutive text versions.
Each version records:

- stable chapter, project, book, run and step identity;
- `version` and `parent_version`;
- status, text, producing step artifact reference and exact input hash;
- reason/source operation and the preceding evaluation when applicable;
- ContextPackage ID/hash and canon, Book Bible and style versions;
- producing role, requested model and effective model;
- quality decision, SHA-256 text artifact hash and creation timestamp.

CRITIC, QUALITY, CONTINUITY, CANON_CHECK, FACTCHECK and STYLE step artifacts are
preserved as resolvable evaluation references with their own hashes. The final
QUALITY reference and decision are attached to the current chapter version.

`artifact_hash` is the SHA-256 hash of the exact UTF-8 text for that version.
F-004 separately verifies the byte hash of the complete JSON document.

## 3. Idempotency and recovery

The chapter operation identity binds project, domain book, optional series, run,
root step and `CHAPTER_ARTIFACT_LINEAGE_V2`. A technical retry with the same
identity recovers the durable F-004 operation and returns the existing chapter;
it does not append another version or allocate another `chapter_XXX.json`.

The complete lineage document is written through the existing F-004
project-owned outbox using its confined `books/` artifact boundary. The write uses
temporary file, flush/fsync, atomic replace and final byte-hash verification.
Only after F-004 reports `COMMITTED` may run state, audit and the API response
reference the chapter artifact.

## 4. Isolation and scope limits

The owner is the project-bound `ProjectRepository`; persisted step artifacts and
ContextPackages must match its project and domain-book identities. Another
project repository cannot enumerate or recover the operation.

This contract does not implement F-002, F-006, F-007, F-009 or GAP-014 and does
not change full SERIES semantics.
