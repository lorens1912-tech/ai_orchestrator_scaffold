# Memory Ledger — plan bezpiecznej migracji v1

STATUS = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
PHASE 1 = COMPLETE
CONTRACT = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
MIGRATION PLAN = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
IMPLEMENTATION = NOT STARTED
MIGRATION EXECUTION = NOT RUN
TESTS = NOT RUN
PHASE 2 = NOT STARTED

Data: 2026-09-23. HEAD: `b8003d114174eaae5012dab642d22681042c3032`.
Rewizja: 2.
Kontrakt: [MEMORY_LEDGER_CONTRACT_v1.md](MEMORY_LEDGER_CONTRACT_v1.md).
To plan, nie wykonany backup, migracja, test ani skrypt uruchomieniowy.
Nie sprawdzano zawartości żadnej realnej bazy ani książki.

## 1. Fakty odczytane z kodu

Owner: `app/p20_core/project_repository.py`.

| Stan obecny i symbol | Proponowana zmiana |
|---|---|
| PROJECT_DB_SCHEMA_VERSION=5; PROJECT_DB_MIGRATIONS kończy `_apply_project_schema_v4_to_v5` | Nowa migracja 5→6: additive ledger tables/constraints/control marker |
| SERIES_DB_SCHEMA_VERSION=3; SERIES_DB_MIGRATIONS kończy `_apply_series_schema_v2_to_v3` | Nowa migracja 3→4: ten sam kontrakt, SERIES owner |
| SYSTEM_DB_SCHEMA_VERSION=1 | Bez zmian, brak globalnego ledgera |
| `SchemaMigrationRunner.migrate`: inspect przed BEGIN, apply/validate/version/commit, rollback przy wyjątku | BEGIN IMMEDIATE i ponowny odczyt wersji pod lockiem; walidacja również przy osiągniętym target |
| `SchemaMigration.backup`: opcjonalny hook wewnątrz migracji | Kontrolowany backup przed DDL; sam hook nie dowodzi backupu |
| `ProjectRepository.migrate_schema`, `SeriesRepository.migrate_schema`: osobne sqlite3 connections | Jawne foreign_keys i bounded busy_timeout także w migracji |
| `project_identity`, `series_identity` i tabela `schema_version` | Oba markery wersji aktualizować atomowo, zachować domain IDs |
| Repozytoria `connect()` obsługują WAL i timeout | Zachować ownerów; history read nie inicjalizuje/migruje DB |

To są **przyszłe zmiany**, nie opis istniejącej poprawnej serializacji dwóch
migratorów. Nie zmieniać historycznych apply migracji przy dodawaniu nowego kroku.
Źródła: ADR-0001; NSE §§4–7,24–25,39–40; MASTER_CANON §§75–84;
ARCHITEKTURA §§101–114,134–137; Canonical Change §§7–12 i F4/F5.
Numery ustalono z kodu, bez otwierania rzeczywistych DB.

## 2. Wybrana strategia i kolejność

**Jawna migracja offline w maintenance. Schema i bootstrap to oddzielne lokalne
transakcje. Nie wykonujemy backfill dawnych zdarzeń.**

Nie uruchamiać migracji z GET, ContextBuildera ani initialize starej DB.
Przyszłe polecenie administracyjne korzysta z istniejących repositories i
SchemaMigrationRunner. W tej fazie nie powstaje skrypt. Normalne P20 startuje
dopiero, gdy wszystkie magazyny potrzebne wybranemu projektowi są na schema
docelowym i ACTIVE. Niezależny projekt nie wymaga nieużywanej serii.

1. Jawne maintenance: zatrzymać nowe zapisy, dokończyć transakcje, zatrzymać
   backend/workery korzystające ze wskazanych DB. Book/run lock nie zastępuje
   migration exclusivity. Brak dowodu wyłączności procesów → odmowa; nie kasować locków.
2. Wybrać jawny zestaw project/book/series przez istniejący registry, bez
   rekursywnego odkrywania baz książek. Pierwsze wykonanie i dowody wyłącznie
   na syntetycznych fixture'ach. Migracja realnych danych wymaga późniejszej
   osobnej autoryzacji; nie jest autoryzowana tą fazą.
