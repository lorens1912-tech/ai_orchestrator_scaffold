# Memory Ledger v1 — Phase 5 audit repair report

Data: 2026-09-24

## Werdykt

BLOCKERS = FIXED, PENDING INDEPENDENT VERIFICATION

FULL REGRESSION = PASS — 899 passed, 1 skipped, exit 0

PHASE 5 = READY_FOR_REAUDIT

MEMORY LEDGER V1 = NOT CLOSED

REAL DATA MIGRATION = NOT RUN

COMMIT/PUSH = NOT PERFORMED

Branch i checkpoint pozostają zgodne z zadaniem:
`codex/agentpro-stabilization-freeze` / `b8003d114174eaae5012dab642d22681042c3032`.
Staging jest pusty. Nie czytano ani nie haszowano danych realnych książek.

## P5-01 — backup i migracja pod write reservation

**Root cause.** `SchemaMigrationRunner` wykonywał backup i część preflightu przed
`BEGIN IMMEDIATE`. Kopia nie była związana z rezerwacją migratora, a helper nie
gwarantował osobnych połączeń M/R/D, pełnego deadline'u, identity check ani
manifestu.

**Zmiana.** Migrator najpierw pozyskuje `BEGIN IMMEDIATE`, ponownie sprawdza
wersję i uruchamia preflight. M pozostaje właścicielem rezerwacji; osobne
read-only R wykonuje SQLite backup do osobnego D. Połączenia backupu są zamykane
przed DDL, a rezerwacja M trwa do końcowej walidacji i COMMIT. Backup ma deadline,
kontrolę integralności, FK, schema i identity oraz manifest z zakresem, wersją
źródłową, release/HEAD, hashem i wynikami walidacji. Migracja wymaga jawnego
maintenance confirmation i odrzuca błąd lub timeout przed DDL.

**Dowód.** Ograniczona reprodukcja Windows 10 / Python 3.11.9 / SQLite 3.45.1
potwierdziła R → D pod rezerwacją M: backup trwał 0.016 s, zawierał zatwierdzony
rekord pozostający w WAL, a równoległy writer zakończył się `SQLITE_BUSY` (code
5), bez deadlocku. Testy rzeczywistej ścieżki sprawdzają maintenance gate,
deadline, manifest, WAL, writer-between-backup-and-DDL, rollback i idempotentny
drugi migrator.

**Status:** FIXED, PENDING INDEPENDENT VERIFICATION.

## P5-02 — zamknięty codec per event type

**Root cause.** Wspólna allowlista payloadu nie wymuszała semantyki konkretnego
`event_type`; poprawny hash mógł osłaniać pusty lub obcy payload i niepoprawny
slot.

**Zmiana.** Codec ma zamknięte warianty dla wszystkich 17 typów zdarzeń:
wymagane i dozwolone pola, typy i nullability, Domain IDs, confined locators,
operation namespace oraz dokładny `event_slot` związany z proposal/version,
attempt, entity lub innym identyfikatorem zdarzenia. Te same reguły są
wykonywane przy budowie, append i odczycie. Producenci zapisują stabilne
`reason_code` zamiast swobodnych komunikatów błędu.

**Dowód.** Parametryzowany test każdego wariantu obejmuje poprawny rekord, brak
pola, obce pole, zły typ/null, zły namespace, błędny slot i sprzeczny binding.
Błędny codec nie jest zamieniany na pusty sukces ani pomijany.

**Status:** FIXED, PENDING INDEPENDENT VERIFICATION.

## P5-03 — exact schema i control record

**Root cause.** Walidatory target-version dopuszczały brak control record i
sprawdzały tylko fragment definicji schema. Istniejący uszkodzony store mógł być
po cichu uzupełniony przez inicjalizację `IF NOT EXISTS`.

**Zmiana.** Walidacja porównuje dokładne tabele, kolumny, PK/FK, indeksy i SQL
triggerów, identity schema version oraz zamknięty dokument
`memory_ledger_control.v1`. `SCHEMA_READY` pozostaje stanem migracyjnym bez praw
producentów. `ACTIVE` wymaga prawidłowego activation eventu i zapisanego baseline
manifestu, sprawdzanych ponownie przy użyciu Ledgera. Istniejący target jest
walidowany przed jakąkolwiek próbą tworzenia brakujących obiektów; atomowa
inicjalizacja naprawdę pustej bazy pozostaje osobną ścieżką.

**Dowód.** Macierz negatywna usuwa lub zmienia control row, tabelę, kolumnę,
indeks, trigger i jego działanie, wersję identity oraz binding ACTIVE. Każdy
wariant kończy się jawnym błędem bez naprawy danych.

**Status:** FIXED, PENDING INDEPENDENT VERIFICATION.

## P5-04 — stabilne locatory i pełny bootstrap preflight

**Root cause.** Baseline identyfikował rekordy przez ordinal `rowid`, a metadata
wybierał szerokimi prefixami bez kompletnego dowodu bindingów i stanu operacji.

**Zmiana.** Locator ma postać `table:<table>:key:<canonical-json>` i wynika z
pełnego rzeczywistego PK lub klucza złożonego. Odczyt jest sortowany po PK i nie
zależy od kolejności INSERT. Registry metadata jest zamknięte. Preflight obejmuje
maintenance, integrity/FK, identity i membership, wspierane reprezentacje,
terminalne i pending operations, canonical pipeline/proposal/commit, research,
cross-store operation/audit, wymagane referencje oraz odmowę sekretów i
niedowodliwych bindingów. `series_operations` zachowuje aliasy i surowy
`result_payload_json`. Błąd nie zapisuje częściowego baseline ani ACTIVE; retry
waliduje utrwalony manifest.

