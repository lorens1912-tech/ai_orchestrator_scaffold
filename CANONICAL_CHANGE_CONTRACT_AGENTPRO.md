# CANONICAL CHANGE CONTRACT — AGENTPRO

## 0. Status i zakres

**STATUS: ACCEPTED**
Data akceptacji: 2026-09-15
Wersja dokumentu: 1.1 (uzupełnienie §6; schemat proposal/hash v1.0 bez zmian)
Data: 2026-09-15
Baza przeglądu: `f7bf22cde472d37193aeb99ca946e9252d932d09`
Zakres: kontrakt procesu potrzebny do późniejszej remediacji F-002 / F-006.

Dokument wymaga jawnej akceptacji. Jego utworzenie nie oznacza akceptacji,
implementacji ani zamknięcia findings. Wszystkie reguły poniżej są wymaganiami
dla późniejszej implementacji, chyba że oznaczono je jako stan potwierdzony.
Nie zmieniają Master Canonu ani zaakceptowanych ADR.

Źródła prawdy i zgodność:

| Źródło | Zachowane wymagania |
| --- | --- |
| [MASTER_CANON_AGENTPRO.md](MASTER_CANON_AGENTPRO.md), v2, §18–20, §70–82, §95–98 | Ochrona Kanonu, zgoda autora, lineage, idempotencja, izolacja i pojedynczy guard |
| [ARCHITEKTURA_AGENTPRO.md](ARCHITEKTURA_AGENTPRO.md), v1.2, §2–3, §44A, §102 | P20.x, API w app.main, niezależna weryfikacja ekstrakcji, atomowy zestaw, operation identity |
| [ADR-0001.md](ADR-0001.md), §5–7, §9–14 | Repozytoria związane ze scope, SeriesAccessContext, niezależne frozen i author_locked, granice transakcji |
| [ADR-00XX_NARRATIVE_STATE_ENGINE_PROPOSED.md](ADR-00XX_NARRATIVE_STATE_ENGINE_PROPOSED.md), ACCEPTED, §8, §10–13, §24–29 | Candidate != proposal, CanonService jako owner, bramki przed commitem, audyt i granice recovery |
| [ROADMAPA_AGENTPRO.md](ROADMAPA_AGENTPRO.md), v1.0 | Rozdzielenie kontraktu, implementacji i dowodu; bez zmiany kolejności etapów |

### Stan potwierdzony w repozytorium

| Komponent | Istniejące zachowanie / ograniczenie |
| --- | --- |
| `app/p20_core/memory_extraction.py` | Candidate set, provenance, osobne ModelInvocation EXTRACTOR/VERIFIER, precision/completeness, limit prób, atomowy commit_verified_memory_candidate do ProjectRepository |
| `app/p20_core/domain_mutation_guard.py` | DomainMutationGuard, MutationContext, MutationPolicy, wyniki ALLOW/DENY; brak kontraktu approval powiązanego z hashem proposal |
| `app/p20_core/impact_analysis.py` | analyze_impact, ImpactRequest/ImpactResult; BOUNDED_PROJECT_GRAPH; SERIES kończy się ImpactScopeUnsupportedError po kontroli dostępu |
| `app/p20_core/canon_service.py` | Istniejący owner funkcji kanonu i audytu; plikowy commit_chapter_to_canon/rebuild nie jest jeszcze opisanym tutaj chronionym commitem proposal |
| `app/p20_core/project_repository.py` | ProjectRepository, SeriesRepository, StorageResolver, SeriesAccessContext; domain_transaction i wywołanie istniejącego guarda przy zapisie structured memory |
| `app/p20_core/domain_records.py` | Stabilne DomainId, wersje, scope, provenance i istniejące kontrakty rekordów |

Logiczne kontrakty proposal/approval opisane dalej nie są deklaracją obecnych
klas, endpointów ani schematu DB. Identyfikatory procesu nie dodają nowych
namespace do DomainNamespace. Nazwy i ID encji muszą korzystać z jego
istniejących reguł. Nie znaleziono kontraktu owner_id/author_id ani dowodu
autoryzowanej zgody w przeglądanych modułach P20; samo actor_id nie dowodzi zgody.

## 1. Obowiązujący pipeline

```text
ACCEPTED ARTIFACT
→ STRUCTURED MEMORY EXTRACTION
→ CANDIDATE SET
→ INDEPENDENT VERIFICATION
→ PRECISION / COMPLETENESS
→ ACCEPT EXTRACTION
→ CANONICAL CHANGE PROPOSAL
→ FREEZE PROPOSAL / CALCULATE PROPOSAL HASH
→ IMPACT ANALYSIS
→ DOMAIN MUTATION GUARD EVALUATION
→ REQUIRED USER APPROVAL, jeżeli wymagana
→ FINAL GUARD VALIDATION
→ CANONICAL COMMIT
→ AUDIT
```

ACCEPTED ARTIFACT oznacza konkretną zaakceptowaną wersję i hash źródła, nie
odczyt zmiennego `latest`. Quality ACCEPT może uruchomić ekstrakcję; nie
zatwierdza pamięci ani chronionej mutacji. Status jakości pozostaje niezależny
od decyzji ekstrakcji, proposal i approval.

