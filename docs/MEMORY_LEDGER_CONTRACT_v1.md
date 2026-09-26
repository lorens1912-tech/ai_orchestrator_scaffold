# Memory Ledger — kontrakt wykonawczy v1

STATUS = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
PHASE 1 = COMPLETE
CONTRACT = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
MIGRATION PLAN = ACCEPTED / FROZEN FOR MEMORY LEDGER V1
IMPLEMENTATION = NOT STARTED
MIGRATION EXECUTION = NOT RUN
TESTS = NOT RUN
PHASE 2 = NOT STARTED

Data: 2026-09-23. Baza: `b8003d114174eaae5012dab642d22681042c3032`,
branch `codex/agentpro-stabilization-freeze`.
Rewizja: 2. Nowe nazwy, schema, hashe i interfejsy poniżej są zaakceptowanym
kontraktem implementacyjnym, nie opisem istniejącej implementacji ani zmianą
zaakceptowanego ADR.
Plan towarzyszący: [MEMORY_LEDGER_MIGRATION_PLAN_v1.md](MEMORY_LEDGER_MIGRATION_PLAN_v1.md).
Nie nadano numeru GAP-018 i nie otwarto ponownie zamkniętych GAP-ów.

## 1. Podstawa i rozstrzygnięcie zakresu

Źródła: pełny `ADR-00XX_NARRATIVE_STATE_ENGINE.md` (ACCEPTED), zwłaszcza
§§4–8, 10–17, 24–29, 37–40; MASTER_CANON v2 §§14,18–20,71–85;
ARCHITEKTURA v1.2 §§22,27–28,44A,97–114,134–137; `ADR-0001.md`;
zaakceptowany Canonical Change v1.2, ADR-0002 SERIES, F-004 i F-005.
Wykorzystano ustalenia `AGENTPRO_GROK_KREION_NARRATIVE_COVERAGE.md`, bez
powtarzania całego audytu inspiracji. Nie znaleziono konfliktu z tymi źródłami.

**V1 jest append-only historią określonych procesów pamięciowych od jawnego
punktu startowego. Nie jest pełną bezstratną historią całej aplikacji.**
Nie rekonstruuje dawnego przebiegu projektu ani całego current state przez replay.
Istniejący stan pozostaje autorytatywną projekcją obsługiwaną przez te same
repozytoria i CanonService. Ledger stanowi atomowo związany zapis jego zmian.

MemoryEventRecord jest obserwacją trwałego faktu procesowego. Nie zastępuje:
fabularnego EventRecord, KnowledgeEvent, EvaluationRecord, ModelInvocationAudit,
CanonicalChangeProposal, AuthorDecision ani istniejących artefaktów. Odwołuje
się do ich dokładnych wersji. Nie jest osobnym kanonem ani Development Memory.

### Pierwsze wdrożenie — obowiązkowe

1. PROJECT i SERIES: historia pipeline ekstrakcji, wersji propozycji, decyzji,
   wyników guarda/oczekiwania/odmowy/stale i canonical commit ośmiu istniejących
   typów. Jeden wspólny kontrakt i adaptery obecnych repozytoriów.
2. PROJECT research: import źródeł i trwałe wyniki operacji/claims; promocja
   przechodzi przez istniejący canonical workflow, bez osobnego commitu.
3. Istniejące F-004 operacje pamięciowe: trwały zamiar, potwierdzona projekcja
   chapter lineage i zamknięcie tomu/transfer do SERIES; potwierdzenie końcowe
   dopiero po zakończeniu istniejącego protokołu.
4. Baseline istniejącego stanu w chwili aktywacji, append-only, scoped read,
   paginacja, integralność, idempotencja oraz atomowość ze wskazanymi producentami.

SERIES baseline obejmuje także każdy `series_operations.operation_id` jako
`memory-baseline-row.v1` z dokładnymi kolumnami `operation_id`, `semantic_hash`,
`result_type`, `result_id`, `result_payload_json`. Ostatnia wartość pozostaje
surowym stringiem, bez parse ani rewrite; alias receipt pozostaje osobnym
baseline object. Tylko zapisana identity plus baseline evidence daje
`LEGACY_BEFORE_LEDGER`; brak eventu sam w sobie nie jest takim dowodem i nie
uprawnia do wytworzenia dawnego `SERIES_VOLUME_CLOSED` lub
`CANONICAL_COMMITTED`.

### Jawnie poza coverage v1

Pełne as-of i projekcja temporalna, retcony, ReaderKnowledge, automatyczna
odbudowa wszystkich pochodnych, ogólny workflow/scheduler, osobny system
decyzji development, nowe zmiany ochrony lub authority. Nadal obowiązuje
DERIVED_REBUILD_UNSUPPORTED i CURRENT_PROTECTION_UNKNOWN.

