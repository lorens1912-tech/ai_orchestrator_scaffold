# GAP-020 — P20.2/P20.3 scripts/integration — Phase 1 contract and impact

Status: **ACCEPTED — PHASE 1 CONTRACT FROZEN**
Implementation status: **NOT STARTED**
Baseline HEAD: `a7fce0e30b7da377228e83855ae0de928f3489f9`
Branch/worktree: `codex/gap020-p20-integration` / `C:\AI\agentpro_gap020`
Scope: GAP-020 only; four phases (1 contract, 2 implementation, 3 integration, 4 test/repair and practical gate).

## 1. Authority and interpretation

- [SPECYFIKACJA] `MASTER_CANON_AGENTPRO.md` §§2–5, 51–58: app-first, `app.main` as official API owner, P20.x as the sole production engine, deterministic process, technical retry distinct from reevaluation, and quality decision distinct from execution status.
- [SPECYFIKACJA] `ARCHITEKTURA_AGENTPRO.md` §§75, 90–91, 141–142: original ContextPackage for technical retry, completed evaluation reuse, explicit reevaluation and context/quality gates.
- [SPECYFIKACJA] `ADR-0001.md` §§1–7, 13: project-bound storage, Series access, cross-store recovery and one mutation guard. `ADR-0002_SERIES_GRAPH_CANONICAL_CHANGE.md` keeps SERIES state in `series.db`. No new storage owner is authorized here.
- [SPECYFIKACJA] Frozen `docs/GAP017_EVALUATION_CONTRACT.md`, `docs/GAP018_PREFLIGHT.md`, `docs/GAP019_PREFLIGHT.md`: operation identity and evaluation cache; Source approval/replay; locale-scoped revision, approval, CAS and recovery. GAP-018/019 final audit reports describe their tested closures, not a GAP-020 implementation.
- [DOWÓD] Historical `audit/agentpro_v2_etap0_20260911_003728/10_GAP_LIST.md` calls GAP-020 `PARTIAL` and “P20.2/P20.3 scripts/integration”. This is an Etap 0 observation, not proof of a current missing retry engine.
- [SPECYFIKACJA] `ROADMAPA_AGENTPRO.md` ETAP 20 is a later, broader production hardening/final audit. GAP-020 does not absorb that work.

## 2. Confirmed current state and meaning of P20.2/P20.3

