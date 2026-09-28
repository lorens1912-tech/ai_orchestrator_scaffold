# GAP-019 final independent audit

Date: 2026-09-28 (Europe/Warsaw)
Branch: `codex/gap019-translation`
Frozen Phase 1 HEAD: `b8f4914530012c2a2e6c760c4574926aeec376a0`
Authority: `docs/GAP019_PREFLIGHT.md` (unchanged)

## Final decision

**ACCEPT.** Open BLOCKER: 0; open HIGH: 0; open MEDIUM requiring repair: 0. GAP-019 Phase 5 is accepted on the code and tests recorded below. No GAP-020 or publishing/KDP work is included.

## Independent findings and repairs

The findings were reproduced before repair with domain probes or a failing official API regression. The current source locations identify the repaired boundary.

| ID | Severity | Invariant, evidence and failure mode | Repair and regression |
| --- | --- | --- | --- |
| G019-F01 | HIGH | Exact run lineage: a `TRANSLATION_UNIT` from run A could seal run B when Source/Bible matched. `translation.py::seal_translation_version` did not compare unit `run_id`. | Reject cross-run UNIT; `test_run_unit_and_qa_finding_provenance`. |
| G019-F02 | HIGH | QA evidence provenance: a report could accept a finding with forged source/target hashes and context/invocation refs, producing a quality decision. `translation.py::create_translation_qa_report`. | Verify every finding against its unit and matching coverage provenance; forged report is `BLOCKED/INVALID` with no decision. Same regression. |
| G019-F03 | HIGH | Durable replay integrity: a committed Bible request returned its record after the artifact file was corrupted. `translation.py::_artifact_record`. | COMMITTED replay now calls `load_translation_record`, including artifact SHA-256 and F-004 verification; same regression. |
| G019-F04 | HIGH | Operator authority: generic unauthenticated `/agent/step` could enter P20 TRANSLATE execution. `executor.py::execute_p20`. | Require authenticated scoped GAP-019 operator execution endpoint; generic route returns 403. `test_official_two_locale_flow_and_target_authority`. Legacy `app/tools.py::tool_translate` was not changed. |
| G019-F05 | HIGH | QA efficacy: the original positive gate used a QA stub, and an actual provider returned fenced JSON that failed parsing. No evidence showed real detection of omissions or names. `translation_execution.py::execute_translation_qa`. | Strict structured QA response with one assessment per criterion; deterministic literal Bible/locale checks only for provable cases. Two live QA gates now produce 15/15 valid coverage and evidence-backed REVISE for deliberately defective targets in both locales. |
| G019-F06 | MEDIUM | Same execution request with another model returned the committed translation (HTTP 200) because UNIT replay skipped the model audit. `translation_execution.py::execute_translation_run`. | Compare pinned requested/effective model, mode, scope and operation ID before replay; changed model returns 409 without a provider call. API regression for both locales. |
| G019-F07 | MEDIUM | Staleness was calculated from durable heads but the observation itself was not durable. `translation.py::translation_staleness`. | Write a deterministic immutable `STATUS` observation bound to record hash and observed Source/Bible heads. Repeated reads are idempotent; en-GB is unaffected by en-US Bible change. `test_new_source_marks_old_translation_stale_without_mutating_history` and `test_bible_change_stales_only_its_locale`. |
| G019-F08 | MEDIUM | A completed run returned `TRANSLATION_INPUT_STALE` (HTTP 409) on technical replay after a newer Bible, instead of its historical result. `translation_execution.py::execute_translation_run`. | Verify committed seal receipt, version and every UNIT/audit, then return the original version without moving heads or calling the provider. Incomplete stale work remains blocked. API regression. |
| G019-F09 | HIGH | Historical replay returned HTTP 200 after the persisted provider `result_hash` was corrupted. `translation_execution.py::_check_pinned_model`. | Recompute result/output hashes and require completed transport (and valid translation output) before replay. Tampered evidence returns 409; API regression. |

## Frozen invariant matrix

| Contract area | Final evidence | Verdict |
| --- | --- | --- |
| Approved SourceMaster only, exact ID/version/hash and explicit start; Candidate cannot substitute; no automatic start | `test_gap019_phase2.py`, official API test, adversarial gate | PASS |
| Source spans, chapter boundaries, byte coverage, hashes and immutable Source snapshot | Domain tests, 200k gate (51 contiguous units, zero lost bytes) | PASS |
| Translation Bible explicit user authority, version/hash, locked names/terms/forbidden substitutions and locale scope | Domain tests, adversarial gate, live QA cases | PASS |
| Separate en-US/en-GB lineage, heads, units, QA, receipts, approvals and Master | Official two-locale flow, adversarial isolation gate | PASS |
| QA execution/validation/decision separation; BLOCKED has no quality decision | Contract/API/adversarial tests | PASS |
| QA finds semantic errors, omission, addition, mistranslation, terminology, names, locale, Bible and style/voice; unit/chapter/book reduction | Two actual-provider QA gates and domain hierarchy tests | PASS |
| Targeted REVISE keeps unaffected hash-verified units and runs fresh complete QA; history immutable | Adversarial gate and API tests | PASS |
| Source/Bible staleness, historical Master readability, durable observation, no automatic retranslation | Domain regressions and adversarial gate | PASS |
| Candidate is not TargetMaster; separate authenticated user approval, rejection, challenge and exact CAS | Official positive flow, negative/adversarial gate | PASS |
| Same request replay, changed payload/model conflict, one CAS winner, loser `HEAD_CONFLICT` | API replay regressions and adversarial race | PASS |
| F-004, Memory Ledger, recovery without synthetic approval or repeated model call | Recovery gate (15 fault checkpoints), cross-store tests | PASS |
| Schema 7→8, project-owned constraints/primary-key indexes, legacy rows, reopen, no remigration, `series.db` unchanged | Migration gate and storage regression | PASS |
| Router/provider, ContextPackage, Source/Bible/locale and invocation/response hashes | Live provider gate and provenance tests | PASS |
| Two-project read/write/recovery isolation | Adversarial gate and project graph tests | PASS |
| Official P20 cannot use legacy passthrough; no publishing/KDP or GAP-020 | Executor 403 regression and final diff inspection | PASS |