Nie rejestrujemy jako osobnych MemoryEvent każdego technicznego SDK attempt,
ContextPackage, EvaluationRecord, lock acquisition, GET/review challenge,
zapisu StyleRecipe/StylePerformance ani kuracji Style Library. Ich obecne
historie/hashe pozostają źródłami referencji; nie twierdzimy, że ledger pokrywa
cały system stylu. Także niskopoziomowe seed/import methods repozytoriów,
bezpośrednie administracyjne zapisy, stara ścieżka plikowa canon rebuild i
przyszły Human Edit nie uzyskują automatycznie statusu producenta v1.
Ich wcześniejszy stan obejmuje bootstrap, nie fikcyjna historia operacji.
Dokumentacja odczytu musi pokazywać `coverage=MEMORY_PIPELINES_V1`.

## 2. Macierz producentów i zdarzeń

Wszystkie nazwy event types w tabeli są zaakceptowanymi nazwami v1.
`P` = project.db, `S` = series.db, właściciel zgodny ze scope. Brak drugiego
event busa, recovery engine lub audytora wykonującego decyzje.

| Zdarzenie | Istniejący producent / miejsce integracji | Zapis i transakcja/recovery | Wymagany dowód przyszły |
|---|---|---|---|
| LEDGER_BASELINE_OBJECT, LEDGER_ACTIVATED | ProjectRepository/SeriesRepository, jawny bootstrap opisany w planie | P lub S, jedna transakcja bootstrap + marker ACTIVE | Nie tworzy dawnych zdarzeń; dokładny snapshot i powtarzalne reopen |
| EXTRACTION_STARTED | `canon_service.process_accepted_artifact`, `_pipeline_transaction`; repository `canonical_pipeline_operation` | P/S razem ze `started`, źródłem i identity operacji; przed modelem | Crash po rezerwacji zachowuje źródło i nie daje zgody na nowe wywołanie |
| EXTRACTION_ATTEMPT_RECORDED | Ta sama funkcja, utrwalenie `state.attempts` | P/S razem z zapisaną próbą; referencje candidate, extractor, verifier, verification | Osobne invocation IDs, ACCEPT/REVISE/REJECT, bez duplikatu próby |
| EXTRACTION_FINISHED | Ta sama funkcja: REJECT/FAILED przy terminalnym `state.result`; ACCEPT przy trwałym utworzeniu proposal z przyjętym evidence | P/S; ACCEPT razem z proposal, pozostałe z wynikiem; nie dodawać success do `state.result`, co zmieniłoby semantykę retry pipeline | Błąd append cofa odpowiadający zapis; błędny verifier nie daje canonical event |
| PROPOSAL_RECORDED | `process_accepted_artifact`, `save_canonical_proposal_for_review`, `prepare_research_proposal` | P/S, `canonical_proposal_transaction`, razem z pierwszym zapisem dokładnej wersji | Każdy producent ma event, proposal istnieje bez promocji |
| PROPOSAL_STATE_RECORDED | Zapis impact/initial guard w producerze; `commit_canonical_proposal`; obsługa trwałego FAILED/STALE | P/S, razem z przejściem stanu; fazy i sloty z §4 | Oczekiwanie, DENY, STALE i final guard mają właściwy basis/impact; GET nie tworzy event |
| AUTHOR_DECISION_RECORDED | `canon_service.record_operator_decision` | P/S w tej samej transakcji co decision i zużycie challenge | APPROVE/REJECT i authorization_ref związane z proposal; retry odtwarza event |
| CANONICAL_ENTITY_CHANGED | `commit_canonical_proposal` wraz z `ProjectRepository.apply_canonical_record_set` / SERIES odpowiednikiem | P/S, po final guard, w lokalnym commicie całego zestawu | 9 rekordów/8 typów: każda zmiana ma entity refs old/new; rollback obejmuje wszystkie |
| CANONICAL_COMMITTED | Ten sam CanonService; zapis `canonical_commit.v1:<operation_id>` | P/S, ten sam connection co rekordy, historia, receipt i wszystkie entity events | Jeden receipt i jeden batch event; utrata odpowiedzi nie powiela wersji |
| RESEARCH_COMMAND_RECORDED | `research._save_command` wywoływany przez `create_research` / `import_source` | P, istniejący atomowy zapis `research.v1` | Źródło tekstowe vs REFERENCE_ONLY rozróżnione; nie kopiuje treści źródła do event |
| RESEARCH_RESULT_RECORDED | `research.run_research`, końcowy zapis wyniku/claims/konfliktów | P, ta sama transakcja; fencing istniejącej operacji; recovery przez `recover_operation` | Terminalne wyniki i odmowy, brak spóźnionego wyniku po recovery |
| ARTIFACT_WRITE_INTENDED | `CrossStoreRecoveryService._create_or_validate_intent` dla `CHAPTER_ARTIFACT_LINEAGE_V2` i operacji z series_snapshot | P, razem z istniejącym intent F4; weryfikacja durable payload | Nie oznacza obecności pliku ani zaakceptowania rozdziału |
| SERIES_VOLUME_CLOSED | `SeriesRepository.close_volume` | S razem ze snapshotem, transferami i operation receipt; bez czekania na projektowy marker | Awaria po SERIES commit przed checkpoint P odtwarza ten sam event |
| ARTIFACT_WRITE_CONFIRMED | `CrossStoreRecoveryService._write_final_audit` | P razem z final audit i COMMITTED po weryfikacji wymaganych efektów | Brak pliku nie daje CONFIRMED; intent/confirmed różne sloty |
| CHAPTER_VERSION_RECORDED | `chapter_lineage.persist_chapter_lineage` → F4 final audit hook, na podstawie trwałego planu | P w tej samej finalnej transakcji co CONFIRMED, po weryfikacji F5 artefaktu | Każda wersja i jej status/quality refs; ACCEPTED oznacza istniejący status artefaktu, nie zgodę na kanon ani Source Master |
| MEMORY_EVENT_CORRECTION | Kontrolowana operacja administracyjna istniejącego repozytorium, bez publicznego mutation endpointu v1 | Ta sama lokalna DB; nowy event z parent i powodem | Nie zmienia starego eventu, faktu ani historycznej decyzji autora |

