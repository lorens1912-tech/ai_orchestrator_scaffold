# PROJECT TRUTH — AgentAI Author Platform (PRIVATE / PRO)

## Non-negotiable goal (platform)
Build a private, professional author-agent platform that can produce serious written work:
- long fiction (including very long)
- non-fiction guides (often shorter)
- other structured materials

Genre and length are PROJECT CONFIG — never platform identity.
This platform is multi-book and multi-genre by design.

## What "PRO" means here (always)
- resumable, auditable runs (artifacts + state; no "lost progress")
- deterministic pipeline orchestration (presets/steps, per-step overrides)
- enforced quality loop (critic -> revise/edit -> quality; retry) with explicit ACCEPT/REVISE/REJECT
- multi-book isolation (no cross-leakage)
- anti-drift: every tool call receives this flagship context (injected)

## Project profiles (examples, not limits)
Each book/project defines its own profile (per book_id):
- fiction profile: canon + threads + continuity enforcement
- non-fiction profile: fact ledger + definitions/glossary + claim checks + checklists

## Definition of Done (platform)
- A global flagship exists and is injected into every tool call.
- Per-book override flagship exists (books/<book_id>/PROJECT_TRUTH.md) and overrides global.
- A project can declare its own benchmark (e.g., long-marathon for novels OR shorter guide benchmark).
- No platform-level assumptions about genre or length.

## Not the goal (do not drift)
- SaaS/product-market features (billing/auth/onboarding)
- random genre tangents unrelated to active project profile