3. Zweryfikować wejściowe P5/S3, identity, membership, integrity_check i
   foreign_key_check. Starsze wersje wymagają osobnego kontrolowanego upgrade
   do punktu wejścia, bez automatycznego łączenia tego zadania z dawnymi migracjami.
4. Spisać terminalne i pending operacje objętych producentów. Pending z pełnym
   bindingiem/payloadem może przejść jako LEGACY_PENDING. Niedowodliwy stan
   kierować do istniejącego operator recovery/interwencji; migracja nie go naprawia.
   Jeżeli uniemożliwia spójny snapshot, odmówić aktywacji.
5. Backup i schema: najpierw używane SERIES w porządku series_id, potem PROJECT
   w porządku project_id. Jedna DB naraz, bez jednoczesnych locków P i S.
6. Bootstrap SERIES, potem PROJECT; sprawdzić refs/membership. Po częściowym
   powodzeniu maintenance trwa, nie uruchamiać runtime między krokami.
7. Włączyć wyłącznie nowe wydanie z producer hooks. Stare procesy piszące bez
   eventów są niedopuszczalne. Sam schema marker nie chroni przed starym writerem,
   dlatego nie zastępuje wyłączności wdrożenia.

Nie ma globalnego rollbacku P/S/plików. Po przerwaniu pomiędzy DB ponowny
maintenance waliduje wykonane kroki i kończy brakujące. Migracja schema nie
udaje domenowej operacji F4. F4 nadal odzyskuje rzeczywiste cross-store writes.

## 3. Backup i lokalna migracja schematu

Na czystym connection ustawić foreign_keys=ON, busy_timeout zgodny z obecnym
`_DOMAIN_DB_BUSY_TIMEOUT_MS`; BEGIN IMMEDIATE, ponownie odczytać schema i identity.
Jeżeli drugi migrator już zakończył: zwalidować dokładny schema/control marker,
nie migrować ponownie. Timeout = MAINTENANCE_BUSY, bez wymuszonego takeover.

Pod write reservation, przed DDL, wykonać SQLite Online Backup przez **osobne
read-only connection** do źródła i nową docelową kopię; nie przez backup tego
samego connection z aktywną transakcją zapisu. Writerzy są zablokowani,
maintenance gwarantuje brak aktywnego backendu. Nie kopiować samego `.db`
pomijając WAL. Kopia ma unikalną ścieżkę, nigdy nadpisywaną; walidacja przez
osobne otwarcie, integrity check, identity i schema. Niepełna kopia po awarii
pozostaje oznaczona niepoprawną i nie może służyć do restore.

Manifest backupu: scope, source schema, release/HEAD, hash kopii i wynik
walidacji. Artefakty nie są modyfikowane przez migrację; przyszły restore musi
mieć spójny backup/manifest projektu obejmujący też artefakty, wykonany przez
istniejącą procedurę backup w maintenance. Brak praw/miejsca/potwierdzonej kopii
→ rollback i odmowa DDL. W tej fazie nie tworzono żadnego backupu danych.

W tym samym BEGIN IMMEDIATE, po backup:

1. Sprawdzić brak obcych tabel/indeksów/triggerów o nowych nazwach. Nie maskować
   kolizji przez samo IF NOT EXISTS.
2. Utworzyć memory_events, memory_event_entities, indeksy i immutable/scope
   triggers z kontraktu. Walidator sprawdza kolumny, constraints, FK, indeksy
   i treść triggerów, nie tylko numer schema.
3. Zapisać `memory_ledger_control.v1` w existing metadata jako SCHEMA_READY,
   coverage MEMORY_PIPELINES_V1, bootstrap_operation_id=null. To nie ACTIVE.
4. Zachować wszystkie stare wiersze i zaktualizować schema_version oraz
   identity.schema_version do P6/S4; walidacja i COMMIT.

Nie używać sposobu wykonywania SQL powodującego implicit commit w środku
operacji (np. niekontrolowanego executescript). Crash przed COMMIT zostawia
P5/S3; po COMMIT P6/S4+SCHEMA_READY. Częściowy schema/marker = INTEGRITY_ERROR,
nie „naprawa na oko”. Wynik target already reached wymaga walidacji.

