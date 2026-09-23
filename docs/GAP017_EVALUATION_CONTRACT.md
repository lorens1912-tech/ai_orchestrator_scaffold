# GAP-017 — EvaluationRecord / Evaluation Cache

PHASE 1 = COMPLETE
CONTRACT STATUS = ACCEPTED / FROZEN FOR GAP-017 IMPLEMENTATION
PHASE 2 = IMPLEMENTATION COMPLETE
PHASE 2 TARGETED TESTS = 47 passed
PHASE 2 REPOSITORY/STORAGE REGRESSION = 19 passed
FULL REGRESSION = NOT RUN
GAP-017 = NOT CLOSED
PHASE 3 = NOT STARTED
Wersja kontraktu: 1. Data: 2026-09-16.
Wszystkie decyzje kontraktu są zaakceptowane, w tym GAP-017 RECOVERY v1
w sekcji F.1. Kontrakt nie stanowi opisu zaimplementowanego kodu.
Źródła i dowody: [GAP017_PREFLIGHT.md](GAP017_PREFLIGHT.md), sekcje 2–4.

## A. Własność i zakres

Jedyny owner: istniejący ProjectRepository związany przez StorageResolver
z project/book; trwałość w project.db. Bez globalnego cache i bez odczytu
obcego project.db. Series identity jest bindingiem pochodzenia kontekstu,
nie przenosi właściciela oceny projektu do series.db.

Najmniejsze rozszerzenie: wersjonowany envelope w istniejącym project_metadata
`evaluation.v1:<operation_id>`, atomowo obejmujący binding operacji, attempts,
ukończony EvaluationRecord, jego hash i lokalny audit. Dodatkowy indeks, jeśli
potrzebny, jest tylko projekcją odbudowywalną z ownera. Brak nowego repository,
cache service i recovery engine. Numer wersji envelope jest jawny;
nieobsługiwana wersja oznacza błąd, nie domyślną konwersję.

Istniejące step artifacts, run_state w odpowiedzi /agent/step i audit.json
otrzymują referencję/projekcję rekordu. Odczyt nie uruchamia nowej oceny.
Ścieżki plikowe nie są drugim właścicielem; brak projekcji naprawia recovery.

IN: finalne oceny QUALITY aktywnego P20, ich powiązanie z wejściowymi uwagami
CRITIC/StyleEvaluation, wersją tekstu, kontekstem i następnymi bramkami;
retry, explicit reevaluation, trwałość, audyt i izolacja.
OUT: LLM-as-judge, nowe kryteria/progi/routing, nowy autonomiczny rewrite
engine, Book QA/Source Master/GAP-018, promocja kanonu przez cache, P0,
przebudowa Style Library lub canonical/research verification cache.

## B. Tożsamość i rzeczywisty wykonawca

Rekord zachowuje wymagane pola ARCHITEKTURA §89:
evaluation_id, project_id, artifact_id, artifact_hash, criteria_version,
prompt_version, context_package_id, context_hash, requested_model,
effective_model, provider, model_version, decision, reasons[], must_fix[], created_at.
Addytywnie: schema_version, book_id, series_id|null, run_id, step_id,
operation_id, evaluation_kind=QUALITY, evaluator_kind, evaluator_id/version,
criteria_hash, configuration_hash, input_fingerprint, record_hash,
invocation_refs[], input_evaluation_refs[], execution_status,
validation_status, completed_at, reevaluation_of|null.

evaluation_id jest przydzielany raz podczas rozpoczęcia operacji i zachowany
przez jej retry. operation_id wiąże istniejącą tożsamość wykonania P20
project/book/run/step i kind QUALITY. Ordinale w pętli poprawy są osobnymi
logicznymi krokami; attempt_id nie jest evaluation_id.
Weryfikować istniejące ID przed odczytem/zapisem, nie ufać samej ścieżce.

artifact_hash to SHA-256 dokładnych bajtów UTF-8 ocenianego tekstu, zgodnie
z F-005; hash step JSON pozostaje odrębnym hashem pliku. Ocena wejściowego
tekstu bez wcześniejszego WRITE dostaje trwałą referencję wejścia operacji,
nie pusty artifact_id ani odczyt ruchomego latest. Binding wskazuje dokładną
wersję tekstu, a także rzeczywiście użyte wejścia stylistyczne i kryteria.