- [DOWÓD] `scripts/p20_2_tune_auto_retry_policy.py` reads `P20_BASELINE_IN` and `P20_THRESHOLDS_IN` JSON, writes `P20_POLICY_OUT` JSON, and emits GREEN/YELLOW/RED, retry counts/backoff and advisory flags. It has no import of the active app, repository or runtime. Its subprocess test only checks the output shape (`tests/test_p20_2_auto_retry_policy.py`).
- [DOWÓD] `scripts/p20_3_build_retry_feedback.py` reads policy/summary/events files and writes `P20_3_OUT` with a level, event count and `can_relax/keep_monitoring/tighten`. Its two subprocess tests check output values and JSONL fallback (`tests/test_p20_3_retry_feedback_loop.py`).
- [DOWÓD] Repository search finds their environment variables and `retry_feedback_policy` in these scripts/tests only; `app.main`, `app.operator_api`, `app.p20_core.runtime` and `app.p20_core.executor` do not import either script or consume these files. Thus these names currently mean **offline historical analysis tools**, not P20 runtime versions or production policy authority. Their generated files must not be installed as a runtime retry policy merely because of the labels.
- [DOWÓD] `app.main:/agent/step` calls `app.p20_core.runtime.run_agent_step`; runtime imports `app.p20_core.executor.execute_p20` under a local compatibility alias `execute_stub`. The alias is misleading but resolves to the P20 executor, not `app.orchestrator_stub` (`app/main.py:4,99`, `app/p20_core/runtime.py:16,868`, `app/p20_core/executor.py:877,1406`). `app.main` mounts `app.operator_api`; GAP-018 and GAP-019 endpoints call their P20 services. `app.agent_api` still imports `app.orchestrator_stub` but is not mounted by `app.main`. `books_api.py` and `run_book_v2*.ps1` are separate legacy/direct-provider paths, not mounted by `app.main`.
- [DOWÓD] `app/p20_core/runtime.py:557–582` requires step identity for technical retry and rejects retry plus `REEVALUATE`. `app/p20_core/executor.py:616–637,1375–1382` already has a bounded, preset-driven QUALITY → EDIT → QUALITY loop. `app/presets.json:63–76` enables that loop for `ORCH_RETRY_TEST`; it is a test preset, not evidence of a general autonomous production retry policy. `tests/test_gap017_p20_integration.py` and `tests/test_gap017_recovery_hardening.py` cover evaluation reuse, changed-binding conflicts, reevaluation and recovery. GAP-018/019 implement separate domain-specific retry/revision/approval contracts.
- [DOWÓD] `app/p20_core/canon_service.py:1029–1124` uses bounded `revision_feedback` for memory extraction; `app/p20_core/executor.py:179–215` carries that feedback to the model call. This is not the P20.3 file builder.
- [DOWÓD] `run_server.ps1`, `run_server_agent.ps1` and `RUN.bat` launch `app.main:app` but first change to the fixed directory `C:\AI\ai_orchestrator_scaffold`. Running them from the GAP-020 worktree would import the source checkout. `tests/test_123_start_scripts_point_to_p20.py` only asserts the `uvicorn app.main:app` string and port; it does not test the resolved source location, router or process.
- [DOWÓD] `scripts/start_server_full_real.ps1` launches `app.main` but sets `AGENT_TEST_MODE=1`; `app/tools.py:67–68,105–119` shows that this makes WRITE return synthetic text. `scripts/start_server_strict_fastpath.ps1` sets `PYTEST_FASTPATH=1`. Both scripts resolve their own checkout, but their labels/flags are unsafe as a production-start guarantee. A repository search did not find `app.pytest_fastpath` installed by the active `app.main`; this does not make the flag an approved production setting. `config/quality_alert_thresholds.json:auto_retry` is a separate configuration shape; no evidence connects it to the P20.2 JSON output or active P20 quality loop.
- [DOWÓD] The baseline scoped test command with `AGENT_TEST_MODE=1`, `PYTHONDONTWRITEBYTECODE=1` and pytest cache disabled ran `test_p20_2_auto_retry_policy.py`, `test_p20_3_retry_feedback_loop.py`, `test_123_start_scripts_point_to_p20.py`, `test_p20_active_legacy_boundary.py`: **8 passed**. This proves the current limited assertions, not real server startup. Pytest also emitted an unrelated Windows temp-cleanup permission warning.

## 3. Active versus historical matrix

| Component | Classification | Evidence / contract |
|---|---|---|
| `app.main`, mounted `app.operator_api`, `app.p20_core.runtime`, `app.p20_core.executor.execute_p20` | ACTIVE PRODUCTION | Official API → P20 service/executor import/call chain above. |
| ProjectRepository, Context Builder, EvaluationRecord, Memory Ledger and GAP-018/019 services | ACTIVE PRODUCTION | P20 imports and frozen contracts; existing owners remain authoritative. |
| `run_server.ps1`, `run_server_agent.ps1`, `RUN.bat` | ACTIVE SERVER LAUNCHERS WITH DEFECT | Correct module string, wrong fixed checkout root. |
| `scripts/start_server_full_real.ps1` | DIAGNOSTIC/UNSAFE FOR PRODUCTION | `AGENT_TEST_MODE=1` selects synthetic WRITE. Name does not establish “full real”. |
| `scripts/start_server_strict_fastpath.ps1` | DIAGNOSTIC/UNVERIFIED PROFILE | Sets test-oriented `PYTEST_FASTPATH=1`; not a production contract. |
| `scripts/p20_2_tune_auto_retry_policy.py`, `scripts/p20_3_build_retry_feedback.py` | OFFLINE TOOL / HISTORICAL P20.2–P20.3 | File-to-file transformations; no active runtime consumer. Retain as offline tools or explicitly retire if consumers are disproved; never grant runtime authority. |
| Their three subprocess tests, `ORCH_RETRY_TEST`, `test_123_start_scripts_point_to_p20.py` | TEST ONLY | Check offline outputs, test retry preset and launcher strings. |
| `app.agent_api`, `app.orchestrator_stub`, `books_api.py`, `run_book_v2*.ps1`, archived `archive/legacy_p` scripts | LEGACY / UNMOUNTED BY OFFICIAL API | No import/mount from `app.main`; not approved production entrypoints. Separate legacy cleanup belongs to later system audit unless a GAP-020 gate proves direct exposure. |
| P20.2/P20.3 runtime or second executor | OBSOLETE CONCEPT / NOT PRESENT | No such active runtime import or app route was found. Do not create one. |