Nowa pusta DB tworzona nowym kodem otrzymuje docelowy schema oraz pusty baseline
i LEDGER_ACTIVATED(origin=EMPTY_STORE) atomowo ze swoim identity. Również SERIES
initialize musi mieć pełną transakcję schema/identity/activation. Format jest
ten sam co migrowanej DB, provenance inne.

## 4. Bootstrap — obserwowany stan, bez wymyślania przeszłości

Oddzielny BEGIN IMMEDIATE tylko na SCHEMA_READY, bez domain writes. Wszystkie
baseline events, activation i marker ACTIVE commitowane razem. V1 nie ma
chunkowego częściowo aktywnego bootstrapu; zbyt duży zbiór/brak przestrzeni
oznacza odmowę przed rozpoczęciem, nie samowolną zmianę algorytmu.

### Registry snapshotu

| Źródło | Sposób zachowania |
|---|---|
| PROJECT project_structured_memory_records, legacy project_fact_records i project_character_states, edges | BASELINE_OBJECT per rzeczywisty klucz tabeli. Snapshot_text zachowuje payload i ochronę. Nie deduplikować podobnych danych z różnych dawnych tabel. |
| SERIES series_state_records, edges, volume_closing_snapshots, series_memberships | Per klucz/state_kind. Membership jest obserwacją stanu, nie dawnym author approval. |
| SERIES series_operations | Per `operation_id`, dokładny row wszystkich pięciu kolumn: `operation_id`, `semantic_hash`, `result_type`, `result_id`, `result_payload_json`. Zachować każdy receipt, także alias z tym samym semantic_hash/result_id; nie deduplikować po znaczeniu ani po wyniku. |
| Canonical metadata P/S: canonical_versions.v1:, canonical_proposal.v1:, canonical_pipeline.v1:, canonical_commit.v1: | Per metadata key: dokładny snapshot value lub immutable ref. Nie zamieniać zawartych attempts/versions w domniemane historyczne MemoryEvent. |
| PROJECT research.v1, cross_store_operation.v1:, cross_store_audit.v1: | Per key, zachowany payload; oddzielny manifest terminal/legacy-pending identities. Bez system credentials. |
| Referencje rozdziałów i źródeł | Locator/hash z istniejących dowodów. Brak globalnego skanu plików; brak dowodu dostępności = LEGACY_UNVERIFIABLE. |

ContextPackages, EvaluationRecord, model telemetry i style tables nie są
kopiowane hurtowo. Pozostają własnymi zasobami powiązanymi referencjami.
Nie deklarować przez to pełnej historii aplikacji. Snapshot_text jest celową
kopią tylko koniecznego zmiennego stanu zakresu; zapobiega utracie wersji
początkowej po następnej aktualizacji. Napotkany sekret lub nierozpoznana
struktura w objętym zbiorze → odmowa do wyjaśnienia, nie cicha redakcja/hash zmiana.

### Algorytm

1. Inwentaryzacja dokładnych tabel/prefiksów powyżej, bez interpretacji jako
   dawnych decyzji. Sort category, tabela/key, pełny klucz w serializacji v1.
2. Stały bootstrap_operation_id: `bootstrap-v1:<scope_type>:<scope_id>`.
   Jeden bootstrap per store v1; ponowny z inną treścią jest konfliktem.
3. BASELINE_OBJECT per obiekt: timestamp/created_at = teraz,
   actor SYSTEM/MEMORY_LEDGER_BOOTSTRAP_V1,
   history_completeness=UNKNOWN_BEFORE_BOUNDARY. Daty/aktorzy dawnego payloadu
   pozostają danymi źródła, nie są uzupełniane. Brak flag nie staje się false.