Ekstraktor i verifier wykonują odrębne wywołania, również przy tym samym modelu.
Reuse `verify_memory_extraction_candidate`: obie osie muszą mieć ACCEPT,
provenance i candidate_hash muszą pasować, eskalacja musi być nieaktywna.
REVISE/REJECT nie tworzą proposal. Obowiązuje istniejąca polityka limitu prób;
technical retry nie jest nową próbą merytorycznej ekstrakcji.

Candidate set i proposal są odrębnymi obiektami. Zapis zweryfikowanej ekstrakcji,
jeżeli występuje przed proposal, nie może zmieniać Canonical State ani nadawać
kanonicznego authority. Samo użycie commit_verified_memory_candidate nie jest
dowodem bezpiecznej promocji. Późniejsza integracja musi zachować ten warunek
bez drugiego systemu pamięci. Powiązany zestaw pozostaje jednostką walidacji
i promocji; nie wolno wybierać tylko wygodnej części. Każda rozbieżność między
zestawem a proponowanym stanem wymaga jawnej walidacji; zmiana treści ekstrakcji
wymaga nowego candidate hash i ponownej weryfikacji.

## 2. CanonicalChangeProposal

Minimalny logiczny kontrakt:

| Pole | Reguła |
| --- | --- |
| contract_version | Wersja schematu procesu i reguł hashowania |
| proposal_id | Stabilne, niepuste ID logicznej propozycji; zachowane przy retry i kolejnych wersjach tej propozycji |
| project_id, book_id | Jawne, odrębne tożsamości, zgodne z kontekstem wykonania i bindingiem repository |
| series_id | Jawne ID uprawnionej serii albo null; brak domyślnej serii |
| scope_type, scope_id | Dokładnie jeden PROJECT/project_id albo SERIES/series_id |
| run_id, step_id | Źródłowy ślad wykonania aktywnego P20 |
| source_artifact_id, source_artifact_ref | Stabilna tożsamość i rozwiązywalne odniesienie do dokładnego źródła |
| source_artifact_hash, source_artifact_version | Hash i wersja zaakceptowanego artefaktu; nie wywnioskowane z nazwy pliku |
| source_scene_id | ID źródłowej sceny, zgodne z candidate set |
| context_package_id, context_hash | ContextPackage użyty do wytworzenia źródłowego artefaktu |
| extraction_candidate_set_id, extraction_candidate_hash | Referencja do pełnego, niezmiennego zestawu; reuse candidate_id/candidate_hash |
| verification_ref | Trwała referencja i hash wyniku, wiążąca candidate ID/hash, source hash, niezależny verifier, obie osie i decyzję ACCEPT |
| proposed_mutations[] | Niepusty, kompletny zestaw mutacji w jednym scope |
| source, actor_ref | Pochodzenie i istniejąca tożsamość inicjatora; nie stanowią samodzielnego uprawnienia |
| authority_ref, policy_ref | Rozwiązywalne, wersjonowane podstawy oceny authority i reguł domenowych; bez domyślnego pozwolenia |
| proposal_version | Dodatnia wersja; zwiększana przy zmianie treści lub ponownym przygotowaniu po STALE |
| proposal_hash | SHA-256 zamrożonej treści według §3 |
| status | Stan według §10, zarządzany przez CanonService |
| created_at | Niezmienny czas utworzenia danej wersji w UTC |

Każda proposed mutation zawiera:

| Pole | Reguła |
| --- | --- |
| target_entity_type, target_entity_id | Typ zgodny z istniejącym ID domenowym i rekordem; ID nie zmienia się przy aktualizacji |
| operation_type | CREATE, UPDATE lub REPLACE zgodnie z MutationType; brak niejawnego upsert |
| expected_current_version | Wersja odczytanego celu; null wyłącznie dla CREATE wymagającego nieistnienia celu |
| expected_current_hash | Hash odczytanego stanu, wraz z protection; null tylko dla CREATE |
| proposed_state | Pełny, walidowalny stan docelowy wraz z wersją i jawnymi flagami ochrony |
| provenance | Źródłowy artefakt/wersja/hash, scena i referencje do elementów candidate set uzasadniających zmianę |

Jedna propozycja nie może zawierać dwóch mutacji tego samego celu. CREATE musi
sprawdzać nieistnienie; UPDATE/REPLACE muszą sprawdzać istnienie i dokładną
wersję bazową. Nowa wersja aktualizowanego rekordu musi być większa od obecnej;
REPLACE nie omija ochrony, historii ani wersjonowania. Nieobsługiwana operacja
jest odrzucana, nie tłumaczona na CREATE. Referencje między encjami są walidowane
w docelowym stanie całego zestawu, z zachowaniem integralności i reguł temporalnych.

## 3. Zamrożenie treści i proposal_hash

Zamrożenie propozycji jest niezmiennością procesu; nie zmienia domenowej flagi
`frozen`. Najpóźniej przed pierwszą analizą wpływu CanonService utrwala dokładną
treść wersji. Propozycja CREATED nie jest jeszcze dostępna do analizy/approval.

