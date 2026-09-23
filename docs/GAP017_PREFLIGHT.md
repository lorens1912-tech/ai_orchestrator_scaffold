# GAP-017 — System 2 — faza 1/5

PHASE 1 = COMPLETE
CONTRACT STATUS = ACCEPTED / FROZEN FOR GAP-017 IMPLEMENTATION
PHASE 2 = IMPLEMENTATION COMPLETE
PHASE 2 TARGETED TESTS = 47 passed
PHASE 2 REPOSITORY/STORAGE REGRESSION = 19 passed
FULL REGRESSION = NOT RUN
GAP-017 = NOT CLOSED
PHASE 3 = NOT STARTED
Data: 2026-09-16.
Dokument opisuje analizę statyczną, nie wykonany functional proof.

## 1. Checkpoint i granice

- Repo: `C:\AI\ai_orchestrator_scaffold`.
- Gałąź: `codex/agentpro-stabilization-freeze`.
- HEAD lokalny i `git ls-remote --heads origin refs/heads/codex/agentpro-stabilization-freeze`:
  `bfe0bc2e6d35a44b78b6849d4af64ba57cc13e61`.
- Staging pusty. Baseline: 2546 pozycji `--porcelain=v1 -uall`,
  3 tracked modifications i 2543 untracked. Porównano pełne ciągi status/path,
  nie tylko liczby, z `gap016_evidence/final-git-status-porcelain.txt`;
  delta = 0. Porównano również grouped `final-git-status-short.txt`
  i `checkpoint.json`. Wynik poprzedniej regresji w GAP016_REPORT:
  695 passed, 1 skipped — dowód historyczny, nie test GAP-017.
- Oba docelowe dokumenty i katalog phase1 nie istniały przed zapisem.
- Nie przeglądano treści książek ani nie wykonywano ich fingerprintów.
  Uwaga proceduralna: końcowe `git diff --check` omyłkowo nie miało pathspec,
  więc Git sprawdził także trzy zastane tracked modifications w books/.
  Wyświetlił wyłącznie ostrzeżenia CRLF, bez treści i bez zapisów do tych plików.
  To przekroczyło zamierzony zakres kontroli; nie powtarzano tej operacji.
  Porównanie status/path nie dowodzi równości zawartości untracked plików.
  W tej fazie polecenia zapisu obejmowały wyłącznie dwa dokumenty i nowe
  dowody phase1. Nie uruchamiano aplikacji, migracji, importów ani pytest.

## 2. Źródła i zakres

| Źródło | Wiążący zakres |
|---|---|
| MASTER_CANON_AGENTPRO.md §51–58, linie 536–599 | context hash; technical retry; nowa ocena; quality != execution; wersjonowany rekord i cache |
| ARCHITEKTURA_AGENTPRO.md §86–92, linie 2889–3031 | niezależność oceny; pola EvaluationRecord; pełny klucz; retry/reevaluation |
| ROADMAPA_AGENTPRO.md ETAP 13, linie 317–333 | quality, EvaluationRecord, wersje kryteriów/promptu, model, cache, pętla poprawy |
| audit/agentpro_v2_etap0_20260911_003728/10_GAP_LIST.md:27 | GAP-017 = EvaluationRecord / Evaluation Cache; zależności GAP-013/GAP-016; historyczne MISSING nie jest bieżącą diagnozą |
| ADR-0001.md §1, §5–7 | project.db owner, scope-bound repository, jawne granice cross-storage |
| docs/GAP016_MODEL_PROVENANCE.md: Trwałość i recovery; Powiązanie i odczyt | trace wykonania modelu nie jest Evaluation Cache |
| docs/GAP016_REPORT.md: Implementacja; Wyniki bramek | stan wdrożonego GAP-016 i ograniczenia dowodów |
| CANONICAL_CHANGE_CONTRACT_AGENTPRO.md §1, §5–12, §15 | quality nie nadaje authority; niezależna weryfikacja, approval, final guard |
| F004_RECOVERY_CONTRACT_AGENTPRO.md §2–5 | istniejący owner, intent, odtwarzalne zapisy i interwencja |
| F005_CHAPTER_ARTIFACT_LINEAGE_CONTRACT_AGENTPRO.md §2–4 | konkretny tekst/hash, final QUALITY, immutable lineage i F-004 |

Nie znaleziono konfliktu wymagającego zmiany zaakceptowanej architektury.
Historyczne zapisy FUTURE w starszych kontraktach nie unieważniają późniejszego
wdrożenia F-004. Uszczegółowienia są w osobnym kontrakcie ACCEPTED / FROZEN FOR GAP-017 IMPLEMENTATION.
GAP-017 nie obejmuje całego ETAPU 13, Book QA ani Source Master (GAP-018).

## 3. Aktywne ścieżki i rzeczywisty stan