Dla canonical entity events atomowy hook musi być przy fizycznym zapisie w
repository, pod istniejącym guardem; batch COMMITTED dopisuje CanonService.
Nie wolno polegać wyłącznie na późniejszym logowaniu po powrocie z transakcji.
Wszystkie nowe branche trwałego wyniku producentów z tabeli należą do coverage;
testy mają wykrywać brak hooka również w ścieżce wyjątku/odmowy.

`canonical_pipeline_operation` i część istniejących wrapperów zwracają obecnie
stan, nie publiczny connection. Integracja ma udostępnić wewnętrzny hook/connection
w tej samej transakcji ownera, przed jej COMMIT; append po wyjściu z wrappera
na nowym connection nie spełnia kontraktu. Jeżeli close_volume rozpoznaje alias
operation_id dla identycznego snapshotu, event CLOSED wiąże pierwotny operation_id
utrwalonego snapshotu; nowy alias receipt nie oznacza drugiego zamknięcia tomu.

F4 hook jest filtrowany typem operacji, nie loguje jako MemoryEvent każdej
projekcji technicznej GAP-017. Powiązania Evaluation/ModelInvocation są refs,
nie nowe fikcyjne oceny/wywołania. SERIES source wskazuje inicjujący projekt,
ale owner eventu nie przenosi się do project.db.

## 3. MemoryEventRecord v1 — pola i walidacja

Obiekt jest zamknięty: nieznane pola odrzucane. `schema_version=1`,
`hash_version=memory-event-content.v1`. Wszystkie wymienione klucze są obecne;
nullable oznacza jawne JSON null, nie pominięty klucz.

| Pole | Typ / nullability / reguła |
|---|---|
| memory_event_id | string NOT NULL: `MEV-` + SHA-256 key preimage (§4), lowercase hex |
| sequence | integer > 0, nadawany lokalnie w DB; jedyny porządek historii w tym scope |
| scope_type, scope_id | PROJECT/PROJ-id albo SERIES/SERIES-id zgodne z rzeczywistym StorageScope i tożsamością DB; istniejące DomainId validators |
| operation | object NOT NULL: namespace (zamknięty enum poniżej), id (niepusty string zachowujący ID ownera) |
| event_slot | tablica niepustych stringów o wersjonowanym znaczeniu z §4; żadnego losowego attempt ID generowanego na retry |
| event_type | jeden enum z §2; schema_version rozstrzyga registry payloadu |
| timestamp, created_at | RFC3339 UTC `YYYY-MM-DDTHH:mm:ss.ffffffZ`, nadane przez repository na pierwszym INSERT; identyczne w v1, oznaczają zapis historii, nie czas świata |
| actor | object: kind SYSTEM/OPERATOR; id niepusty; evidence_ref nullable. Operator musi mieć istniejący trwały dowód uwierzytelnionej decyzji; migration = SYSTEM, id MEMORY_LEDGER_BOOTSTRAP_V1. Model jest źródłem wyniku/invocation, nie autorem zgody |
| project_id, book_id, series_id | istniejące domain IDs albo null; PROJECT wymaga pierwszych dwóch; SERIES nowe zdarzenie procesu wymaga inicjującego project/book i zgodnego series_id. SERIES bootstrap całego store dopuszcza null project/book z powodem STORE_BASELINE |
| run_id, step_id | niepuste stringi lub null; nie fabrykować run dla importu/bootstrap. Null dozwolone tylko gdy producer nie ma takiej tożsamości |
| entity_refs | tablica 0..N: `{record_type, entity_id, version}`; version dodatni int albo null dla eventu niezmieniającego encji. Zmiana encji wymaga dokładnych old/new refs w payloadzie |
| parent_refs, artifact_refs, source_refs | tablice 0..N typowanych referencji z §5, nie dowolne stringi pozwalające czytać pliki |
| structured_payload | zamknięty object zależny od event_type: tylko opis konkretnego skutku/obserwacji, outcome/reason_code i odwołania; nie drugi snapshot całego envelope |
| content_hash | SHA-256 pełnego zapisanego obiektu bez tego pola, zgodnie z §4 |

