# GAP-020 final report

Date: 2026-09-28 (Europe/Warsaw)
Branch: `codex/gap020-p20-integration`
Frozen Phase 1 HEAD: `7c34f2aabe14d35a6ee61762586f8286af4b8a5c`
Authority: `docs/GAP020_PREFLIGHT.md` (unchanged)

## Final decision

**ACCEPT. GAP-020 = CLOSED / FROZEN.** Phase 1 = CLOSED / FROZEN; Phase 2 = ACCEPT; Phase 3 = ACCEPT; Phase 4 = ACCEPT. Open scoped BLOCKER/HIGH findings: 0. The final full-repository regression and every post-regression process gate below passed.

## Findings, root causes and repairs

| ID | Finding and reproduced cause | Minimal repair |
| --- | --- | --- |
| G020-F01 | `run_server.ps1`, `run_server_agent.ps1` and `RUN.bat` changed to a fixed `C:\AI\ai_orchestrator_scaffold` directory. From the GAP-020 worktree they could run the old checkout even while displaying `app.main:app`. | Resolve the directory of each launcher itself; explicitly set `AGENT_TEST_MODE=0` and clear inherited `PYTEST_FASTPATH`. |
| G020-F02 | The launcher labelled `FULL_REAL` set `AGENT_TEST_MODE=1`, which selects synthetic WRITE. | Resolve its own checkout, set `AGENT_TEST_MODE=0`, clear fastpath and launch `python -m uvicorn app.main:app`. |
| G020-F03 | The strict-fastpath script was ambiguous beside production launchers. | Label it diagnostic and keep its `PYTEST_FASTPATH=1` role explicit. P20.2/P20.3 scripts are documented as offline JSON tools with no runtime authority. |

No new production defect was found in Phase 4. Gate assertions needed correction against the existing contract: Python's import-time profiler omits a top-level module loaded through `importlib.import_module`, so the process gate uses verbose import paths; the GAP-019 GET Bible representation adds `effective_status`, so restart compares every persisted field from POST; omitted project/book context creates an explicit isolated `PROJ-runtime-*` scope; changed technical-retry binding returns 409 Conflict.

## Active runtime and launcher matrix

The supported production chain is launcher → Python 3.11 `-m uvicorn app.main:app` → `app.main` → mounted `app.operator_api` and `app.p20_core.runtime.run_agent_step` → `app.p20_core.executor.execute_p20`. The runtime compatibility alias `execute_stub` points to `execute_p20`. The process import trace identifies `app/main.py` in the intended checkout and P20 runtime/executor. The official OpenAPI surface contains `/agent/step` and operator routes; it does not mount `app.agent_api`.

| Launcher | Role | Own/foreign cwd process | Alternate valid worktree process |
| --- | --- | --- | --- |
| `run_server.ps1` | production, port 8001 | PASS | PASS |
| `run_server_agent.ps1` | production, port 8001 | PASS | PASS |
| `RUN.bat` | production, port 8001 | PASS | PASS |
| `scripts/start_server_full_real.ps1` | production, port 8000 | PASS | PASS |
| `scripts/start_server_strict_fastpath.ps1` | diagnostic, port 8000 | PASS as diagnostic | PASS as diagnostic |

All production launchers were started with inherited `AGENT_TEST_MODE=1` and `PYTEST_FASTPATH=1`; they set effective production mode to 0 and clear fastpath. A separate process sent production WRITE to a local controlled Responses HTTP provider and persisted a `VALID` WRITER invocation with requested/effective model and provider response ID. This establishes that the production launcher did not silently use synthetic WRITE. The controlled provider returned a deliberately nonconforming result for the subsequent memory extraction; no canonical-memory success is claimed from that probe.

## Process Practical Gate

`python -m tests.gap020_phase4_practical_gate`: PASS on isolated neutral storage. It launched real processes, not TestClient, through all five launchers. On `/agent/step`, project A returned QUALITY `REVISE`; project B returned `ACCEPT`, with distinct project-owned ContextPackage and EvaluationRecord. Omitted project/book context received a distinct explicit runtime scope; malformed project identity returned 422, wrong project resume returned 422, changed retry binding returned 409 and retry plus reevaluation returned 422. The existing bounded `ORCH_RETRY_TEST` preset ran QUALITY → EDIT → QUALITY. It is a test preset, not a new production retry policy.

The same process exercised authenticated operator identity, SourceMaster current read, protected Bible version creation, and en-US/en-GB official translation-run creation. Unauthenticated Source read and foreign project/book read were denied; generic `/agent/step` TRANSLATE returned 403 for both locales.

After process stop/restart, SourceMaster, both Bible/run records and their hashes were recovered from the same storage. Technical retry reused the completed EvaluationRecord. Explicit reevaluation created a distinct record linked to the original. The audit record and both project repositories retained the expected project scope; cross-project evaluation IDs were absent.

`python -m tests.gap020_controlled_transport_gate`: PASS; the local HTTP provider received the production WRITER request and a separate memory-extractor request. WRITER model audit was `TRANSPORT_COMPLETED/VALID`.

`python -m tests.gap020_alternate_worktree_gate <managed worktree>`: PASS for all five launchers from a foreign cwd, repeated after the final full regression; each process loaded `app.main` from that alternate checkout and completed project-local QUALITY. The temporary managed worktree was archived after the test.

Runtime import traces exclude `app.agent_api`, `app.orchestrator_stub`, P20.2/P20.3 modules and the old checkout. The `run_book_v2*` scripts are not launched by the tested production entrypoints. P20.2/P20.3 remain offline tools, with no production policy or retry authority.

## Regression and integrity

- Targeted GAP-020 launcher/runtime/offline tests: **21 passed**.
- AST parse of six changed Python files: PASS.
- `git diff --check`: PASS.
- Full repository regression after the last Python code change: **1262 passed, 2 skipped** in 1:08:26 (`AGENT_TEST_MODE=1`, bytecode and pytest cache disabled).
- Final post-regression Practical Gate: **PASS** for primary/foreign cwd, alternate worktree, production/test-mode boundary, `/agent/step` P20, operator API, retry/REVISE/reevaluation, restart/reopen, project isolation, GAP-018/019 smoke, offline P20.2/P20.3 and P0/legacy exclusion.
- Frozen `docs/GAP020_PREFLIGHT.md`: no diff.

## Scope

Only the five GAP-020 launchers, two offline-tool docstrings, GAP-020 tests/process gates and this report are included. P20 retry/domain logic, ProjectRepository, storage schema, Context Builder, Memory Ledger, GAP-018/019 contracts, P0/legacy code, main dirty checkout, ETAP 17 and publishing/KDP are unchanged.