Hash obejmuje wszystkie pola treści z §2 oprócz `proposal_hash` i zmiennego
`status`. Obejmuje w szczególności wersję kontraktu, proposal_id/version,
identity, pełne mutations, expected versions/hashes, provenance, authority/policy
references i created_at. Późniejsze wyniki impact, guard, approval i commit są
odrębnymi dowodami związanymi z hashem; nie należą do jego preimage.

Serializacja: walidowany JSON, klucze sortowane, bez nieistotnych spacji,
separatory `,` i `:`, `ensure_ascii=True`, UTF-8, bez BOM i końcowego newline;
SHA-256 zapisany jako 64 małe znaki hex. NaN/Infinity, zduplikowane klucze JSON
i wartości spoza schematu są odrzucane. Nie normalizuje się po cichu tekstu
ani liczb. Reguły odpowiadają konwencji kanonicznego JSON istniejących rekordów.
Mutations są uporządkowane po `(target_entity_type, target_entity_id,
operation_type)`; pozostałe tablice zachowują zwalidowaną kolejność i są hashowane
dokładnie w niej. Referencje muszą wskazywać niezmienne wersje i hashe.

Zmiana któregokolwiek pola treści po zamrożeniu tworzy nową proposal_version
i proposal_hash. Stara wersja zostaje zachowana jako STALE, chyba że już ma
niezmienny wynik terminalny. Nowa wersja wymaga ponownej analizy, guarda i nowej
zgody, jeśli wymagana. Hash przelicza CanonService; hash dostarczony przez klienta
nie jest dowodem. Zmiana statusu nie zmienia hasha treści.

## 4. Impact Analysis i jej binding

Reuse GAP-011: `analyze_impact` i istniejący graf/repository. CanonService wiąże
wyniki dla każdego celu z całą propozycją; nie tworzy drugiego analizatora.
W tym pipeline analizowane są wszystkie proposal, a przed chronionym commitem
jest to bezwzględna bramka. Pusty wynik nie oznacza automatycznej zgody.

Dowód analizy zawiera: impact_id, result_hash, proposal_id/version/hash,
project_id, scope_type/id, analizowane typy/ID i operacje, policy_version,
parametry i coverage istniejącego analizatora, wynik oraz czas wykonania.
Zawiera też rozwiązywalny opis wersji/hash stanu użytego w analizie: celów,
ich ochrony i grafu/zależności wpływających na wynik. Są to metadane procesu
w istniejącym audycie, nie zmiana deklarowanego interfejsu ImpactResult.

BOUNDED_PROJECT_GRAPH nie stanowi pełnej analizy temporalnej ani dowodu braku
niezapisanych zależności. Ograniczenia pokrycia muszą być jawne. Jeżeli wymagany
zakres nie może zostać przeanalizowany, wynik jest niewystarczający i blokuje
commit; nie wolno oznaczać błędu lub ucięcia jako „brak wpływu”.

Finalna kontrola musi wykrywać również dodane/usunięte krawędzie, a nie tylko
zmiany wersji zwróconych encji. Może użyć spójnego snapshotu lub deterministycznej
ponownej analizy istniejącego grafu. Nie wolno zakładać istnienia globalnej
wersji grafu, której repository obecnie nie gwarantuje.

## 5. Jedyny DomainMutationGuard i authority

CanonService koordynuje proces, istniejący DomainMutationGuard jest jedynym
punktem egzekwowania ochrony. Kontrakt wymaga jego późniejszego rozszerzenia,
nie stworzenia CanonGuard/ApprovalGuard ani wyłączenia kontroli repository.
Obecne ALLOW/DENY nie są dowodem implementacji nowych reguł.

Logiczne wyniki oceny:

| Wynik | Znaczenie |
| --- | --- |
| ALLOW | Wszystkie warunki danego etapu spełnione; wstępny wynik nie uprawnia do zapisu |
| REQUIRE_USER_APPROVAL | Brakuje wyłącznie wymaganej, prawidłowo związanej zgody; brak prawa do commitu |
| DENY | Błąd integralności, scope, authority, nieobsługiwany zakres lub inna niespełniona reguła; approval nie omija tego wyniku |

Wejścia: zamrożona proposal, bieżące stany i wersje celów, niezależne frozen
i author_locked, ważny impact result, authority, policy i opcjonalny approval.
Wynik wskazuje proposal_hash, reason codes, wersję guarda/policy i warunki
spełnione/niespełnione dla całego zestawu.

| Ochrona celu | Warunek mutacji |
| --- | --- |
| frozen=false, author_locked=false | Jawne authority i wszystkie reguły domenowe; zgoda może być dodatkowo wymagana przez te reguły |
| frozen=true, author_locked=false | Formalne proposal → impact → user approval → wersjonowana zmiana → invalidation/rebuild → audit |
| frozen=false, author_locked=true | Zgoda autora/właściciela na dokładną zmianę, impact i pozostałe bramki |
| frozen=true, author_locked=true | Łączne spełnienie obu niezależnych ochron; jedna zgoda może obejmować obie dla całej propozycji |