Czas świata i narrative order są opcjonalnymi danymi źródłowej wersji encji,
nie nową osią porządku ledgera. Jeśli producer przekazuje je w payloadzie:
`world_time={valid_from:string|null, valid_to:string|null}` i
`narrative_order:nonnegative int|null`, wyłącznie dosłowne wartości źródłowe.
Nie konwertować znaczenia czasu istniejących kontraktów. Brak oznacza null,
nie czas zapisu eventu. Nie powstaje query-as-of v1.

Registry payloadów jest konkretnie ograniczone do:

- baseline: category, typed source locator, bytes_hash, snapshot_text lub
  immutable_ref, observed_schema_version, history_completeness=UNKNOWN_BEFORE_BOUNDARY;
- activation: baseline_count, manifest_hash, coverage, origin=EMPTY_STORE/LEGACY_CUTOVER;
- extraction: phase/attempt ordinal, source/candidate/verification/invocation refs,
  outcome i allowlisted reason_code; rezultat zapisany przez istniejącego ownera;
- proposal/state: proposal_id/version/hash, phase, previous_state/new_state,
  basis_hash i impact/guard refs (null tylko przed powstaniem tych dowodów);
- decision: approval_id, authorization_ref, proposal/version/hash, APPROVE/REJECT;
- entity: entity type/id, old_version/hash nullable dla CREATE, new_version/hash,
  proposal ref i commit operation ref; stare hashe zachowują własny hash_scheme;
- commit: receipt ref/hash, sorted entity event IDs, outcome=COMMITTED;
- research: command/action, source/claim/version refs, result/outcome/reason_code;
- artifact: F4 operation/input_hash, typ, confined path, expected/observed hash,
  intent lub confirmed; wersja rozdziału dodatkowo chapter_id/version/status,
  tekst hash i quality evaluation ref (nullable zgodnie z rzeczywistym F5);
- volume: snapshot_id/semantic_identity, operation receipt ref, transferred entity refs;
- correction: target memory_event_id/content_hash, reason, corrected_description.
  corrected_description nie jest nową decyzją kanoniczną ani nadpisaniem faktu.

Pola nieznane przed daną fazą mają null, tylko tam gdzie pozwala wariant.
Udokumentowany wariant payloadu jest warunkiem testu; nie ma dowolnego `metadata`
przyjmującego sekrety, tekst wyjątku SDK lub całe env. Bootstrap snapshot_text
jest jedynym celowym wyjątkiem od nieduplikowania danych: zachowuje obserwowany
stan, gdy istnieje tylko zmienny locator; uzasadnienie w planie migracji.

## 4. Tożsamość, serializacja i współbieżność

Namespace operation: CANONICAL_PIPELINE, CANONICAL_PROPOSAL, RESEARCH_COMMAND,
RESEARCH_OPERATION, CROSS_STORE_OPERATION, SERIES_VOLUME_OPERATION,
LEDGER_BOOTSTRAP, LEDGER_CORRECTION. Zachować istniejące ID; scope i namespace
zapobiegają kolizjom między równymi lokalnymi stringami.

Key preimage = JSON obiektu:
`{key_version:"memory-event-key.v1",scope_type,scope_id,operation,event_slot}`.
event_type jest sprawdzany w treści, nie zmieniany w istniejącym slocie.
Przykładowo ta sama operacja pipeline ma sloty `["start"]`,
`["attempt","1"]`, `["terminal"]`; proposal `["proposal","1"]`,
`["state","1","initial-analysis"]`, `["decision","1"]`,
`["state","1","final-validation"]`, `["entity","1","FACT","FACT-test"]`,
`["commit","1"]`. Numer wersji/attempt zamieniany na dziesiętny string bez zer.

Oczekiwanie initial guard i powtórna final validation przed zgodą mogą być
różnymi obserwacjami: final-validation slot ma dodatkowo revision dowodu
`NONE` lub istniejący approval_id. Zmiana bazy prowadzi do terminalnego STALE;
nowa proposal version dostaje inne sloty. Nie logować każdego identycznego
sprawdzenia ani timestampu retry jako nowego zdarzenia. Jeśli istniejący producer
legalnie wykonuje kilka trwałych finalizacji FAILED w tej samej identity,
slot obejmuje jego **utrwalony** numer próby przed wykonaniem, nigdy lokalny licznik
odtwarzany od zera. Producer bez takiej identity nie dostaje nowego retry eventu.

F4 sloty `["intent"]`, `["confirmed"]`, `["chapter-version",chapter_id,version]`;
SERIES volume `["closed"]`. Bootstrap `["object",category,stable_locator]`
i `["activated"]`. Correction operation ma własne jawne command ID i `["correction"]`.
Jedna operacja może zatem mieć wiele eventów, ale jeden slot ma jeden wynik.

