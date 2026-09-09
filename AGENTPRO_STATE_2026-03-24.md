# AGENTPRO_STATE_2026-03-24

## KANON
Obowiązuje MASTER_CANON_AGENTPRO.
Źródło prawdy dla tego etapu:
1. working tree repo
2. MASTER_CANON_AGENTPRO.md
3. zielone testy regresyjne
4. aktualne artefakty runtime P20.x

## ETAP
Konsolidacja runtime AgentPRO dla novel mode — zamknięta operacyjnie i regresyjnie.

## DOWÓD
Działa:
- app.main jako jedyny owner API
- /agent/step -> app.p20_core.runtime.run_agent_step
- /canon/rebuild jako oficjalny endpoint
- pre/post canon check
- quality gate
- zapis chapter_XXX.json
- commit do kanonu
- spójny canon_snapshot_path
- pełny audit runu

## NAPRAWIONE BŁĘDY
1. master_canon_ref używane przed pewną inicjalizacją w run_agent_step
2. write_audit() nie odkładał pełnego audit artifact do runs/<run_id>

## KONTRAKT MASTER CANONU
MASTER_CANON_AGENTPRO jest spięty z:
- response /agent/step
- chapter_XXX.json
- runs/<run_id>/audit.json

## TESTY KOŃCOWE
- test_app_main_exposes_required_routes
- test_app_main_is_the_owner_of_the_agent_step_contract
- test_agent_step_returns_p20_novel_contract_and_updates_canon_snapshot
- test_master_canon_file_is_resolved
- test_agent_step_returns_master_canon_binding
- test_agent_step_writes_master_canon_into_audit
- test_precanon_reject_returns_master_canon_and_writes_audit

Wynik:
7 passed in 1.97s

## ZMIENIONE PLIKI
- app\p20_core\runtime.py
- app\p20_core\canon_service.py
- app\p20_core\master_canon.py
- tests\test_app_main_owner_regression.py
- tests\test_master_canon_binding.py
- tests\test_master_canon_audit_binding.py
- tests\test_master_canon_reject_path.py
- MASTER_CANON_AGENTPRO.md

## STATUS
- owner app.main: DONE
- runtime P20 novel: DONE
- binding master canon: DONE
- audit binding: DONE
- reject path regression: DONE
- regresja zabezpieczona testami: DONE

## JEDEN NASTĘPNY KROK
Następny etap: uszczelnienie trwałego źródła prawdy projektu tak, aby runtime używał MASTER_CANON_AGENTPRO jako nadrzędnego kontraktu bez dryfu między czatem, repo i artefaktami.