| Element | Dowód kodowy | Stan i luka |
|---|---|---|
| API i runtime | app/main.py:84 → run_agent_step; app/p20_core/runtime.py:404 | aktywny P20; P0 nie jest potrzebny |
| Scope i retry | runtime._assert_run_execution_identity:367; context_runtime.ProjectExecutionContext:243, for_step, operation_id | project/book/series/run/step istnieją; retry wymaga step_id; nie daje jeszcze odtworzenia Quality |
| ContextPackage | context_runtime.build_runtime_context_package:419; ContextBuilder.reuse_for_retry:942; ProjectRepository.save_context_package:1775 | zapis i reuse istnieją; brak związania kompletnego wejścia oceny i jej wyniku |
| CRITIC | app/tools.py::tool_critic:129, rejestr TOOLS | lokalny stały zestaw uwag, nie LLM-as-judge; nie ma finalnego werdyktu jakości |
| QUALITY | tools.tool_quality_pipeline:717 → quality_contract._fq_tool_quality lub quality_rules.evaluate_quality:33 | dwa lokalne algorytmy; wybór przez obecność min_words; progi i zasady zachować |
| Final QUALITY binding | executor.execute_p20:844–888 | style veto, QUALITY-EVAL-run:ordinal tylko przy aktywnej sesji stylu; brak pełnego trwałego EvaluationRecord |
| StyleEvaluation | adaptive_style.StyleCritic.evaluate:741; AdaptiveStyleSession.evaluate/finalize:769/775 | osobny trwały rekord stylu i memory tylko po ACCEPT; nie zastępuje ogólnej oceny jakości |
| Pętla poprawy | executor._quality_retry_steps:330 i execute_p20:941 | kolejka kroków po non-ACCEPT, nowe ordinale; nie mylić z technical_retry |
| Konsument werdyktu | runtime:687–715 i :719 | odczyt pierwszego pliku z sufiksem _QUALITY.JSON; ACCEPT uruchamia lineage i canonical pipeline |
| Artefakty i audyt | executor:890–939; runtime:804–842; canon_service.write_audit; chapter_lineage._evaluation_ref:182 / persist_chapter_lineage:408 | step JSON, run_state, audit, chapter lineage istnieją; trzeba dopiąć referencję do oceny bez przepisywania starych hashy |
| ModelInvocationAudit | model_provenance.ModelInvocationAudit, public_trace; ProjectRepository.model_invocation_transaction:1309 | trwały transport, próby, walidacja i recovery; nie domenowa ocena tekstu |
| Weryfikacja kanonu/researchu | executor.invoke_memory_model:52; canon_service.process_accepted_artifact; research._model_call/run_research | rzeczywiste osobne EXTRACT/VERIFY przez call_text; ich osie precision/completeness nie są Quality Gate tekstu |
| Cross-store recovery | cross_store_recovery.CrossStoreRecoveryService:269; chapter_lineage.persist_chapter_lineage | istniejące F-004; nie potrzeba drugiego recovery engine |
| Recovery modelu | research.recover_operation; operator_api.recover_research:350; model_provenance.recover_invocation | tylko research ma tę autoryzowaną ścieżkę; nie jest gotowym ogólnym recovery ocen |

Wyszukiwanie `EvaluationRecord|evaluation_cache` w app/tests (bez backupów)
nie znalazło implementacji. Potwierdzenie braku obejmuje też odczyt executora,
repozytorium, ContextBuildera, jakości i istniejącego model provenance.
Metadane requested/effective_model na lokalnym kroku są routingiem,
nie dowodem faktycznego wykonania LLM. Żadna ocena lokalna nie dostaje
fikcyjnego invocation_id ani provider-reported modelu.

## 4. Ryzyka i impact wymagający późniejszych testów

1. Runtime wybiera pierwszy QUALITY, a retry tworzy `__attempt_XX.json`,
   który nie pasuje do tego sufiksu. Kolejne QUALITY po REWRITE również
   nie może zostać zastąpione pierwszym werdyktem. To dowód z kodu;
   reprodukcja API jest zaplanowana, obecnie NOT VERIFIED.
2. Executor od nowa wywołuje tool przy technical_retry. Sam reuse kontekstu
   i tworzenie kolejnego pliku próby nie stanowią Evaluation Cache.
3. `quality_version` istnieje dla gałęzi canonical; druga gałąź i efektywne
   progi/min_words/forbid_lists wymagają pełnego snapshotu wersji wejść.
   Komentarz legacy FAIL oraz BLOCK_PIPELINE porównujące z FAIL są niespójne
   z aktualnym evaluate_quality (zwraca ACCEPT/REVISE/REJECT).
   Nie projektujemy konwersji nieistniejącego werdyktu ani zmiany progów.
4. Zapis StylePerformance następuje w executorze przed step artifact.
   Nowy rekord jakości musi być trwale ukończony przed konsumentami;
   crash między tymi zapisami wymaga idempotentnego dokończenia, nie drugiej oceny.
5. CRITIC uwagi, StyleEvaluation, sprawdzenie kanonu i weryfikacja ekstrakcji
   pozostają osobnymi dowodami. Nie wolno automatycznie nadać im quality ACCEPT.