LOCAL_DETERMINISTIC: evaluator_id wskazuje rzeczywistą gałąź
quality_contract._fq_tool_quality lub quality_rules.evaluate_quality
z istniejącym style veto. requested/effective_model/provider/model_version
są null z jawnym znaczeniem NOT_APPLICABLE; snapshot routingu kroku jest
oddzielną referencją, nie fikcyjną tożsamością sędziego. prompt_version ma
jawny znacznik NOT_APPLICABLE:LOCAL_V1. criteria_version i evaluator_version
identyfikują realny algorytm; criteria_hash obejmuje jego reguły, efektywne
progi i style gate. Snapshot zachowuje min_words/forbid_lists i obecność pól.

Jeżeli w przyszłości zaakceptowany evaluator rzeczywiście użyje modelu,
rekord będzie referował ModelInvocationAudit GAP-016 i jego przypiętą
konfigurację; nie skopiuje jego prób, transportu ani recovery. Parametry
i model/provider reported pozostają tam; nieznany snapshot pozostaje null.
Ta faza nie aktywuje takiego wykonawcy. Wyniki precision/completeness
kanonicznego verifiera nie są EvaluationRecord jakości tekstu.

## C. Dokładny cache i integralność

Proponowany klucz v1 = SHA-256 kanonicznego JSON nowego envelope:
UTF-8, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
allow_nan=False. Własny domain separator `GAP017_EVALUATION_CACHE_V1`.
Nie zmieniamy żadnego starego preimage ani serializacji.

Preimage zawiera: project/book/series identity; run/step/operation/kind;
artifact_id, wersję i artifact_hash; criteria_version/hash; prompt_version
i hash rzeczywistego promptu (NOT_APPLICABLE lokalnie); evaluator_kind/id/version;
rzeczywistą effective model identity lub jawne LOCAL; configuration_hash;
context_package_id/hash; hashe wszystkich użytych dodatkowych wejść, w tym
StyleEvaluation/recipe/DNA i krytyki, jeśli wpływają na tę ocenę.
Brakujące krytyki nie są wymyślane. ID/czas nowej próby nie należą do klucza.
Nieznany model_version nie oznacza dowolnego modelu; binding opiera się na
przypiętym rzeczywistym wywołaniu, nie na hipotetycznej zgodności snapshotów.

Hit WYŁĄCZNIE gdy ta sama operacja, scope, run, step, kompletne wejścia,
obsługiwana wersja, execution COMPLETED, validation VALID, decyzja z dozwolonego
zbioru oraz integralność rekord/context/artifact są zgodne. Odczyt odtwarza
ten sam evaluation_id, decision, reasons i must_fix. Zmiana tekstu, kontekstu,
kryteriów, parametrów lub gałęzi algorytmu przy tej samej operacji jest
konfliktem, nie cichym miss z nadpisaniem historii. Nowa operacja liczy nową ocenę.
Cache między odrębnymi operacjami jest poza tym podzakresem.

Historyczny brak pełnego bindingu: NOT_RECORDED, brak hit i brak backfill
fikcyjnych wersji/modelu. Technical retry bez wystarczających danych kończy
się jawnym błędem wymagającym decyzji o nowej ocenie. Uszkodzony rekord
to INTEGRITY_ERROR/NEEDS_INTERVENTION, nigdy ACCEPT ani automatyczna rekalkulacja.
record_hash obejmuje immutable EvaluationRecord bez własnego hash i bez
historii odczytów. Integralność sprawdzana przed każdym użyciem.

## D. Retry i świadoma reevaluation

Technical retry używa istniejących project/run/root step i zapisanej kolejki,
wejść oraz ContextPackage. Nie może wykonać ponownie ukończonej oceny tylko
dlatego, że projekcja JSON nie powstała. Reuse nie czyta nowego stanu jako
zamiennika starego kontekstu i nie przyjmuje zmienionego tekstu.
Nie rozszerzamy resume między projektami, runami ani audytami.

ACCEPTED addytywny intent w istniejącym /agent/step payload:
`evaluation_intent=REEVALUATE`, `reevaluation_of=<evaluation_id>`.
Domyślny przebieg QUALITY pozostaje oceną nowej operacji. Serwer tworzy
nowy step/operation/evaluation_id dla intencji REEVALUATE i zapisuje jej
powiązanie z poprzednią oceną; nie wolno użyć starego operation_id.
Po nadaniu nowej tożsamości ponowienie tej operacji używa zwykłego
technical_retry z zwróconymi IDs, bez ponownego REEVALUATE.
Jednoczesne technical_retry i REEVALUATE jest błędem walidacji.
Klient może podać nową stabilną tożsamość żądania dla deduplikacji duplikatów;
bez niej dwa odrębne intencjonalne żądania są dwiema operacjami.
Nowa ocena omija stary cache nawet dla identycznych bajtów i kryteriów.
Może korzystać z nowego ContextPackage; poprzedni pozostaje audytowalny.