Samo source=AUTHOR/ADMIN_CONTROLLED_OPERATION, confidence, quality score lub
MutationPolicy.allow_* nie zastępuje zgody. Zgoda na zmianę zachowuje flagi;
zdjęcie blokady wymaga jawnej mutacji flagi objętej tą samą zgodą i regułami.
Nie ma automatycznego unlock/unfreeze podczas commitu.

Minimalna reguła authority: inicjator może przygotować proposal; prawo finalnego
zapisu wynika z deterministycznej oceny istniejących zasad domenowych i uprawnień
w konkretnym scope. Origin i rola LLM nie nadają tego prawa. Brak rozwiązywalnej
podstawy authority oznacza DENY. Zgoda autora usuwa wymóg approval, ale nie
legalizuje błędnego scope, naruszenia schematu, konfliktu wersji ani niedozwolonej
operacji. Kontrakt nie wprowadza nowej hierarchii ról ani systemu użytkowników.

## 6. CanonicalChangeApproval

| Pole | Reguła |
| --- | --- |
| approval_id | Stabilne ID zdarzenia decyzji; ponowienie nie tworzy nowego |
| proposal_id, proposal_hash | Dokładna zamrożona propozycja; nie samo target entity ID |
| project_id, scope_type, scope_id | Identyczne jak w proposal; żadnej zgody globalnej |
| decision | APPROVE albo REJECT |
| approved_by | USER; jest klasyfikacją, nie dowodem uwierzytelnienia |
| actor_ref, authorization_ref | Dowód rzeczywistej decyzji uprawnionego autora/właściciela w istniejącym kontekście aplikacji |
| impact_id, impact_result_hash | Analiza przedstawiona przed decyzją i związana z tą propozycją |
| created_at | Czas decyzji UTC; decyzja następuje po impact i wstępnej ocenie guarda |

Backend musi sprawdzać autentyczność źródła zgody. Pole `approved_by=USER`
przesłane przez LLM/klienta nie wystarcza. Jeśli istnieje identity autora lub
właściciela, należy je wykorzystać. Jeśli nie da się dowieść uprawnionej decyzji
w istniejącym kontekście operatorskim, chroniony commit pozostaje zablokowany;
nie wolno tworzyć fikcyjnego użytkownika ani traktować admina technicznego jako
autora. Backend obsługuje proces bez UI; UI jedynie przekazuje decyzję.

Approval jest niezmiennym zdarzeniem. Dla danej wersji przyjmowana jest jedna
efektywna decyzja; identyczne ponowienie zwraca istniejący wynik, sprzeczna
decyzja nie nadpisuje historii. Zmiana decyzji po REJECT wymaga nowej wersji
proposal i ponownego procesu. Nie ma dziedziczenia approval między wersjami,
scope, projektami ani różnymi zmianami tej samej encji.

### 6.1. ACCEPTED — lokalny operator, uzupełnienie v1.1 z 2026-09-15

Jawna decyzja użytkownika z 2026-09-15 dopuszcza dedykowane poświadczenie
lokalnego operatora. Uzupełnienie usuwa brak źródła authorization_ref opisany
w historycznym przeglądzie v1.0. Akceptacja v1.0 z 2026-09-15 i jej historia
pozostają zachowane; nie wymaga się kolejnego ADR ani rundy akceptacji.

Granica zaufania: prywatna instalacja jednego operatora. Zaufana lokalna
inicjalizacja tworzy losowy token przez secrets.token_urlsafe(32), czyli co
najmniej 32 bajty entropii. Poprawna weryfikacja tokenu stanowi dowód tożsamości
operatora tej instalacji. Nie dowodzi fizycznej obecności człowieka i nie
chroni przed administratorem Windows ani dowolnym kodem działającym z pełnymi
uprawnieniami konta operatora. DPAPI CurrentUser nie izoluje od wszystkich
procesów tego samego użytkownika. Localhost, USER, approved=true, model,
project_id i SeriesAccessContext nie są poświadczeniami.

Metadane operatora należą do istniejącego agentpro_system.db przez
SystemRepository: operator_id, credential_id, SHA-256 tokenu, wersja, aktywność,
czas utworzenia/unieważnienia. Porównanie skrótów używa secrets.compare_digest.
Treść proposal, książki i dowodu decyzji nie trafia do bazy systemowej.

Sekret klienta jest szyfrowany rzeczywistym Windows DPAPI CurrentUser i domyślnie
zapisany w `%LOCALAPPDATA%\AgentPRO\Security\operator.dpapi`. Katalog posiada
chronioną DACL wyłącznie dla bieżącego użytkownika; nowy plik dziedziczy ją.
Ścieżka sekretu musi znajdować się poza repozytorium i storage książek, bez
reparse points. Parametr ścieżki umożliwia izolowane testy. Sekret nie jest
argumentem CLI, zmienną środowiskową, elementem URL, payloadu modelu ani audytu.

Cykl życia jest dostępny wyłącznie jako jawne polecenia lokalne narzędzia
`python -m app.operator_cli`: init, rotate, revoke. Import i start serwera nie
inicjalizują operatora. Init odrzuca istniejącego operatora i istniejący plik
sekretu; nie ma anonimowego endpointu bootstrap/reset. Rotate i revoke wymagają
odczytu aktualnego DPAPI i ponownej weryfikacji aktywnego tokenu w bazie.
Unieważnienie jest trwałe; nie umożliwia automatycznej ponownej inicjalizacji.

