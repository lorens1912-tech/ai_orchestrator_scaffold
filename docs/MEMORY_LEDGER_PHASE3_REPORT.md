# Memory Ledger v1 — Phase 3 integration report

STATUS = INTEGRATION PASS

Data: 2026-09-23. Zakres obejmuje integrację producentów z aktywnymi ścieżkami
P20 oraz syntetyczne dowody runtime/API. Nie wykonano migracji danych użytkownika,
pełnej regresji repozytorium ani niezależnego audytu.

## Macierz producentów §2

| Wiersz kontraktu | Hook | Transakcja ownera | Dowód |
|---|---|---|---|
| Extraction START/ATTEMPT/FINISH PROJECT/SERIES | `canon_service.process_accepted_artifact` | `ProjectRepository.canonical_pipeline_operation` albo `SeriesRepository.canonical_pipeline_operation`, ten sam connection co trwały pipeline | `test_active_p20_canonical_pipeline_writes_one_complete_event_set`, `test_active_p20_series_pipeline_keeps_events_in_series_owner` |
| Proposal i stany impact/guard/waiting/denial/failure/stale | `save_canonical_proposal_for_review`, `prepare_research_proposal`, `commit_canonical_proposal`, obsługa wyniku w `process_accepted_artifact` | `canonical_proposal_transaction` właściwego PROJECT/SERIES | testy Phase 3 oraz pakiety canonical PROJECT/SERIES |
| Decyzja autora | `record_operator_decision` | ta sama transakcja co decision i zużycie challenge | fault na append decyzji cofa decision i `challenge.used`; poprawny retry tworzy jeden event |
| Zmiany encji | `ProjectRepository.apply_canonical_record_set`, `SeriesRepository.apply_canonical_record_set` | bezpośrednio przy fizycznym zapisie wersji pod istniejącym guardem | PROJECT/SERIES P20; fault końcowego batch eventu cofa encję i entity event |
| Batch commit | `commit_canonical_proposal` | ta sama transakcja co wersje, historia, receipt, final guard i entity events | `CANONICAL_COMMITTED` po wszystkich entity events; retry bez duplikacji |
| Research command/import | `research._save_command` | `research_transaction` PROJECT | command/import, brak tekstu źródłowego w event payload, retry i reopen |
| Research terminal result/recovery | `research.run_research`, `research.recover_operation` | `research_transaction` PROJECT po ponownym sprawdzeniu attempt token | success/failure, late-attempt fencing z GAP-015/GAP-016, operator recovery |
| F-004 intent | `CrossStoreRecoveryService._create_or_validate_intent` | PROJECT outbox transaction | crash po artefakcie pozostawia tylko INTENDED |
| F-004 confirmation | `_write_final_audit` | PROJECT final-audit transaction po weryfikacji pliku i wymaganych skutków | recovery/reopen/retry, brak CONFIRMED przy złym lub brakującym artefakcie |
| Chapter lineage | `_record_chapter_events` | ten sam PROJECT final-audit transaction co CONFIRMED | dokładne version/status/text hash/quality ref, bez duplikacji |
| SERIES volume close | `SeriesRepository.close_volume` | lokalna transakcja `series.db` obejmująca snapshot, transfery, receipt i CLOSED | alias wskazuje pierwotny event, bez drugiego transferu ani CLOSED |
| Scoped operator read | `operator_api.read_proposal`, odczyt research record | istniejące autoryzowane zasoby; read-only ledger query po scope/operation | TestClient potwierdza `memory_event_refs` i `coverage=MEMORY_PIPELINES_V1` |

## Granice transakcji i recovery

- Każdy hook otrzymuje connection istniejącej transakcji ownera. Append failure
  wycofuje lokalny zapis domenowy, receipt/audit i eventy danego zestawu.
- PROJECT i SERIES zachowują osobnych ownerów. SERIES canonical/graph nie korzysta
  z PROJECT fallbacku.
- Granice PROJECT/SERIES/plik pozostają obsługiwane przez istniejący F-004.
  Ledger nie deklaruje globalnego ACID i nie stanowi recovery engine.
- Replay terminalnego skutku wylicza wymagany event ID. Brak eventu jest legacy
  wyłącznie wtedy, gdy dokładna identity istnieje w bootstrap snapshot:
  `canonical_commit.v1:<operation_id>`, konkretna operacja w `research.v1` albo
  konkretny `series_operations.operation_id`.
