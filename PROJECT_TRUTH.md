# PROJECT TRUTH — AgentAI Author Platform (PRIVATE / PRO)

## Non-negotiable goal (platform)
Build a private, professional author-agent platform for producing serious written work:
- long fiction (including very long)
- non-fiction guides (often shorter)
- other structured materials

multi-book + multi-genre.
Genre and length are PROJECT CONFIG — never platform identity.

## What "PRO" means here (always)
- resumable, auditable runs (artifacts + state; no "lost progress")
- deterministic pipeline orchestration (presets/steps, per-step overrides)
- enforced quality loop (critic -> revise/edit -> quality; retry) with explicit ACCEPT/REVISE/REJECT
- multi-book isolation (no cross-leakage)
- anti-drift: every tool call receives this flagship context (injected)
- writing is the core: WRITER produces content; control systems exist to keep it coherent at scale

## Project policy (configurable, not a restriction)
- guides/non-fiction: multi-project parallel work may be enabled by project policy
- novels/long fiction: default policy is single active project at a time for quality
(architecture still supports multi-project for any type; policy decides)

## Project profiles (examples, not limits)
each book/project defines its own profile (per book_id):
- fiction profile: canon + threads + continuity enforcement + long-marathon benchmark
- non-fiction profile: fact ledger + definitions/glossary + claim checks + checklists + shorter benchmark

## Definition of Done (platform)
- a global flagship exists and is injected into every tool call.
- per-book override exists (books/<book_id>/PROJECT_TRUTH.md) and overrides global.
- each project declares its own benchmark (long-marathon for novels OR shorter guide benchmark).
- no platform-level assumptions about genre or length.

## Not the goal (do not drift)
- SaaS/product-market features (billing/onboarding)
- random genre tangents unrelated to active project profile