## Practical gate evidence

- **Live provider and positive flow:** `python -m tests.gap019_phase4_practical_gate` PASS. Both en-US and en-GB used the official operator API and Model Router, provider `OPENAI`, requested model `null`, effective model `gpt-4.1-mini`, `TRANSPORT_COMPLETED/VALID`. en-US invocation `INV-4ba48d443bb74ef394f1c22f2c6894d1`, response `resp_0f6ff777c56a60f2006ab99bf9885c87d18ea0bebe0c1da6cd`; en-GB invocation `INV-700f8b96a0624de6bfedd7c7b0e371e4`, response `resp_02c817f24c04a021006ab99c13765487d18909f2c4dd514bd0`. The gate checks ContextPackage, SourceMaster, Bible hash, locale, output hash, user approval, durable TargetMaster and reopen under schema 8. This positive gate controls QA at the transport boundary; actual QA efficacy is evidenced below.
- **Actual-provider QA:** `python -m tests.gap019_phase5_quality_gate` PASS and `python -m tests.gap019_phase5_quality_cases_gate` PASS. For both locales, actual provider transport was `TRANSPORT_COMPLETED/VALID`, 15/15 criteria were covered, quality decision was `REVISE`, and evidence-backed findings included semantic fidelity, omissions, additions, mistranslations, Bible, terminology, names/entities, locale and voice/style. No TargetMaster was created for those defective targets.
- **Negative, targeted REVISE, CAS and isolation:** `python -m tests.gap019_phase4_adversarial_gate` PASS. Includes absent/unpromoted/wrong Source, wrong project/book, QA REVISE/REJECT/BLOCKED, user reject/no Master, challenge failures, stale Source/Bible, targeted two-unit revision with one unaffected UNIT retained, two-project isolation and one CAS loser with `HEAD_CONFLICT`.
- **Recovery/fault injection:** `python -m tests.gap019_phase4_recovery_gate` PASS. Fifteen checkpoints cover before/after intent, staged artifact, F-004 prepare/commit/logical commit, after model receipt, QA, user approval, before CAS, and commit-before-response. Reopen succeeds; one translation model call and one QA model call survive retries.
- **Migration:** `python -m tests.gap019_phase4_migration_gate` PASS. Schema 7→8, `PRAGMA integrity_check=ok`, 12 Memory Ledger rows retained, SourceMaster retained, no schema-8 remigration, `series.db` unchanged.
- **Long SourceMaster:** `python -m tests.gap019_long_source_gate` PASS. 200,000 words, two chapters, 51 bounded translation UNITs and ContextPackages, 51 QA calls, 58 Source Book QA calls, three Bible decisions, max Source span 8,000 bytes, max prompt 25,259 bytes, complete byte/hash coverage, book QA `ACCEPT`. Provider transport is controlled only for this scalability gate; live provider calls are separately verified above.

## Test quality, targeted tests and full regression

The original positive QA stub proved transport and persistence, not real defect detection. The two live QA gates above close that evidence gap. The long-source stub is confined to provider transport and asserts exact source/target span identity and complete coverage; it does not claim real literary translation quality.

- `python -m pytest -q -p no:cacheprovider tests/test_gap019_audit_regression.py tests/test_gap019_phase2.py tests/test_gap019_phase4.py tests/test_gap019_api_integration.py` with `AGENT_TEST_MODE=1`: **20 passed**.
- GAP-018, Memory Ledger, migration, Context Builder, model provenance, F-004 recovery and project graph regression (12 explicit test files): **592 passed**.
- `python -m pytest -q -p no:cacheprovider` with `AGENT_TEST_MODE=1`, after the last source change: **1249 passed, 2 skipped** in 1:04:05.
- `git diff --check`: PASS. AST parse of 14 new GAP-019 modules/scripts/tests: PASS. Frozen `docs/GAP019_PREFLIGHT.md`: no diff.

## Final scope

Only GAP-019 translation/localization domain, contract and execution modules, authenticated operator API, minimal P20 integration/storage/Context Builder/F-004 boundaries, schema-8 compatibility test updates, GAP-019 tests and practical gates are included. `app/tools.py`, frozen GAP-019 preflight, `series.db`, publishing/KDP and GAP-020 are outside this change.