4. Manifest_hash = SHA256 JSON v1 posortowanej listy
   `{category,locator,bytes_hash}`. bytes_hash to SHA256 UTF-8 dokładnego
   istniejącego payload string. Dla rows bez pojedynczego payload stosować
   `memory-baseline-row.v1`: kanoniczny JSON mapy nazw kolumn na ich wartości
   (string/int/null), bez zmiany starych hashy. Dla `series_operations` format
   `memory-baseline-row.v1` zawiera dokładnie pięć nazwanych kolumn z registry;
   `result_payload_json` jest zachowany jako surowy string, bez parse, rewrite
   ani kanonizacji zagnieżdżonego JSON. Nieznany BLOB/float poza istniejącym
   payload_text → odmowa zamiast zgadywania serializacji.
5. LEDGER_ACTIVATED zawiera count/manifest/coverage i ref do zbioru bootstrap
   identyfikowanego operation ID oraz manifest hash. Nie tworzyć olbrzymiej
   tablicy parent refs wszystkich eventów. Marker ACTIVE wskazuje ten event.
   Walidacja count/hash/scope/indeksów oraz zachowania starych rows, COMMIT.

Awaria przed COMMIT cofa cały baseline i marker. Po COMMIT retry waliduje
**zapisany** manifest/eventy, nie porównuje zmienionego już current state z
początkowym baseline ani nie generuje nowych czasów. Dwa bootstraps serializują
się po DB locku; drugi po reread ACTIVE tylko waliduje wynik.

SERIES bootstrap używa wyłącznie wewnętrznej ścieżki istniejącego
`SeriesRepository` opisanej w kontrakcie: namespace `LEDGER_BOOTSTRAP`, SYSTEM
actor i zweryfikowana tożsamość tego samego series.db. Dzięki temu pusty magazyn
SERIES bez członków może atomowo utworzyć schema/identity/activation z
origin=EMPTY_STORE, a legacy store może zapisać STORE_BASELINE z null
project_id/book_id. To nie jest publiczny bypass membership: normalne
read/list/append/resolve nadal wymagają SeriesAccessContext i membership;
bootstrap nie daje canonical write, correction ani zwykłego odczytu historii.

Aktualna pamięć nadal należy do istniejących tabel i CanonService. Od ACTIVE
nowe objęte operacje atomowo zapisują domenę i event. History read niczego
nie odtwarza do domeny. Pełny rebuild current state/as-of to późniejszy
kontrakt; ledger v1 nie jest kompletnym backupem całego projektu.

## 5. Stare operacje i zgodność hashy

Nie zmieniać IDs, tekstu/artefaktów, ContextPackage preimages, proposal_hash,
approval/basis/impact hash, G16/G17 hashes, F4 input_hash ani SERIES semantic_identity.
Porównać przed/po na syntetycznych fixture'ach. Nowe event hashes mają osobny
namespace; nowe schema/ledger IDs nie wchodzą do dawnych hash preimages.

Terminalny dawny receipt bez eventu pozostaje poprawnym historycznym receipt.
Retry zwraca go z LEGACY_BEFORE_LEDGER, bez backfill CANONICAL_COMMITTED.
Legacy rozpoznaje się po bootstrap snapshot/manifest **konkretnych identities**,
w tym `series_operations.operation_id` i jego zapisanych pięciu kolumnach; sam
brak eventu nigdy nie wystarcza. Bootstrap nie emituje fikcyjnego dawnego
`SERIES_VOLUME_CLOSED` ani `CANONICAL_COMMITTED` dla takiego receipt.

Znany pending sprzed cutover może tworzyć eventy tylko dla rzeczywistych przejść
po ACTIVE. Nie odtwarzać brakującego START jako dawnego zdarzenia; parent/source
wskazuje LEGACY_PENDING snapshot. Wznowieniem kieruje istniejący recovery owner.
Stary COMMITTED F4 nie dostaje nowego confirmation od samego odczytu; zakończenie
starego pending może dopisać nowe confirmation związane z legacy intent.

Nowy receipt po ACTIVE bez wymaganych eventów = intervention, nie legacy.
Producer nie deklaruje sam wyjątku legacy. Uszkodzony manifest, nieznany schema,
zły scope i konflikt key blokują aktywację/replay. STALE nie wraca automatycznie
do commitu, a stara zgoda nie przechodzi na nowe podstawy.

## 6. Rollback, restore i warunki odmowy