Rotacja zachowuje nieaktywną wersję poświadczenia w bazie. Nowy zaszyfrowany
plik `.pending` powstaje przed zmianą aktywnego skrótu, a zastępuje plik klienta
po zatwierdzeniu DB. Błąd lub przerwanie tej lokalnej procedury jest jawne,
nie uruchamia resetu ani fallbacku. Osierocony plik lub nierozstrzygnięty wynik
wymaga zaufanej lokalnej diagnostyki; narzędzie nie deklaruje globalnej transakcji
DB+plik. Nie jest to protokół F-004 dotyczący pamięci literackiej.

HTTP przyjmuje poświadczenie wyłącznie w Authorization: Bearer. Operacje
`/operator/*` sprawdzają aktywne poświadczenie, loopback adresu klienta i Host.
Klient operatorski łączy się wyłącznie z numerycznym adresem loopback HTTP,
wyłącza proxy i przekierowania. Instalację uruchamia się z `--host 127.0.0.1`.
Ograniczenie sieciowe jest dodatkowe względem uwierzytelnienia. Brak konfiguracji
zwraca OPERATOR_NOT_CONFIGURED (503); zły/brak/nieaktywny token zwraca 401 po
skonfigurowaniu operatora. Nie ma wildcard CORS ani anonimowego odpowiednika.

Operator może zarządzać projektami z istniejącego rejestru project/book tej
instalacji. Backend wyznacza book_id z rejestru; nie ufa zadeklarowanym prawom
klienta. Propozycja PROJECT powiązana z serią zachowuje kontrolę członkostwa przez
SeriesAccessContext. Niniejsza minimalna ścieżka przechowywania/review dotyczy
PROJECT; nieobsługiwane proposal SERIES są odrzucane, bez fallbacku.

Odczyt zapisanej propozycji jest oddzielony od przygotowania i decyzji:

- GET `/operator/projects/{project_id}/proposals/{proposal_id}` pokazuje pełne
  mutations, zakres, wersje, impact i zapisany stan decyzji; nie wydaje zgody.
- POST tej ścieżki z `/review` tworzy krótkotrwałe jednorazowe wyzwanie związane
  z operatorem, credential_id/version, proposal/hash oraz podstawą stanu projektu.
- POST z `/decision` przyjmuje wyłącznie proposal_hash, scope_type/id,
  challenge_id i jawną decyzję APPROVE/REJECT. Pozostałe pola są odrzucane.

Ważność wyzwania: `AGENTPRO_OPERATOR_CHALLENGE_TTL_SECONDS`, domyślnie 300 sekund,
dozwolony zakres 1–900 sekund. Wadliwa konfiguracja blokuje przygotowanie.
Stan bazowy obejmuje rekordy i krawędzie projektu; zmiana bazy lub nowa wersja
proposal wymaga nowego przeglądu. Skrót klienta jest porównywany z zapisanym
obiektem, a jego zawartość weryfikowana ponownie. Decyzja oraz zużycie wyzwania
są zapisywane atomowo w project.db przy propozycji. Weryfikowane poświadczenie
i rejestr systemowy pozostają stabilne do zakończenia tego zapisu; operacja
wydania decyzji nie mutuje jednocześnie obu baz.

Backend tworzy authorization_ref, approval_id, operator_id, credential_id/version,
project/scope, proposal_id/hash, decision, authentication_method,
challenge_id, created_at i powiązanie z impact. Jest to trwały, audytowalny dowód,
nie token uprawniający do innych działań. Identyczny retry zwraca wcześniejszy
dowód; sprzeczna decyzja albo użycie wyzwania do innej propozycji jest odrzucane.
Rotacja uniemożliwia użycie starych wyzwań; unieważnienie blokuje nowe decyzje.
Historyczne dowody pozostają dostępne w repository.

Minimalny zapis propozycji w CanonService przyjmuje niezmienny obiekt zgodny
ze schematem v1.0 i powiązane dowody impact/wstępnej oceny od przyszłego producenta
pipeline. Nie jest endpointem HTTP ani narzędziem modelu. Ta implementacja nie
wykonuje ekstrakcji, nie tworzy drugiego modelu proposal ani guarda i nie
poświadcza jeszcze kompletności upstream F-002/F-006. Wynik przyjęcia decyzji
ma canonical_commit=false. Zgoda nie zastępuje DomainMutationGuard, authority,
aktualnej Impact Analysis ani końcowej walidacji opisanej w §7.

Narzędzie operatorskie review pokazuje pełną propozycję; decide przygotowuje
przegląd i pyta w konsoli o APPROVE albo REJECT. Nie wymaga kopiowania tokenu.
Token i plik DPAPI nie są udostępnione w rejestrze TOOLS P20. API nie przekazuje
nagłówka Authorization do runtime, ContextPackage ani providera. Nie oznacza
to, że wszystkie wcześniejsze ścieżki zapisu kanonu zostały zabezpieczone.