## 4. Root cause and minimal closure contract

- [DOWÓD] The historical GAP conflates names of offline scripts and shallow launcher tests with proof of integrated v2 runtime. The present concrete entrypoint defects are fixed-root launchers and a “full real” profile that enables synthetic WRITE. The offline files have no production consumer; the current P20 owns retry/evaluation/revision in separate, already frozen paths.
- [REKOMENDACJA] **Do not integrate P20.2/P20.3 outputs into the app.** Phase 2 should make their offline-only status explicit at the script/documentation boundary, without altering their output contract or adding a second retry loop. Retain them while their subprocess tests/possible external offline use exist; deletion requires a proven no-consumer decision.
- [REKOMENDACJA] Phase 2 should make each supported server launcher resolve its own checkout and launch `app.main:app`, and make any launcher advertised as real production avoid `AGENT_TEST_MODE=1` or test-fastpath behavior. Diagnostic profiles should be unmistakably diagnostic and excluded from the production-start contract. Add focused assertions/probes for resolved cwd/module and effective mode, not only a string search. Preserve existing ports unless a verified collision requires change.
- [HIPOTEZA] The exact set of launchers used by an external operator or scheduler cannot be inferred from repository search. Phase 3 must enumerate supported entrypoints and prove their actual process imports; an unknown external launcher is not silently classified as production.
- [SPECYFIKACJA] One production path: supported launcher → `uvicorn app.main:app` from that checkout → `/agent/step` → `run_agent_step` → `execute_p20`; mounted operator API → its existing P20 services. No P0/stub switch, second P20.2/P20.3 executor or file-driven policy override.
- [SPECYFIKACJA] Technical retry retains operation/step and pinned ContextPackage, model provenance and completed evaluation; changed input conflicts. Quality `REVISE` means a new targeted revision/new assessment when authorized by the domain contract; it is not technical retry. `REEVALUATE` is explicit and creates a distinct evaluation identity. Quality decision, validation and technical status remain separate. GAP-018 Source and GAP-019 locale approvals remain separate user authority with their existing idempotency, CAS, recovery and audit.
- [SPECYFIKACJA] Any necessary start-boundary change must leave `ProjectRepository`, `project.db`, `series.db`, Memory Ledger, EvaluationRecord/cache, Context Builder, F-004 recovery and model audit hashes/contracts untouched. Do not create new storage, policy authority, memory path or user-facing retry semantics.

## 5. Phase 2–4 and evidence matrix