**Serializacja wszystkich nowych preimages v1:** UTF-8 bez BOM;
Python JSON `sort_keys=True, separators=(",", ":"), ensure_ascii=True,
allow_nan=False`; bez końcowego LF, bez Unicode normalization; unpaired
surrogates odrzucone. Zamknięte nowe obiekty używają string/bool/null/int
(signed 64-bit), dict i list; brak float. Float w dawnych danych zachować
w snapshot_text jako tekst oryginalnego źródła, bez zmiany dawnych hashy.
Tablice refs i entity_refs są set-like: usunięcie identycznych duplikatów,
sort po tych samych kanonicznych bytes; event_slot zachowuje kolejność.
Nie przyjmować JSON z powtórzonymi kluczami.

Trzy różne skróty:

1. memory_event_id = `MEV-` + SHA256(key preimage).
2. comparison payload = wszystkie pola logicznego rekordu bez sequence,
   timestamp, created_at, content_hash; memory_event_id już wynika z key.
   Repository porównuje jego kanoniczne bytes przy retry (nie tylko hash).
3. content_hash = SHA256 kanonicznych bytes kompletnego zapisanego rekordu
   bez content_hash, z memory_event_id, sequence i pierwszym timestamp/created_at.

Przed append: BEGIN IMMEDIATE (albo istniejąca transakcja write ownera),
odczyt po unique key; obecny event musi przejść content_hash check.
Identyczny comparison payload zwraca cały istniejący rekord, z tym samym
ID/sequence/czasem. Zmieniona treść = MEMORY_EVENT_IDENTITY_CONFLICT, rollback.
Nowy key: INSERT, przypisanie sequence i zapis ostatecznego JSON/hash w ramach
jednego INSERT (sequence wyznaczone pod lockiem jako następna wartość zgodna
z `max(sqlite_sequence dla memory_events, MAX(sequence), 0)+1`, bez UPDATE
dopiero utworzonego eventu; przepełnienie int64 oznacza odmowę).
UNIQUE key i ID stanowią drugą barierę; konflikt constraint nie oznacza sukcesu
bez odczytu i porównania zwycięskiego rekordu. Równoczesne duplikaty serializuje
SQLite. Busy timeout daje błąd retryable, nie nowy event ID.

Kolejność jest scope-local: `(scope_type,scope_id,sequence)`; luki dozwolone,
UUID i timestamp nie definiują kolejności. Nie ma globalnego porządku P/S.
W jednym batchu: eventy encji posortowane `(record_type,entity_id)`, potem
CANONICAL_COMMITTED. Przy niezależnych równoczesnych operacjach kolejność oznacza
faktyczną serializację zapisu, nie determinizm kolejności requestów sieciowych.

## 5. Referencje, integralność i append-only

Każda reference: `{kind, owner_scope_type, owner_scope_id, locator, version,
hash_scheme, hash}`. Kind: MEMORY_EVENT, ENTITY_VERSION, PROPOSAL, APPROVAL,
ARTIFACT, SOURCE, EVALUATION, MODEL_INVOCATION, RECOVERY_OPERATION,
CONTEXT_PACKAGE, SNAPSHOT, BASELINE_OBJECT. `version` string lub null,
hash_scheme/hash string; null hash dopuszczalny tylko w oznaczonym bootstrap
unverifiable locator, nigdy jako dowód nowego commitu.

Locator jest walidowaną referencją repozytorium/JSON pointer do konkretnego
niezmiennego podobiektu albo ścieżką względną do zatwierdzonego artifact root.
Zakazane traversal, dowolna ścieżka systemu i token operatora. Mutable envelope
nie jest hashowany jako całość dla starego eventu: wskazać jego wersję/proposal
hash preimage, konkretną próbę lub niezmienny receipt. Jeśli podobiektu nie da
się później odtworzyć, producer musi zachować mały immutable evidence snapshot
w swoim dotychczasowym ownerze przed append; nie zmienia frozen hash preimages.

Nowy obowiązkowy parent/source musi istnieć w transakcji lub jako wcześniej
utrwalony dowód w uprawnionym ownerze. Lokalny parent ma niższy sequence.
Cross-store ref wymaga dokładnego scope/membership i odczytanego dowodu;
nie daje prawa do enumeracji obcego projektu. Odwołanie do jeszcze nieistniejącego
efektu w INTENDED jest **expected target**, nie potwierdzający source_ref.

Normalny odczyt weryfikuje hash rekordu, indeksowane pola i typ payloadu.
Rozwiązywanie refs daje osobne statusy VERIFIED / MISSING / HASH_MISMATCH /
NOT_AUTHORIZED / LEGACY_UNVERIFIABLE. Historyczny event pozostaje czytelny, lecz
brak dowodu nie staje się pozwoleniem na canonical write ani sukcesem recovery.
Nie ujawniać szczegółów zasobu NOT_AUTHORIZED. Uszkodzony content_hash kończy
odczyt błędem MEMORY_EVENT_INTEGRITY_ERROR, bez pomijania wiersza w paginacji.