- Przed COMMIT schema/bootstrap: lokalny rollback; inne DB bez automatycznego cofania.
- Po schema COMMIT, przed ACTIVE i przed nowymi skutkami: jawny restore
  zweryfikowanego backupu w maintenance możliwy po wykazaniu braku nowych danych.
  Nie wprowadzać down migration kasującej event tables.
- Po ACTIVE i nowych zdarzeniach: zakaz prostego restore starego backupu.
  Najpierw backup obecnych DB/artefaktów i manifest post-cutover operations.
  Domyślnie forward repair. Restore tracący skutki wymaga jawnej decyzji
  właściciela danych i uzgodnienia wszystkich magazynów; nie ma automatycznego
  merge/replay, którego v1 jeszcze nie gwarantuje.
- Restore przy zamkniętych connections, zgodnie z procedurą SQLite/WAL:
  nie pozostawiać starego WAL/SHM przy podmienionej bazie. Potem identity,
  integrity i references check przed uruchomieniem.
- Odmowa: brak backupu/praw/miejsca/wyłączności, unknown schema, kolizja nazw,
  niespójna identity/membership, FK/integrity failure, niedowodliwy bootstrap,
  uszkodzone frozen hashes albo niespójny zestaw P/S. Bez napraw realnych danych
  „przy okazji”.

## 7. Kryterium → planowany test → oczekiwany wynik

To opis przyszłych testów, **nie istniejące node IDs ani wyniki wykonania**.
Neutralne fixtures, izolowany storage; API proof TestClient(app.main.app),
rzeczywiste repositories/SQLite, SDK zastępowane dopiero na granicy.

| Kryterium | Test | Wynik oczekiwany |
|---|---|---|
| Append-only | UPDATE/DELETE/REPLACE przy `recursive_triggers=OFF` i `ON`; kolizja kolejno sequence, memory_event_id i event_key_json; event bez entity entries; PK indeksu encji | Każda kolizja i REPLACE odmawia przez trigger; stare record/hash/index pozostają identyczne, correction tylko nowy event, bez zmiany domeny |
| Integralność | Corrupt JSON/hash/index/scope, vectors Unicode/null/int | Typed error; bez pomijania wierszy i użycia jako authority |
| Idempotencja | Same key/payload; changed payload; kilka slotów | Te same ID/seq/czasy; konflikt zmiany; osobne eventy per slot |
| Współbieżność | Dwa connections, ten sam i różne keys | Jeden event per key, lokalny porządek, bounded busy bez success fallback |
| Izolacja | Dwa projekty/dwie serie, złe membership/connection | Brak leakage i PROJECT fallbacku, odmowa przed dostępem |
| Authority | Candidate ACCEPT, waiting, REJECT, STALE, sfałszowany actor | Historia bez promocji; brak fałszywej zgody |
| Atomic batch | Fault po mutacji, N-tym append, przed receipt | Domena, historia, receipt i wszystkie eventy rollback razem |
| Decision | Append failure po used challenge | Cofnięcie decyzji i zużycia; poprawny retry nie traci zgody |
| P20 coverage | Mixed PROJECT/SERIES, verifier fail, research import/promote | Każda trwała gałąź §2 kontraktu ma event i właściwe refs |
| F4 granice | Fault po pliku, po SERIES przed P checkpoint, AUDIT_PENDING | INTENDED nie udaje CONFIRMED; recovery bez duplikacji |
| BOOK_LOCKED | Lock przed startem, crash po identity | Brak fikcyjnego startu, zachowane IDs, poprawny pierwszy start |
| Late result | Jawne recovery, wynik starego tokena | Odgrodzony wynik nie finalizuje domeny ani eventu |
| Reopen | Crash po COMMIT przed HTTP | Ten sam receipt/event, bez modeli i nowych decyzji |
| Paginacja | Nowe eventy między stronami, zmiana filtra/scope | Stały high_watermark, brak przeskoków i leakage |
| Legacy schema | P5/S3 z wersjami/approvals i null protection | P6/S4 bez zmiany dawnych danych/hash; UNKNOWN nadal UNKNOWN |
| Bootstrap | Brak historycznego autora/czasu; dwie legacy tabele; pusty SERIES bez members; przerwanie/reopen/retry | Observed-now, bez wymyślonych zdarzeń i bez cichej deduplikacji; dokładnie jedna activation, normalny unauth read/append i forged bootstrap normal path odmawiają |
| Migration crash | Każdy krok DDL/bootstrap + reopen/retry | Old schema albo pełny SCHEMA_READY, nigdy częściowe ACTIVE |
| Dwa migratory | Równoczesny schema upgrade i bootstrap | Jeden wynik, reread pod lockiem, walidacja drugiego lub busy |
| Legacy operations | close_volume i aliasy przed cutover; bootstrap/reopen/retry każdego legacy operation_id | Każdy legacy ID zwraca pierwotny raw result bez retransfer, fake event ani intervention; tylko nowe realne skutki, nowe braki intervention |
| Backup/restore | WAL backup, zła kopia, nowe eventy przed restore | Spójna kopia lub odmowa; brak cichej utraty danych |
| Frozen hashes | Golden CCC/G16/G17/F4/F5/context fixtures | Stare IDs/bytes/hashes identyczne; nowe vectors osobno |
| Read-only | History read starej DB i ACTIVE | Brak migrate/recovery/append/canon write |