Pomoc i cykl życia, uruchamiane jawnie w PowerShell przez operatora:

```powershell
python -m app.operator_cli --help
python -m app.operator_cli init
python -m app.operator_cli rotate
python -m app.operator_cli revoke
```

Podkomendy review/decide wymagają `--project` i `--proposal` z istniejących danych.
W tym zadaniu init/rotate/revoke są wykonywane wyłącznie na danych syntetycznych
w izolowanej bazie i ścieżce; rzeczywisty operator nie jest inicjalizowany.

## 7. Final guard validation i TOCTOU

Bezpośrednio przed zapisem CanonService ponownie wywołuje ten sam guard i
potwierdza:

1. Stan proposal dopuszcza próbę commitu, a przeliczony proposal_hash jest zgodny.
2. Artefakt, candidate i verification są nadal dostępne w dokładnie związanych wersjach.
3. Target versions/hashes, nieistnienie celu CREATE i protection state są zgodne.
4. Impact ma właściwe identity/hash i nadal aktualną podstawę analizy.
5. Authority, policy i dostęp do scope są nadal ważne.
6. Wymagany APPROVE pochodzi od uprawnionego USER i pasuje do proposal oraz impact.
7. Cały zestaw spełnia schemat, referential/temporal integrity i conflict detection.

Zmiana stanu istotnego dla analizy lub zgody oznacza STALE i brak zapisu.
Wymagane jest odświeżenie bazowych wersji, nowa proposal_version/hash, analiza,
ocena i zgoda. Nawet niezmieniona treść mutations nie pozwala przenieść starej
zgody na nowy kontekst. Zmiana samej ekstrakcji wraca również do verifiera.

Końcowy odczyt/kontrola oraz zapis muszą być chronione tą samą granicą transakcji
w pojedynczej DB albo równoważnym sprawdzanym warunkiem wersji. Luźne sprawdzenie
przed otwarciem transakcji pozostawia TOCTOU i jest niedopuszczalne. Konflikt
współbieżności wycofuje cały zestaw; techniczny lock nie zastępuje ochrony domenowej.

## 8. Commit, spójność i granica PROJECT / SERIES

Jedna proposal obejmuje jeden `(scope_type, scope_id)` i jeden logiczny store.
PROJECT używa ProjectRepository/project.db, SERIES używa SeriesRepository/series.db
oraz jawnego SeriesAccessContext i członkostwa. project_id pozostaje tożsamością
inicjującego projektu, nie aliasem series_id ani book_id. Brak zgodności blokuje
odczyt i zapis; nie wolno odpytywać obcego project.db.

Obecny brak SERIES graph analysis oznacza FAIL CLOSED dla wymagającej jej
operacji SERIES. Nie ma PROJECT fallback. Propozycja obejmująca wiele store
jest poza tym kontraktem i nie może być dzielona na części pozornie atomowej
promocji powiązanego candidate set.

CanonService przygotowuje zwalidowaną mutację, repository wykonuje fizyczny
zapis. W jednej DB: cały zestaw nowych wersji, lokalne oznaczenia invalidation,
wiążący wynik operacji i wymagany audyt commitu muszą zostać zapisane atomowo.
Nie można uznać części encji za COMMITTED. Historia zatwierdzonych wersji i
provenance musi pozostać odtwarzalna; nie wystarcza nadpisanie bieżącego payloadu.

Dane pochodne dotknięte zmianą są jawnie unieważnione i odbudowywane według
istniejących odpowiedzialności. Do odbudowy nie wolno przedstawiać ich jako
aktualnych. COMMITTED dotyczy zatwierdzonego stanu domenowego; audyt oddzielnie
podaje stan invalidation/rebuild, bez deklaracji zakończenia całego workflow.

project.db + series.db + pliki nie są jedną transakcją. Zatwierdzony artefakt jest
niezmiennym wejściem z rozwiązywalną wersją/hash, a nie plikiem nadpisywanym w
tej transakcji. Wymagane dodatkowe zapisy poza DB, cross-store propagation,
outbox i recovery należą do **F-004 — FUTURE / OUT OF SCOPE**. Jeżeli konkretna
operacja wymaga tych gwarancji do zachowania spójności, należy ją zablokować,
a nie ogłaszać sukces lokalnego commitu jako pełny sukces między store.

## 9. Operation identity i idempotencja

To uszczegółowienie wspólnej operation identity z Architektury §102 i ADR §25.
Nie powstaje konkurencyjny mechanizm. Logiczny klucz canonical operation obejmuje
`project_id, scope_type, scope_id, proposal_id, proposal_hash, operation_type`;
śladem wejścia są również `run_id, step_id, input_artifact_hash`.

Przed istnieniem proposal ID deduplikacja przygotowania wykorzystuje identity
zaakceptowanego artefaktu/wersji/hash, projektu/scope, źródłowego run/step oraz
typu operacji. Pierwszy utrwalony wynik wiąże tę identity z candidate i proposal ID.
Powtórna dostawa tego samego zdarzenia odtwarza binding; nie losuje nowego ID.
Nowa merytoryczna próba ma jawny numer próby i nie udaje technical retry.