Append-only obejmuje także SQL `REPLACE`: BEFORE UPDATE i BEFORE DELETE na
`memory_events` oraz `memory_event_entities` kończą się `RAISE(ABORT)`, a
BEFORE INSERT dla `memory_events` sam sprawdza kolizję istniejącego `sequence`,
`memory_event_id` i `event_key_json`, kończąc każdą `RAISE(ABORT)`. Ten trigger
nie może zależeć od `PRAGMA recursive_triggers=ON`: ma odmówić także przy OFF.
BEFORE INSERT dla `memory_event_entities` analogicznie odmawia istniejącego PK
`(sequence, record_type, entity_id)`. `INSERT OR REPLACE`, `REPLACE` i UPSERT
nadpisujący istniejący event lub indeks są zakazane w każdej ścieżce append.
Append najpierw czyta i porównuje istniejący rekord pod tym samym write lockiem,
a dla nowego rekordu wykonuje zwykły INSERT; constraint collision nie jest
sukcesem ani podstawą do overwrite. Insert indeksu odbywa się wyłącznie atomowo
z nowym eventem; jego zgodność z `entity_refs` sprawdzana przy walidacji/odczycie.
Nieudany append propaguje błąd do ownera i cofa całą transakcję zmiany domenowej.
Korekta jest nowym eventem z parent; niczego nie stosuje automatycznie do kanonu.
Brak endpointu korekty w v1; użycie wyłącznie jawnego, uwierzytelnionego
maintenance command z reason.

Hash wykrywa niespójność, nie uwierzytelnia administratora DB. Administrator
może usunąć trigger i przepisać dane/hash; nie deklarujemy WORM/tamper-proof
storage ani ochrony przed właścicielem Windows. Backup/ACL pozostają odrębne.

## 6. Persistence — minimalny schemat

PROJECT schema **5→6**, SERIES **3→4**, SYSTEM pozostaje **1**.
Źródło obecnych numerów: `app/p20_core/project_repository.py` stałe schema
version i rejestry migracji; żadnej realnej DB nie otwierano.

W obu istniejących DB te same dwie tabele, różniące się walidacją owner scope:

- `memory_events`: sequence INTEGER PRIMARY KEY AUTOINCREMENT;
  memory_event_id TEXT NOT NULL UNIQUE; event_key_json TEXT NOT NULL UNIQUE;
  scope_type/scope_id TEXT NOT NULL; operation_namespace/operation_id TEXT NOT NULL;
  event_type TEXT NOT NULL; schema_version INTEGER NOT NULL CHECK=1;
  record_json TEXT NOT NULL; content_hash TEXT NOT NULL.
  CHECK niepustych ID, dozwolonego scope/event enum, 64 lowercase hex hash.
  BEFORE INSERT sprawdza scope z istniejącą tabelą identity tej DB oraz trzy
  istniejące identity (`sequence`, `memory_event_id`, `event_key_json`) i
  odmawia kolizji przez `RAISE(ABORT)` niezależnie od recursive_triggers;
  brak możliwości wstawienia PROJECT eventu do series.db i odwrotnie.
- `memory_event_entities`: sequence INTEGER NOT NULL FK memory_events(sequence)
  ON DELETE RESTRICT; record_type/entity_id TEXT NOT NULL; PRIMARY KEY
  (sequence,record_type,entity_id). BEFORE INSERT odmawia kolizji tego PK przez
  `RAISE(ABORT)`; indeks pomocniczy nie jest drugą prawdą domeny.

Indeksy: `memory_events(scope_type,scope_id,operation_namespace,operation_id,sequence)`;
`memory_events(scope_type,scope_id,event_type,sequence)`;
`memory_event_entities(record_type,entity_id,sequence)`. PK obsługuje paginację.
Wybrano osobny indeks encji, ponieważ event batch może dotyczyć wielu rekordów;
nie pełny indeks każdego źródła ani nowe tabele kopii Evaluation/Invocation.

Marker w istniejącym project_metadata/series_metadata:
`memory_ledger_control.v1`, ze stanem SCHEMA_READY/ACTIVE,
coverage, bootstrap operation ID, activation_event_id, baseline_count i
manifest_hash. To kontrola wdrożenia, nie status maszyny stanów każdej operacji.
Nie kopiować envelope GAP-017 ani budować nowych PREPARED/RUNNING dla eventu.

Walidator schematu wymaga obecności i definicji wszystkich BEFORE INSERT oraz
BEFORE UPDATE/DELETE triggerów powyżej, ich `RAISE(ABORT)` i kontroli trzech
identity eventu / PK indeksu; sam fakt tabeli, UNIQUE lub FK nie wystarcza.