6. Style performance ma ID zależne od recipe/text. Reevaluation nie może
   nadpisać historycznego powiązania pierwszego performance z jego oceną.
7. Nie ma ogólnego operatorskiego recovery Evaluation. Najmniejsza integracja
   musi użyć istniejącej autoryzacji i lokalnych transakcji; nie wolno kierować
   rekordów Evaluation do endpointu research/recover z obcą semantyką.

## 5. Plan faz 2–5 — ACCEPTED; PHASE 2 = IMPLEMENTATION COMPLETE

Model/poziom to zalecenia dla późniejszych zadań, nie zmiana ustawień sesji.

| Faza | Zakres | Zalecenie | Bramka |
|---|---|---|---|
| 2 | zaakceptowany kontrakt → wersjonowany rekord/klucz, project persistence, lokalne transakcje i integralność | GPT-5.6 Sol / wysoki | schema, scope, reopen, immutable result, concurrency unit/contract tests |
| 3 | aktywny P20 QUALITY, konkretny artifact, końcowy werdykt, reuse i jawna reevaluation, audit projections | GPT-5.6 Sol / wysoki | TestClient(app.main.app), ACCEPT/REVISE/REJECT, ostatnia właściwa ocena, bez fikcyjnego LLM |
| 4 | failure injection/recovery, spóźnione próby, izolacja, style/canon/lineage/GAP-016 integration | GPT-6 Astra / wysoki | trwałe dowody crash/reopen/duplikatów i zachowania authority |
| 5 | review, bramki finalne, jedna pełna regresja po finalnej zmianie, commit/checkpoint wyłącznie w autoryzowanym zakresie | GPT-5.6 Sol / wysoki | wszystkie kryteria kontraktu; baseline zachowany; bez pustego commita |

Faza 2 została rozpoczęta osobnym poleceniem i ukończyła fundament persistence.
Faza 3 nie została rozpoczęta. Nie ma nierozstrzygniętego konfliktu ze źródłami
prawdy. Wszystkie szczegóły kontraktu zostały zaakceptowane:
lokalny wykonawca bez modelu, zakres cache ograniczony do operacji,
explicit reevaluation intent. Semantyka lokalnego recovery została następnie
rozstrzygnięta decyzją użytkownika GAP-017 RECOVERY v1 — ACCEPTED,
zapisaną w sekcji F.1 kontraktu. Repository-level recovery primitive jest
wdrożony w fazie 2 bez endpointu research/recover i bez nowego recovery engine.
Pełne operator API recovery pozostaje zakresem późniejszej fazy.

## 6. Pliki i zakończenie

Utworzone dokumenty: `docs/GAP017_PREFLIGHT.md`, `docs/GAP017_EVALUATION_CONTRACT.md`.
Dowody w `.test_storage/gap017_evidence/phase1/`:
`baseline-porcelain.txt`, `baseline-short.txt`, `preflight.json`,
`final-porcelain.txt`, `final-short.txt`, `verification.json`.
Końcowe porównanie usuwa z nowej listy tylko dwa nowe dokumenty fazy 1;
dowody są ignored. Pełne wyniki zawiera verification.json.
Staging pusty. Brak add/commit/push. Brak zmian implementacji i danych.

## 7. Stan po fazie 2 — 2026-09-17

PHASE 2 = IMPLEMENTATION COMPLETE. Dodano `app/p20_core/evaluation.py`,
metody Evaluation w istniejącym ProjectRepository oraz neutralny zestaw
`tests/test_gap017_evaluation_record.py`. Envelope pozostaje w project.db pod
`evaluation.v1:<operation_id>`; nie dodano tabeli, migracji, repozytorium,
cache service ani recovery engine. Targeted: 47 passed. Minimalne istniejące
testy ProjectRepository/storage: 19 passed. Pełna regresja: NOT RUN.
## 8. Stan po fazie 3 — 2026-09-17

PHASE 3 = INTEGRATION PASS. Aktywny P20 QUALITY zapisuje końcowy,
lokalny `EvaluationRecord` po style veto i przed konsumentami downstream.
Runtime wybiera rekord według dokładnego `artifact_hash` i kolejności logicznej
kroku, a technical retry odtwarza wynik bez ponownego uruchomienia evaluatora.
Jawne `REEVALUATE` tworzy nową operację i zachowuje `reevaluation_of`.
Evaluation-specific recovery jest dostępne w istniejącej autoryzowanej
warstwie operatorskiej, poza `research/recover`.

Projekcje step/run_state/audit/chapter lineage wskazują ten sam rekord,
którego ownerem pozostaje `project.db`. Targeted GAP-017: 57 passed.
Pakiet integracyjny: 226 passed. Pełna regresja: NOT RUN zgodnie z zakresem
fazy 3. PHASE 4 = NOT STARTED. GAP-017 pozostaje NOT CLOSED.