- Terminalny legacy zwraca wynik z `LEGACY_BEFORE_LEDGER` bez backfillu. Pending
  legacy zapisuje wyłącznie rzeczywiste nowe przejście po ACTIVE. Nowy receipt lub
  wynik po ACTIVE bez eventu przechodzi do `MEMORY_LEDGER_NEEDS_INTERVENTION`.
- F-004 replay sprawdza INTENDED, CONFIRMED i wymagane chapter-version events;
  brak po ACTIVE utrwala istniejący status `NEEDS_INTERVENTION`.

## Naprawione błędy integracyjne

1. Entity index ma klucz `(sequence, record_type, entity_id)`. Event encji
   indeksuje nową wersję jeden raz; stara wersja/hash pozostają w payloadzie.
2. Walidacja proposal SERIES korzysta z istniejącego connection transakcji,
   eliminując zagnieżdżone otwarcie `series.db` i timeout blokady SQLite.
3. Błąd po utrwaleniu zaakceptowanego proposal nie próbuje zmienić zakończonego
   pipeline ACCEPT na FAILED; failure pozostaje stanem proposal.
4. Replay terminalnego canonical/research/F-004/SERIES close rozróżnia dokładny
   baseline legacy od nowego brakującego eventu.

## Zmienione pliki Phase 3

- `app/operator_api.py`
- `app/p20_core/canon_service.py`
- `app/p20_core/cross_store_recovery.py`
- `app/p20_core/project_repository.py`
- `app/p20_core/research.py`
- `tests/test_memory_ledger_phase3.py`
- `docs/MEMORY_LEDGER_PHASE3_REPORT.md`
- `.test_storage/memory_ledger/phase3/TEST_COMMANDS.md` — ignorowany dowód lokalny

Fundament Phase 2 (`memory_ledger.py`, testy Phase 2, zamrożony kontrakt i plan)
został zachowany. Nie zmieniono zaakceptowanych dokumentów kontraktowych.

## Testy

- Memory Ledger Phase 2+3: `16 passed`, exit 0.
- Canonical PROJECT/SERIES, research i operator: `96 passed`, exit 0.
- F-004/F-005 i GAP-017: `157 passed`, exit 0.
- GAP-015/GAP-016 po finalnej zmianie replay: `74 passed`, exit 0.
- GAP-016 i Context Builder: `90 passed`, exit 0.
- Syntax/import compileall: exit 0.

Dokładne komendy, czasy i kontrolowane reruny ACL znajdują się w
`.test_storage/memory_ledger/phase3/TEST_COMMANDS.md`. Wyników nakładających się
pakietów nie zsumowano jako liczby unikalnych testów.

## Ograniczenia do Phase 4

- pełna regresja repozytorium po finalnym kodzie;
- niezależny audit finalnego diffu i coverage;
- test rzeczywistej migracji dopiero po osobnej autoryzacji;
- fizyczna utrata zasilania i live provider pozostają niezweryfikowane.

## Git i baseline

- Branch: `codex/agentpro-stabilization-freeze`.
- HEAD bez zmian: `b8003d114174eaae5012dab642d22681042c3032`.
- Staging: pusty; commit/push nie wykonano.
- Scoped `git diff --check`: PASS, exit 0.
- Stan Phase 2 miał 2556 ścieżek; stan końcowy ma 2562. Delta Phase 3 = 6
  nowych wpisów statusu: cztery śledzone pliki po raz pierwszy zmodyfikowane w
  tej fazie oraz dwa nowe pliki raportu/testów. `project_repository.py` był już
  autoryzowaną zmianą Phase 2.
- Po odjęciu dokładnej allowlisty 13 ścieżek Phase 2+3 pozostaje 2549 wpisów,
  identycznych path/status z `phase1/status-after.txt`; baseline delta = 0.

PHASE 3 = INTEGRATION PASS
PHASE 4 = NOT STARTED
REAL DATA MIGRATION = NOT RUN
FULL REGRESSION = NOT RUN
MEMORY LEDGER V1 = NOT CLOSED
COMMIT/PUSH = NOT PERFORMED