| Phase/gate | Required proof before PASS |
|---|---|
| Phase 2 — implementation | Only launcher path/profile corrections, explicit offline/diagnostic classification and corresponding narrow tests as proven above. No domain/runtime retry change without a newly reproduced counterexample against frozen contracts. Syntax/static checks and focused negative test first. |
| Phase 3 — entrypoint integration | Execute every supported launcher from a different cwd and the GAP-020 worktree; assert process module origin, `app.main` owner, mounted operator routes and `/agent/step → run_agent_step → execute_p20`. Assert no `app.agent_api`, `app.orchestrator_stub`, P0, alternate P20.2/P20.3 runtime or offline JSON policy consumer in this chain. Check test/diagnostic profiles cannot masquerade as production. |
| Phase 3 — semantics/integrity | Official API tests for technical retry same ID/context/result without duplicate model/evaluator; changed binding conflict; `REVISE` targeted correction and new QA; explicit reevaluation new identity; feedback and model provenance; idempotency, fault/recovery, restart/reopen and project isolation. Use existing GAP-017/018/019 tests; add only a missing cross-boundary assertion. |
| Phase 3 — regression | Preserve GAP-018 Source approval/CAS/reopen and GAP-019 locale separation, QA/revision/approval/recovery. Existing dedicated gates plus relevant P20 boundary tests must run after last change. |
| Phase 4 — real-app practical gate | Spawn a real local `uvicorn app.main:app` process through each **supported production launcher** with isolated temporary project storage and controlled transport; verify `/health`, official `/agent/step`, authenticated operator API, exact imported source/flags, durable project-owned evidence, restart/reopen, retry/revision/reevaluation behavior, no P0/legacy/offline-policy path. Include a negative synthetic-mode check and a separate controlled real-transport/provenance check. Do not use real book data. |
| Phase 4 — final proof | Repair any reproduced scoped finding, rerun targeted/integration/practical gates, perform scoped re-review against Canon/Architecture/Roadmap and frozen GAP-018/019 contracts, then record exact results and final Git scope. A green unit suite alone is insufficient. |

[REKOMENDACJA] Full ETAP 20 hardening (global backup/restore, lock stress, broad cleanup, all-GAP final audit and full system regression) remains a separate later task. Phase 4 may run a broader regression if scoped repairs require it, but this contract does not redefine ETAP 20.

## 6. Risks, exclusions and closure

- [DOWÓD] Launcher tests can pass while loading the wrong checkout; a label “FULL_REAL” can conceal test-mode generation. These are Phase 2/3 acceptance risks, not evidence that P20 retry is absent.
- [HIPOTEZA] External shell shortcuts, services and scheduler commands may point to other modules; they require explicit inventory or a practical process check before a universal production-start claim.
- [SPECYFIKACJA] Out of scope: new retry scheduler/policy ingestion, a second feedback loop, P0 rewrites, broad legacy deletion, global storage or schema migration, GAP-018/019 contract changes, publishing/KDP, cleanup of the dirty source checkout and later ETAP 20.
- [SPECYFIKACJA] GAP-020 can close only after Phase 4 proves: supported launchers load the intended checkout and non-test `app.main`; official API reaches one P20 executor and existing operator services; P20.2/P20.3 remain offline or are explicitly retired; retry/REVISE/reevaluation/provenance/idempotency/recovery/isolation/reopen satisfy their existing contracts; GAP-018/019 regressions pass; no unresolved scoped blocker/high finding remains. Phase 1 acceptance freezes this contract only; **GAP-020 is not CLOSED**.


## 7. Phase 1 static re-review

| Source checked against this contract | Result |
|---|---|
| Master Canon §§2–5, 51–58 and Architecture §§75, 90–91, 141–142 | One app/P20 runtime, preserved ContextPackage, retry/reevaluation and quality axes; no conflict found. |
| Roadmap ETAP 20 and historical GAP-020 row | This contract keeps the numbered GAP limited to scripts/entrypoint integration; broad ETAP 20 remains separate. |
| ADR-0001/0002 and GAP-017/018/019 frozen contracts | No new storage owner, approval route, model policy, retry engine or evaluation identity is proposed. |
| Current start scripts, active P20 imports, operator routes and scoped tests | Fixed-root and synthetic-mode defects are explicitly deferred to Phase 2; existing tests do not claim their absence. |
| GAP-018/019 final audits | Their acceptance evidence is used as a regression baseline; this document does not reopen or silently alter their contracts. |

[DOWÓD] Static re-review found **no authority conflict in the proposed GAP-020 boundary**. The two launcher defects remain open for Phase 2 and are not represented as repaired by this Phase 1 freeze.