## E. Trzy niezależne osie

| Oś | Proponowane wartości |
|---|---|
| execution_status | PREPARED, RUNNING, COMPLETED, FAILED, INTERRUPTED |
| validation_status | NOT_PERFORMED, VALID, INVALID |
| decision | ACCEPT, REVISE, REJECT albo null, gdy brak poprawnej oceny |

COMPLETED+VALID+REJECT jest poprawnie ukończoną oceną i cache hit.
Refusal, timeout, UNKNOWN remote outcome i błędny schema nie są REJECT.
Nie kopiujemy legacy runtime status=decision do execution_status rekordu.
Nie zmieniamy progów ani existing quality policy. CRITIC bez decyzji to
wejściowy dowód, a nie rekord z automatycznym ACCEPT.

## F. Atomowość, recovery i współbieżność

Krótka lokalna transakcja BEGIN IMMEDIATE w istniejącym ProjectRepository
wiąże operację i attempt token. Nie utrzymuje blokady podczas obliczeń ani
sieci. Finalny zapis sprawdza ten sam token/binding, waliduje wynik i atomowo
zapisuje EvaluationRecord, record_hash oraz lokalny audit. Stary token nie
może nadpisać nowej próby. Dwa żądania tej samej operacji nie wykonują
dwóch równoległych ocen: drugie czyta completed lub dostaje IN_PROGRESS.

| Przerwanie / stan | Zachowanie |
|---|---|
| przed jakimkolwiek trwałym zapisem operacji | brak operacji; retry może rozpocząć |
| osierocone trwałe PREPARED/RUNNING | wyłącznie jawne operator recovery; timeout nie uprawnia do przejęcia |
| po START, bez końcowego wyniku | brak udawanego completed; jawne odzyskanie osieroconej próby z fencing |
| po obliczeniu, przed transakcją finalną | wynik nie jest durable; bez potwierdzenia nie trafia do konsumenta |
| po finalnej transakcji, przed odpowiedzią/projekcją | retry odtwarza wynik bez evaluatora; naprawia tylko brakujące projekcje |
| SQLite błąd zapisu | rollback rekordu/cache/audytu, brak downstream ACCEPT |
| późny wynik po recovery | token mismatch, odrzucenie; audyt nie zmienia obowiązującego wyniku |
| obca tożsamość / zły hash / wersja | fail closed i interwencja; brak automatycznego nadpisania |
| reopen | odczyt trwałego ownera z kontrolą integralności, bez cache w procesie |

### F.1. GAP-017 RECOVERY v1 — ACCEPTED

Źródło: jawna decyzja użytkownika „GAP-017 RECOVERY v1”.
Akceptacja dotyczy poniższej semantyki recovery. Repository-level primitives
wdrożono w fazie 2; pełne operator API pozostaje zakresem późniejszej fazy.

1. Nie powstaje osobny recovery engine.
2. Normalny technical retry zachowuje project_id, run_id, step_id/operation_id
   i ten sam Evaluation binding. COMPLETED+VALID oznacza odczyt wyniku bez
   ponownej oceny.
3. Osierocone PREPARED/RUNNING nie jest przejmowane automatycznie po timeout.
   Wymaga jawnego operator recovery wyłącznie w tym samym project/run/operation.
4. Evaluation-specific recovery należy dodać do istniejącej warstwy
   operatorskiej z obecną autoryzacją i fencing pattern. Nie używać endpointu
   research/recover ani nowego uniwersalnego recovery engine.
5. Recovery unieważnia poprzedni attempt token i tworzy nowy attempt dla
   TEGO SAMEGO evaluation_id. Zachowuje oryginalny artifact/context/criteria
   binding. Nie może zamienić się w REEVALUATE.
6. Jeżeli bindingu nie da się dowieść: NEEDS_INTERVENTION, bez ponownej
   oceny i bez ACCEPT.

Wszystkie szczegóły kontraktu mają status ACCEPTED / FROZEN FOR GAP-017 IMPLEMENTATION. Ta decyzja nie jest
deklaracją istnienia endpointu ani poleceniem rozpoczęcia fazy 2.

F-004 tylko przy rzeczywistym przejściu project.db → plik/series.db.
Istniejący CrossStoreRecoveryService realizuje projekcję plikową z trwałego
wyniku i obecny zapis chapter lineage. Nie deklarujemy globalnej transakcji.
Nie zmieniać utrwalonych payloadów/hashy istniejących operacji F-004.
Lokalny zapis samej oceny nie wymaga cross-store outbox.

