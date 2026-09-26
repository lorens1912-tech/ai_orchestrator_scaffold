# Memory Ledger v1 — Phase 2 implementation report

STATUS = IMPLEMENTATION COMPLETE — FUNDAMENT

Data: 2026-09-23. Implementacja dotyczy wyłącznie syntetycznych baz testowych.
Nie wykonano migracji danych użytkownika ani uruchomienia normalnej aplikacji.

## Zaimplementowany fundament

- `app/p20_core/memory_ledger.py`: zamknięty codec `MemoryEventRecord`,
  kanoniczna serializacja v1, deterministic `memory_event_id`, comparison payload,
  `content_hash`, walidacja payloadów/refs oraz kontrola indeksu encji.
- `app/p20_core/project_repository.py`: PROJECT 5→6 i SERIES 3→4, tabele
  `memory_events` / `memory_event_entities`, indeksy, trigger append-only,
  SCHEMA_READY/ACTIVE control, append/get/list/keyset cursor i local reference
  validation w istniejących repozytoriach.
- `SchemaMigrationRunner`: reread wersji pod `BEGIN IMMEDIATE` i walidacja
  osiągniętego targetu dla migracji Ledger. Verified SQLite backup powstaje przez
  osobne read-only connection przed DDL; po nim wersja jest ponownie odczytywana
  pod blokadą DDL, aby uniknąć blokady WAL na Windows.
- Pusta nowa PROJECT/SERIES DB otrzymuje atomowy `LEDGER_ACTIVATED` z
  `origin=EMPTY_STORE`. Migracja starszej DB kończy tylko `SCHEMA_READY`.
- Bootstrap legacy zapisuje observed baseline i aktywację w jednej transakcji.
  SERIES zachowuje każdy `series_operations.operation_id`, z surowym
  `result_payload_json`; nie emituje historycznych `SERIES_VOLUME_CLOSED` ani
  `CANONICAL_COMMITTED`.

## Granice Phase 2

Nie podłączono producentów CanonService, research, F-004 ani chapter lineage.
Nie dodano endpointu ani zmian operator API. `MEMORY_PIPELINES_V1` oznacza
schema/control record; nie deklaruje czynnej coverage aktywnych ścieżek P20.

## Dowody syntetyczne

`tests/test_memory_ledger_phase2.py` obejmuje codec/hash/Unicode, idempotencję,
content conflict, REPLACE z recursive triggers OFF/ON, indeks bez entity ref,
rollback owner transaction, concurrent append, SERIES membership i forged
bootstrap, P5/S3 migration, backup refusal, reopen/bootstrap retry oraz receipts
aliasów `series_operations`.

Wykonane pakiety i szczegóły znajdują się w
`.test_storage/memory_ledger/phase2/TEST_COMMANDS.md`.

PHASE 3 = NOT STARTED
PRODUCER INTEGRATION = NOT STARTED
REAL DATA MIGRATION = NOT RUN
FULL REGRESSION = NOT RUN
MEMORY LEDGER V1 = NOT CLOSED
COMMIT/PUSH = NOT PERFORMED