Po zamrożeniu ten sam klucz i payload zwracają istniejący wynik proposal,
approval lub commitu. Ten sam klucz z inną treścią jest konfliktem integralności.
Approval retry dodatkowo wiąże approval_id i treść decyzji; nie tworzy drugiej
decyzji autora. Wynik commitu wskazuje zapisane resulting versions, nie ponawia
wersjonowania. Współbieżne retry muszą spełniać te same gwarancje.

Lokalny zapis wyniku operation identity i skutków commitu musi być atomowy
z mutacjami w tej samej DB. Po niepewnym wyniku najpierw odczyt tego dowodu:
COMMITTED → zwróć wynik; potwierdzony brak commitu → ponów z tą samą identity
po final validation; wynik nierozstrzygalny → brak ponownego zapisu i eskalacja.
Nie wolno wnioskować o braku commitu z braku odpowiedzi HTTP lub logu plikowego.
Nie deklaruje to gotowego cross-store recovery/outbox F-004.

## 10. Minimalna maszyna stanów

Status dotyczy wersji proposal, nie jakości sceny ani verification status.

| Status | Znaczenie i dozwolony następny krok |
| --- | --- |
| CREATED | Powstał odrębny obiekt po ACCEPT ekstrakcji; walidacja schematu, identity, provenance i freeze → READY_FOR_ANALYSIS |
| READY_FOR_ANALYSIS | Treść zwalidowana i zamrożona; impact + guard → AWAITING_USER_APPROVAL albo APPROVED_FOR_COMMIT |
| AWAITING_USER_APPROVAL | Ważny impact i REQUIRE_USER_APPROVAL; ważne APPROVE → APPROVED_FOR_COMMIT; REJECT → REJECTED |
| APPROVED_FOR_COMMIT | Wstępne bramki spełnione; tylko final guard ALLOW i atomowy zapis → COMMITTED |
| REJECTED | Guard DENY albo USER REJECT; brak commitu tej wersji |
| COMMITTED | Trwały wynik i resulting versions; terminalny, retry tylko odczytuje wynik |
| STALE | Treść/bazowy stan/dowody nieaktualne; brak commitu; nowa wersja zaczyna w CREATED |
| FAILED | Błąd techniczny lub nieprawidłowy kontrakt; zapis zabroniony do rozstrzygnięcia przyczyny i wyniku |

CREATED/READY_FOR_ANALYSIS/AWAITING_USER_APPROVAL/APPROVED_FOR_COMMIT mogą przejść
do STALE, REJECTED lub FAILED odpowiednio do przyczyny. Z FAILED wolno wznowić
przerwany etap wyłącznie po potwierdzeniu braku commitu i ważności jego wejść;
nie wolno przeskoczyć bramek. Nierozstrzygnięty commit pozostaje FAILED z reason
COMMIT_OUTCOME_UNKNOWN; nie jest dowodem rollbacku. Odnaleziony trwały wynik
jest odtwarzany jako COMMITTED. REJECTED/STALE zachowują historię; ponowne
przygotowanie używa nowej wersji. Status sam w sobie nigdy nie zastępuje dowodów.

## 11. Failure / retry

| Sytuacja | Wymagane zachowanie |
| --- | --- |
| Verification REJECT / nieudana oś | Brak proposal i canonical commit; zachowaj powód i niezależny status jakości |
| Verification REVISE | Ograniczona pętla ekstrakcji, nowa próba i weryfikacja; po limicie eskalacja |
| Impact FAIL / niewystarczający zakres | FAILED, bez commitu; retry tylko dla błędu przejściowego, bez podmiany dowodów starej zgody |
| Guard DENY | REJECTED z przyczyną; zgoda ani retry nie omijają odmowy |
| Brak approval | AWAITING_USER_APPROVAL, bez commitu; brak timeoutu oznaczającego zgodę |
| USER REJECT | REJECTED, bez zmiany Kanonu |
| Stale proposal / impact / approval | STALE, odświeżenie i nowa wersja/hash, analiza, guard i wymagana zgoda |
| Version conflict podczas commitu | Rollback całego zestawu, STALE, bez częściowej promocji |
| Technical retry po niepewnym commicie | Odczytaj wynik operation identity; nie zapisuj drugi raz bez rozstrzygnięcia |
| Błąd audytu w transakcji | Rollback razem z mutacjami; brak sukcesu bez trwałego dowodu |
| Awaria eksportu audytu/rebuild poza DB | Nie cofaj fikcyjnie potwierdzonego commitu; raportuj rzeczywisty stan i granicę F-004 |

## 12. Audit lineage

Reuse istniejących fundamentów audytu CanonService i właściwego repository;
bez nowego Audit Engine. Końcowe AUDIT w pipeline domyka historię, lecz każdy
etap, także odrzucenie, ma ślad od chwili wykonania. Proposal audit record należy
do project.db albo series.db zgodnie ze scope; plikowy opis nie zastępuje dowodu.

Minimalny odtwarzalny łańcuch:

```text
project_id / book_id / series_id / scope / run_id / step_id
→ accepted artifact ID / version / hash
→ context_package_id / context_hash
→ candidate set ID / hash / extraction attempt / extractor invocation
→ verification ref / hash / verifier invocation / precision / completeness
→ proposal_id / proposal_version / proposal_hash
→ impact_id / result_hash / coverage / analysis state evidence
→ initial guard decision / reason / guard-policy versions / authority result
→ approval_id / decision / actor evidence / binding (lub reason NOT_REQUIRED)
→ final guard decision / checked versions / protection / timestamp
→ operation identity / commit result / resulting entity versions
→ invalidation i rebuild status
```

Ślad zawiera wartości i rozwiązywalne referencje do niezmiennych dowodów, nie
tylko swobodny tekst. Rejection reason, błędy, przejścia stanów i konflikty są
utrwalone bez nadpisania wcześniejszych decyzji. Retry nie tworzy drugiego
logicznego zdarzenia ekstrakcji, approval ani commitu; odrębne obserwacje próby
technicznej wskazują tę samą operation identity.

## 13. Wymagany functional proof późniejszej implementacji

F-002 / F-006 pozostają NOT CLOSED bez izolowanego scenariusza aktywnego P20:
projekt → Book Bible → scena → `/agent/step` → ACCEPTED artifact → extraction
→ niezależny verifier → proposal → freeze/hash → impact → guard → wymagane
approval → final guard → canonical commit. Provider może być adapterem testowym;
cała ścieżka runtime do providera i po wyniku musi być produkcyjna.

Dowody obowiązkowe:

1. ACCEPT uruchamia ekstrakcję rzeczywistej wersji źródła; verifier ma inne call_id.
2. Candidate i ACCEPT extraction nie zmieniają Kanonu; proposal powstaje dopiero po poprawnej weryfikacji obu osi.
3. Pełny powiązany zestaw jest promowany atomowo, błąd jednej encji wycofuje całość.
4. Impact poprzedza wymaganą zgodę i chroniony commit; oba wywołania guarda mają ślad.
5. Chroniona mutacja bez approval nie jest zapisana; polityka/model/admin nie zastępują zgody.
6. Approval hash A + zmieniona proposal hash B nie pozwalają na commit.
7. Zmiana target version, protection albo istotnej krawędzi po impact/approval blokuje commit.
8. USER REJECT, verification REJECT/REVISE, błędne provenance i niedozwolony stan nie promują danych.
9. Dozwolona zmiana ze zgodą zapisuje wersje, zachowuje ochronę i spójność pamięci/Kanonu.
10. Reopen odtwarza proposal, approval, wynik operacji i audit lineage.
11. Technical retry, również współbieżny i po niepewnej odpowiedzi, nie duplikuje ekstrakcji/proposal/approval/commitu.
12. Projekt B pozostaje niewidoczny; cudze approval i scope są odrzucane; nieobsługiwany SERIES jest FAIL CLOSED.
13. Unieważnienie danych pochodnych jest jawne, a stan rebuild nie jest przedstawiony jako ukończony bez dowodu.
14. Fingerprint PRE/POST neutralnego, izolowanego storage testowego dla TEST_PROJECT_A / TEST_BOOK_A pozostaje identyczny; żaden rzeczywisty projekt książkowy ani zewnętrzny storage nie jest odczytywany, indeksowany ani hashowany, a zastane zmiany są zachowane.

Wymagane są testy celowane, odpowiednie contract/integration tests, functional
API/P20 proof oraz pełna regresja. Same zielone unit tests nie zamykają findings.
Nieprzeprowadzony dowód jest oznaczany NOT VERIFIED.

## 14. Poza zakresem i warunki akceptacji

Poza zakresem: implementacja F-002/F-006, F-004 cross-store recovery/outbox,
GAP-014, nowy UI, nowy system użytkowników, storage, runtime, verifier, guard,
CanonService, Impact Analysis i Audit Engine. Dokument nie projektuje SQL,
migracji ani endpointów. P20.x pozostaje jedynym runtime produkcyjnym;
odpowiedzialności MODE/ROLE/PRESET, quality, pamięci i storage pozostają oddzielne.

Otwarte decyzje dotyczące akceptacji: jawne przyjęcie niniejszego kontraktu
PROPOSED, w tym stanów, hash preimage, bindingu zgody i lokalnej idempotencji.
Nie zidentyfikowano konfliktu z dokumentami nadrzędnymi w zakresie tego kontraktu.

Warunki późniejszej implementacji, nie deklaracje stanu obecnego: wskazać
w istniejącej granicy operatorskiej sposób dowodzenia decyzji uprawnionego
autora, rozszerzyć istniejącego guarda i persistence dowodów przez repository,
wykazać atomowość lokalnego wyniku oraz aktualność impact. Brak dowodu to blokada
chronionego commitu. Ograniczenie SERIES pozostaje jawnym FAIL CLOSED; nie
wymaga wymyślenia zastępczego algorytmu w celu akceptacji tego dokumentu.

Utworzenie tego pliku nie usuwa blokera implementacji automatycznie: dopiero
akceptacja kontraktu dostarcza uzgodnionej podstawy do osobnego zadania F-002/F-006.

F-002 = NOT STARTED
F-006 = NOT STARTED
F-004 = NOT STARTED
GAP-014 = NOT STARTED