## G. Integracja konsumentów i authority

Runtime wybiera końcową obowiązującą ocenę konkretnego artifact_id/hash
z kolejności logicznych operacji, nie pierwszy pasujący filename ani losowy
ostatni plik. Po REWRITE potrzebna jest ocena nowej wersji; stary ACCEPT
nie przechodzi na nowy tekst. Wynik należy utrwalić po istniejącym style veto
i przed StylePerformance/canonical downstream. REVISE/REJECT nie zapisują
StylePerformance. Retry ACCEPT nie dubluje istniejącego performance.
Reevaluation zachowuje historyczny performance i jego pierwotny binding;
nowy audyt odnotowuje reuse tego efektu, nie przepisuje historii.

CanonService, impact, approval, final guard i legacy protection pozostają
obowiązkowe. Ukończona ocena nie oznacza ukończonej canonical mutation.
F-005 zachowuje stare hashe i evaluative references; nowe linki są addytywne,
bez przepisywania starszych wersji artefaktu. UI wysyła intencję, nie zapisuje
werdyktu ani cache. GAP-015, Style Library i MODEL_ROUTING_POLICY bez zmian.

## H. Kryteria akceptacji i dowody do wykonania

1. Testy kontraktu: wszystkie wymagane pola, trzy osie, LOCAL null model,
   wersje obu rzeczywistych quality algorithms, brak fikcyjnych SDK calls.
2. Parametryzowane cache tests: zmiana każdego składnika klucza; brakujące
   legacy pole, obcy scope/run/step, uszkodzony hash i zły schema fail closed.
3. SQLite persistence/reopen: ten sam evaluation_id, pełne reasons/must_fix;
   rollback na realnym błędzie zapisu, nie mock repository.
4. Concurrency: dwa takie same żądania, jedna próba i jeden wynik;
   recovery fence, late result, crash we wszystkich granicach tabeli F.
   Operator recovery zarówno PREPARED, jak i RUNNING zachowuje evaluation_id
   i binding, nadaje nowy attempt token, odrzuca poprzedni token. Brak
   autoryzacji, obcy project/run/operation i niedowodliwy binding nie pozwalają
   na wznowienie. Timeout sam nie przejmuje próby; recovery nie jest reevaluation.
5. TestClient(app.main.app): QUALITY ACCEPT/REVISE/REJECT, style veto,
   retry odtwarza wynik bez wywołania lokalnego evaluatora;
   REEVALUATE tworzy nowe ID/audit nawet dla identycznego tekstu.
6. Sekwencja QUALITY → REWRITE → QUALITY: właściwa wersja i końcowy werdykt;
   retry z __attempt JSON nie wraca do starej decyzji. Quality bez WRITE
   ma rzeczywisty wejściowy artifact binding. Reprodukcja ryzyka z preflight.
7. ContextPackage reuse, zmiana DNA/criteria/konfiguracji nie podszywa się
   pod retry; żadnych zmian frozen hash preimages i authority.
8. Downstream: ACCEPT dokładnie raz StylePerformance; REVISE/REJECT zero;
   reevaluation nie nadpisuje historii. Canon nadal wymaga niezależnego
   verifiera, approval i final guard. Crash po ocenie nie dubluje mutacji.
9. Projekcje: step/run_state/audit/lineage odnoszą się do tego samego trwałego
   rekordu. Brak pliku po crash odtwarzany istniejącym F-004; konflikt hash
   pozostaje NEEDS_INTERVENTION. Dwa projekty i dwa runy są odizolowane.
10. Targeted → integracja ContextBuilder/Style/GAP-015/GAP-016/Canonical/F-004/F-005
    → pełna regresja raz po finalnych zmianach. Syntetyczny isolated storage,
    TestClient, żadnych realnych książek/sekretów/model calls.

Faza 2 zaimplementowała wyłącznie fundament opisany w sekcjach A–F:
`EvaluationBinding`, `EvaluationRecord`, hashe i Cache Key v1, envelope
`evaluation.v1:<operation_id>`, transakcje ProjectRepository, atomiczną
finalizację, same-operation reuse, fencing i repository-level Recovery v1.
Faza 3 podłączyła rekord do aktywnego P20 QUALITY, dodała jawne REEVALUATE,
operator recovery i projekcje step/run/audit/lineage zgodnie z kontraktem.
Wyniki: 57 targeted i 226 w pakiecie integracyjnym; pełna regresja nie została
uruchomiona. PHASE 3 = INTEGRATION PASS. PHASE 4 = NOT STARTED.
GAP-017 pozostaje NOT CLOSED; GAP-018 = NOT STARTED.