Metody **istniejących** ProjectRepository/SeriesRepository:
`append_memory_event(connection, event_input)`;
`get_memory_event(memory_event_id)`;
`list_memory_events(cursor, limit, operation=None, entity=None)`;
`validate_memory_event_references(event)`.
SERIES każda normalna metoda read/list/append/resolve wymaga SeriesAccessContext
+ zarejestrowanego członkostwa. Jedyny wyjątek stanowi niepubliczny helper
wewnętrzny istniejącego `SeriesRepository`, wywoływany wyłącznie przez jego
initialize/bootstrap: może zapisać `LEDGER_BASELINE_OBJECT` i `LEDGER_ACTIVATED`
w namespace `LEDGER_BOOTSTRAP` dla własnej, już zwalidowanej tożsamości series.db.
Jest ograniczony do jednej transakcji schema/identity/activation albo uprzednio
zatwierdzonego markeru SCHEMA_READY; dla STORE_BASELINE może użyć SYSTEM i null
project_id/book_id, a dla pustego magazynu origin=EMPTY_STORE. Nie ma publicznego
`bypass_auth`, nowego serwisu, klienta ani payloadu SYSTEM uruchamiającego tę
ścieżkę. Nie daje prawa do canonical write, correction ani zwykłego odczytu
historii bez membership.
Append wymaga połączenia właściwego ownera już w transakcji; nie otwiera drugiego
połączenia, nie commit-uje callerowi i nie przyjmuje arbitralnego scope klienta.
Zamknięty wspólny codec MemoryEventRecord może być dodany obok istniejących
domenowych rekordów; nie powstaje LedgerRepository ani LedgerEngine.

## 7. Authority, transakcje i recovery

Zdarzenie „propozycja”, „czekamy”, „odrzucono”, „import źródła” i nawet
„chapter ACCEPTED” nie promuje faktów. CanonService, impact, final guard,
bieżące versions/approval/credential binding pozostają konieczne. Event nie
niesie nowej authority. Odczyt/replay historii nigdy nie wykonuje decyzji autora.

`requested_by` lub actor w payloadzie klienta nie uwierzytelnia autora eventu.
Integracja przekazuje principal z istniejącej granicy operatora albo zapisuje
SYSTEM z referencją do procesu; nie uznaje deklaracji klienta/modelu za OPERATOR.

### Jedna DB

Zapis domenowy + historia wersji + receipt/audit + wszystkie właściwe MemoryEvent
dzielą jeden connection i jedną lokalną transakcję. Błąd eventu cofa cały zestaw.
Analogicznie decision + used challenge + decision event, proposal + event,
wynik researchu + claims + event. Stan „domena committed, event jeszcze nie”
nie jest dopuszczalnym nowym stanem lokalnym po cutover.
Odmowa bez mutacji kanonu może mieć swój event w transakcji wyniku odmowy.

### PROJECT / SERIES / pliki

Autorytatywny canonical commit SERIES jest lokalny w series.db; nie uruchamia
F4 tylko dlatego, że zainicjował go projekt. Rzeczywiste F4 operacje zachowują
project.db jako owner intent/recovery, plik jako projekcję, a `close_volume`
jako idempotentny lokalny zapis SERIES. SERIES event powstaje w series.db;
PROJECT potwierdzenie zawiera ref do trwałego snapshotu/eventu SERIES. Brak
globalnego ACID, brak kompensacji kasującej już potwierdzone zdarzenia.

Nowe ledger dane nie wchodzą do istniejących input_hash, proposal_hash,
ContextPackage ani plan identity. F4 eventy powstają deterministycznie z
już zapisanego planu/receiptu przy odpowiednich checkpointach. Przyjazd nowego
event hooka nie zmienia hash preimages dawnych operacji.

| Granica przerwania | Rozstrzygnięcie |
|---|---|
| Przed rezerwacją/startem | Brak eventu i skutku; powtórzenie używa oryginalnej command identity. |
| BOOK_LOCKED przed startem | Nie emitować STARTED/COMMITTED. Zachować wcześniejszą rezerwację ID, jeśli istnieje; po zwolnieniu locka istniejący owner rozstrzyga pierwszy start. Ledger nie zamienia RESERVED w retry wymagający Evaluation. |
| Po durable intent/rezerwacji | Intent + jego event atomowe. Payload/binding pozostaje u istniejącego ownera. Osierocony model/research/evaluation nadal wymaga właściwego operator recovery/fencingu, nie automatycznego takeover Ledgera. |
| Po domenowym INSERT przed append, lub po append przed COMMIT | Obie części są w jednej transakcji; awaria rollback całości. Retry odczytuje wynik, nie zakłada braku commitu z braku HTTP. |
| Po lokalnym COMMIT przed odpowiedzią | Receipt i event już istnieją. Ten sam request zwraca je bez nowych wersji/zdarzeń. |
| F4 po pliku przed checkpointem | F4 weryfikuje dokładny hash, dopisuje tylko brakujące checkpointy. INTENDED nie awansuje sam do CONFIRMED. |
| Po SERIES commit przed checkpointem PROJECT | Replay close_volume zwraca snapshot/receipt i ten sam event S. Projekt dopisuje checkpoint, nie wykonuje nowej domenowej decyzji. |
| Po LOGICAL_COMMIT przed final audit | AUDIT_PENDING pozostaje u F4; final audit + CONFIRMED + chapter events są jedną transakcją P. |
| Brakująca projekcja po reopen | Wyłącznie F4 może odtworzyć ją z pinned planu według istniejących gwarancji. Ledger nie renderuje plików ani nie uruchamia modelu. Sprzeczny hash → NEEDS_INTERVENTION. |
| Spóźniony wynik modelu po recovery | Istniejący attempt token musi być sprawdzony przed zapisem wyniku i eventu w tej samej transakcji; odgrodzony wynik nie dostaje eventu potwierdzającego sukces. |
| Nowy receipt po ACTIVE istnieje bez wymaganego eventu | Naruszenie integralności, nie automatyczny backfill. MEMORY_LEDGER_NEEDS_INTERVENTION w istniejącym stanie operacji, blokada dalszych skutków; historia czytelna tam, gdzie integralna. |
| Brak dowodu bindingu lub uszkodzona DB | Jawny błąd/intervention; jeśli DB pozwala, trwały reason w istniejącym operation envelope. Jeśli zapis też niemożliwy, API zwraca błąd storage, nie twierdzi, że intervention zostało zapisane; po otwarciu obowiązkowa walidacja przed skutkiem. |