**Dowód.** Testy sprawdzają identyczne locatory dla różnych kolejności INSERT,
klucze złożone, reopen, niepełne research/canonical/cross-store bindingi oraz
niespójny series membership.

**Status:** FIXED, PENDING INDEPENDENT VERIFICATION.

## Dodatkowe regresje wykryte podczas pełnego gate'u

1. Legacy wejścia `canon/rebuild` używały fizycznego `book_id` jako domenowego
   `project_id`. `ensure_book_dirs` zachowuje fizyczną ścieżkę, a przy braku
   jawnej tożsamości wyprowadza kanoniczne `PROJ-*` i `BOOK-*` dla repository.
2. `StorageResolver` odczytywał procesowy storage root kilkakrotnie, a niezależne
   `Path.resolve()` rodzica i tworzonego równolegle dziecka były niestabilne na
   Windows. Resolver robi jeden snapshot root, wyprowadza z niego wszystkie
   ścieżki, a containment sprawdza atomowo przez znormalizowane `commonpath`.
   Ochrona przed traversal pozostała aktywna.

Drugi problem został wykryty przez pełną regresję, odtworzony w jej porządku,
naprawiony i sprawdzony testami resolvera oraz 10/10 powtórzeniami współbieżnej
izolacji. Dwa wcześniejsze failing logi zostały zachowane.

## Testy i dowody

- reprodukcja M/R/D: PASS; Windows 10, Python 3.11.9, SQLite 3.45.1,
  `SQLITE_BUSY` code 5 dla drugiego writera;
- końcowy pakiet Memory Ledger i bezpośrednich reprodukcji: 117 passed, exit 0;
- pakiet dotkniętych zależności: 477 passed, exit 0;
- resolver/containment/traversal: 37 passed, exit 0;
- współbieżna izolacja PROJECT: 10/10 passed;
- pełna regresja finalnego kodu:
  `python -m pytest -q --disable-warnings -rs --basetemp=<isolated>`;
  **899 passed, 1 skipped**, exit **0**, 1361.79 s;
- jedyny skip: historyczny brak aktywnego legacy
  `PATCH /canon/{book_id}` w `tests/test_110_canon_api_roundtrip.py:47`.

Dowody znajdują się w `.test_storage/memory_ledger/phase5/repair/`, w tym
reprodukcja M/R/D, logi celowane, zachowane failing full runs i końcowy
`FULL_REGRESSION_FINAL_V3.log` z komendą oraz exit code.

## Dokładna allowlista przyszłego commita

1. `docs/AGENTPRO_GROK_KREION_NARRATIVE_COVERAGE.md`
2. `docs/MEMORY_LEDGER_CONTRACT_v1.md`
3. `docs/MEMORY_LEDGER_MIGRATION_PLAN_v1.md`
4. `app/p20_core/memory_ledger.py`
5. `app/p20_core/project_repository.py`
6. `tests/test_memory_ledger_phase2.py`
7. `tests/test_gap014_adaptive_style.py`
8. `tests/test_p20_context_builder.py`
9. `tests/test_p20_project_graph.py`
10. `docs/MEMORY_LEDGER_PHASE2_REPORT.md`
11. `app/operator_api.py`
12. `app/p20_core/canon_service.py`
13. `app/p20_core/cross_store_recovery.py`
14. `app/p20_core/research.py`
15. `tests/test_memory_ledger_phase3.py`
16. `docs/MEMORY_LEDGER_PHASE3_REPORT.md`
17. `tests/test_memory_ledger_phase4.py`
18. `docs/MEMORY_LEDGER_PHASE4_REPORT.md`
19. `docs/MEMORY_LEDGER_FINAL_AUDIT.md`
20. `tests/test_memory_ledger_phase5_repair.py`
21. `tests/test_p20_database_foundation.py`
22. `tests/test_p20_domain_mutation_guard.py`
23. `tests/test_p20_domain_records.py`
24. `tests/test_p20_impact_analysis.py`
25. `tests/test_p20_memory_extraction_integrity.py`
26. `tests/test_p20_project_repository.py`
27. `tests/test_p20_scope_contract.py`
28. `tests/test_p20_series_memory.py`
29. `tests/test_p20_series_repository.py`
30. `tests/test_p20_structured_memory.py`
31. `docs/MEMORY_LEDGER_AUDIT_REPAIR_REPORT.md`

Pliki w `.test_storage/memory_ledger/phase5/repair/` są ignorowanymi dowodami i
nie należą do przyszłego commita. Allowlista nie jest zgodą na commit.

## Git, baseline i ograniczenia dowodów

- stan przed raportem: 2576 wpisów = 2546 baseline + 30 autoryzowanych ścieżek;
- stan po raporcie: 2577 wpisów = 2546 baseline + 31 autoryzowanych ścieżek;
- dokładne porównanie status/path baseline: brak utraconych i dodanych wpisów,
  delta 0;
- scoped `git diff --check`: PASS;
- staging: EMPTY;
- nie wykonano reset/restore/clean/stash/checkout/pull/add/commit/push.

Nie wykonano migracji rzeczywistych danych, testu fizycznej utraty zasilania ani
żywego dostawcy modelu. Własna weryfikacja autora zmian nie jest niezależnym
audytem. Wymagana następna bramka to read-only reaudyt finalnego diffu i dowodów.

## Kryterium jakości literackiej

LITERARY QUALITY EVALUATION = NOT APPLICABLE. Zakres nie zmienia Skill ani
procedury pisania, redakcji lub krytyki i nie wykonuje publikacji Skill.