Przyszła kolejność: targeted → integracja canonical P/S, research, F4/F5,
G16/G17, context/style authority → pełna regresja finalnego kodu → końcowy audyt.
Historyczne **838 passed, 1 skipped** jest dowodem GAP-017, nie Ledgera.
TESTS = NOT RUN. Symulacja crash/SDK nie dowodzi fizycznego power loss ani live provider.

## 8. Dalsze fazy — nie uruchomiono

1. **Review i akceptacja: COMPLETE / FROZEN.** Coverage, record/key/hash v1,
   P6/S4, bootstrap, maintenance/restore i read integration są ustalone;
   nie pozostawiono alternatyw implementatorowi.
2. **Mechaniczna implementacja po akceptacji:** codec/validators, tabele/triggery,
   repository methods, keyset read, runner locking i bootstrap. Bez zmian
   semantyki historycznych kontraktów.
3. **Integracja P20/CanonService:** hooki wszystkich gałęzi §2, principal/ref
   binding, F4 audit, legacy-pending. Wymaga weryfikacji granic transakcji;
   konflikt z authority/recovery nie upoważnia do samowolnej zmiany ADR.
4. **Testy i dowody:** macierz §7, integracja i pełna finalna regresja.
5. **Końcowy audyt:** coverage, failure boundaries, migracja i zachowane hashe.
   Osobny reviewer tylko jeśli dostępny i autoryzowany; własny review nie jest
   niezależnym audytem. Commit/push wyłącznie w późniejszym autoryzowanym zadaniu.

Nie przydzielono modeli według sztywnego cyklu. Pełne as-of/retcon/ReaderKnowledge/
derived rebuild/workflow/scheduler pozostają odłożone. Nie nadano nowego GAP.

## 9. Dowody i kontrola fazy 1

`.test_storage/memory_ledger/phase1/`: preflight, snapshot hashy jawnych źródeł,
symbole kodu, scoped diff/check i końcowa kontrola status/path.
Preflight: 2546 baseline + zachowany raport coverage, delta 0, staging EMPTY.
Nowe ścieżki dokumentów i dowodów nie istniały przed zadaniem.

Samokontrola obejmuje zgodność numerów P5→6/S3→4, hash/slots, ownerów,
atomicity, legacy/new, manifestu, braku globalnego ACID i oddzielenia historii
od authority. Brak wykazanego konfliktu z kanonem. Plan akceptacji nie
autoryzuje realnej migracji ani implementacji.

## Historia rewizji

- Rewizja 1: propozycja bezpiecznej migracji Phase 1.
- Rewizja 2: review domknął ochronę REPLACE, baseline `series_operations` i
  ograniczony bootstrap SERIES bez osłabienia normalnej autoryzacji.

PHASE 1 = COMPLETE
CONTRACT = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
MIGRATION PLAN = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
IMPLEMENTATION = NOT STARTED
MIGRATION EXECUTION = NOT RUN
TESTS = NOT RUN
PHASE 2 = NOT STARTED