Ledger nie wprowadza osieroconej rezerwacji eventu: sequence i event istnieją
dopiero razem z transakcją skutku. Wymagana nowa identity producenta jest
utrwalana wraz z jego danymi do wznowienia. Brak kompletnego bindingu nie może
zostać „naprawiony” nową oceną/zgodą. Odmowa/intervention jest stanem możliwym
do rozpoznania, nie gwarancją automatycznego dokończenia każdego starego procesu.

## 8. Odczyt i udostępnienie

Keyset cursor v1 zawiera scope, activation_event_id, filter fingerprint,
after_sequence, high_watermark i content_hash rekordu high_watermark.
Pierwsza strona odczytuje MAX(sequence) i rekordy w jednym
snapshot read transaction. Kolejne: sequence > after AND <= high_watermark,
ORDER BY sequence ASC; limit 1..200, domyślnie 100. Cursor walidowany przeciw
scope/filter, nie jest capability ani dowodem autoryzacji. Nowe eventy nie
przesuwają stron istniejącej sesji. Brak OFFSET, globalnego merge P/S i sortowania
timestampem. Filtr operation uwzględnia namespace; entity używa indeksu.

Nie wywoływać initialize/migrate/bootstrap z metody history read. Stary schemat
daje MIGRATION_REQUIRED, SCHEMA_READY daje LEDGER_NOT_ACTIVE. Pytanie o historię
nie promuje danych i nie uruchamia recovery. Pełny store scan jest metodą
repozytorium dla autoryzowanego serwisu, nie nowym publicznym endpointem.

Istniejąca granica operatora: `app/operator_api.py`, authenticated_operator,
`_with_operator`, `_project`, `_proposal_target`. Rozszerzyć odpowiedzi istniejących
odczytów proposal oraz research records o `memory_event_refs` i status coverage;
jeśli potrzebna strona historii danej operacji, użyć opcjonalnego cursor/limit
na tym samym zasobie z jego scope, nie nowego `/ledger` ani „GET all projects”.
Nieobecny lub zmieniony high-watermark/activation po restore unieważnia cursor;
nie łączyć stron z różnych historii. Sprawdzenie schema dla history read musi
nastąpić przed ewentualnym istniejącym helperem wywołującym initialize.
Chapter/run audit projections mogą zawierać refs/coverage, lecz kanonicznym
źródłem eventów pozostaje DB; żadnego automatycznego catch-up podczas GET.
Uprawniony operator ma dostęp zgodnie z istniejącym rejestrem i membership;
parametr project_id/series_id od klienta nie jest dowodem uprawnienia.

## 9. Przyszłe bramki i status decyzji

Test matrix i sekwencja pracy są w planie migracji. Warunek wdrożenia v1:
każda ścieżka z §2 przechodzi atomicity/idempotency/failure tests; brak zewnętrznej
sieci w functional proof; P20 i repozytoria rzeczywiste, SDK zastępowane tylko
na granicy. Pełna regresja dopiero w fazie wykonawczej na finalnym kodzie.
Historyczne 838 passed / 1 skipped nie jest dowodem Memory Ledger.

Ustalenia tego dokumentu obejmują event coverage/slots, schema 6/4,
serializację v1, bootstrap granicy historii, maintenance-only migrację, read API
integration i granice correction. Nie pozostawiono wyboru między alternatywnymi
ownerami/algorytmami implementatorowi. Wykryty konflikt wdrożeniowy wymaga
osobnego wyjaśnienia, nie zmiany ADR.

## Historia rewizji

- Rewizja 1: propozycja zakresu i planu Phase 1.
- Rewizja 2: review domknął ochronę przed REPLACE, baseline `series_operations`
  oraz wewnętrzny wyjątek bootstrap SERIES bez osłabienia normalnego authority.

PHASE 1 = COMPLETE. CONTRACT = ACCEPTED / FROZEN FOR MEMORY LEDGER V1.
MIGRATION PLAN = ACCEPTED / FROZEN FOR MEMORY LEDGER V1. IMPLEMENTATION = NOT STARTED.
MIGRATION EXECUTION = NOT RUN. TESTS = NOT RUN. PHASE 2 = NOT STARTED.
Nie wykonano implementacji, migracji, uruchomienia aplikacji ani testów.
