# ADR-00XX — NARRATIVE STATE ENGINE

## Status

ACCEPTED

## Data

2026-09-12

## Data akceptacji

2026-09-14

## Decydenci

Architektura AgentPRO

## Powiązane dokumenty

- MASTER\_CANON\_AGENTPRO v2
- ARCHITEKTURA\_AGENTPRO v1.2
- ADR-0001
- przyszłe kontrakty danych Narrative State Engine
- przyszłe kontrakty procesu Canon Change
- kontrakty Context Buildera

---

# 1. CEL DECYZJI

AgentPRO otrzymuje natywną warstwę trwałego, wersjonowanego i temporalnego stanu narracyjnego:

NARRATIVE STATE ENGINE.

Warstwa ta ma zapewniać jednocześnie:

- pełną historię zmian,
- aktualny obowiązujący stan świata powieści,
- wersjonowanie faktów,
- temporalność,
- provenance,
- historię retconów,
- relacje,
- stan wiedzy postaci,
- stan wiedzy czytelnika,
- wykrywanie sprzeczności,
- ochronę decyzji autora,
- kontrolowaną materializację Kanonu,
- deterministyczne źródło danych dla Context Buildera określonego w Architekturze.

Narrative State Engine nie jest pamięcią modelu LLM.

LLM pozostaje wymiennym, disposable executorem.

Źródłem prawdy pozostaje trwały stan projektu/serii zarządzany przez AgentPRO.

---

# 2. KONTEKST

AgentPRO wymaga pamięci znacznie silniejszej niż:

- historia rozmów,
- semantic search,
- RAG,
- embedding retrieval,
- streszczenia sesji,
- pliki kontekstowe,
- zwykły append-only log.

W domenie powieści system musi rozróżniać co najmniej:

1. co kiedykolwiek zostało zapisane,
2. co było prawdą w określonym momencie,
3. co jest prawdą obecnie,
4. która wersja faktu została zastąpiona,
5. która zmiana była retconem,
6. kto zatwierdził zmianę,
7. z jakich źródeł zmiana wynika,
8. która postać zna określony fakt,
9. która postać tylko w niego wierzy,
10. co wie czytelnik,
11. jakie elementy są zablokowane przez autora,
12. jakie sprzeczności pozostają nierozstrzygnięte.

Samo:

LOSSLESS CAPTURE + RETRIEVAL

nie wystarcza.

AgentPRO wymaga:

LOSSLESS HISTORY
\+
CANONICAL STATE
\+
TEMPORAL STATE
\+
KNOWLEDGE STATE
\+
PROVENANCE
\+
CONFLICT CONTROL
\+
DETERMINISTIC CONTEXT DELIVERY.

---

# 3. GŁÓWNA DECYZJA

Narrative State Engine jest natywną częścią istniejącej architektury AgentPRO.

Nie powstaje:

- osobny produkt,
- osobny runtime,
- osobny system pamięci,
- osobna baza pamięci,
- równoległy backend,
- osobny `agentpro-memory`.

Warstwa działa w istniejącym układzie:

ProjectStorageContext / SeriesStorageContext
↓
ProjectRepository / SeriesRepository
↓
project.db / series.db

Logika domenowa działa ponad Repository:

P20.x
↓
Domain Services
↓
CanonService
↓
ProjectRepository / SeriesRepository
↓
project.db / series.db

`CanonService` NIE jest częścią Repository.

Repository odpowiada za persistence.

CanonService odpowiada za reguły domenowe.

Opis Narrative State Engine określa TARGET ARCHITECTURE, nie deklaruje kompletności CURRENT IMPLEMENTATION. Na HEAD 0503ea57f338ea7e569aec353f5469548a678908 GAP-001..GAP-010 są zamkniętymi fundamentami. Ten ADR je rozszerza, nie otwiera ponownie i nie stanowi polecenia implementacji.

---

# 4. GRANICE STORAGE

Narrative State Engine respektuje ADR-0001.

Dane projektu należą do:

project.db

Dane współdzielone przez serię należą do:

series.db

Dane systemowe pozostają w:

agentpro\_system.db

Narrative State Engine nie tworzy nowych baz poza tym podziałem.

Nie istnieje niejawna globalna pamięć narracyjna.

Każdy rekord posiada jawny scope:

PROJECT

lub:

SERIES.

---

# 5. IMMUTABLE MEMORY LEDGER

Docelowy Memory Ledger rozszerza istniejący fundament pamięci GAP-007 jako append-only historia zatwierdzonych zdarzeń i zmian, z provenance oraz operation identity.

Zatwierdzenie zapisu zdarzenia historycznego nie oznacza zatwierdzenia jego treści jako faktu kanonicznego. Rejestracja odrzucenia proposal lub wygenerowania kandydata nie promuje kandydata do Kanonu.

Logiczna nazwa:

MemoryEventRecord

Identyfikator:

memory\_event\_id

Nie należy utożsamiać `MemoryEventRecord` z fabularnym `EventRecord`.

`EventRecord` reprezentuje wydarzenie świata powieści.

`MemoryEventRecord` reprezentuje zdarzenie systemowej historii pamięci.

Przykładowe Memory Events:

- decyzja autora,
- import źródła,
- zaakceptowanie rozdziału,
- wygenerowanie nowego faktu,
- zmiana faktu,
- retcon,
- zmiana relacji,
- zmiana timeline,
- zmiana wiedzy postaci,
- Human Edit,
- zatwierdzenie Canon Change,
- odrzucenie Canon Change,
- import researchu,
- zapis provenance,
- zmiana blokady autora.

Minimalna semantyka MemoryEventRecord obejmuje:

- memory\_event\_id,
- operation identity,
- scope\_type,
- scope\_id,
- timestamp,
- actor,
- event\_type,
- structured\_payload,
- parent\_refs,
- artifact\_refs,
- source\_refs,
- content\_hash,
- created\_at.

Ledger jest append-only.

Istniejącego zdarzenia nie nadpisujemy w celu zmiany historii.

Zmiana tworzy kolejne zdarzenie.

---

# 6. LOSSLESS CAPTURE A HYBRYDOWY STORAGE

„Pełny zapis źródłowy” nie oznacza:

wszystkie PDF-y, screenshoty, rozdziały i duże binaria jako BLOB-y w `project.db`.

AgentPRO zachowuje istniejący model hybrydowego storage.

Małe dane strukturalne mogą być przechowywane bezpośrednio.

Duże lub naturalnie plikowe źródła pozostają artefaktami.

Memory Ledger zapisuje wtedy co najmniej:

artifact\_ref
\+
content\_hash
\+
source\_ref
\+
metadata.

Przykład:

PDF
→ Artifact Storage

MemoryEventRecord
→ artifact\_ref
→ hash
→ provenance.

Dzięki temu zachowujemy lossless provenance bez zamiany SQLite w magazyn wszystkich binariów.

---

# 7. MATERIALIZED NARRATIVE STATE

Obowiązujący stan narracyjny jest materializowany ze źródłowej historii i zatwierdzonych zmian.

Istniejący fundament GAP-007 pozostaje bazą pamięci. Materialized State jest deterministyczną projekcją Ledgera i zatwierdzonych operacji, a nie drugim, niezależnie edytowalnym źródłem prawdy. Dla tych samych zatwierdzonych zdarzeń i wersji reguł projekcji powstaje ten sam stan domenowy.

FactRecord, CharacterState, KnowledgeEvent, EventRecord, ThreadRecord, SetupRecord i PayoffRecord zachowują swoje role domenowe w tym jednym modelu: ich zatwierdzone utworzenia i zmiany są reprezentowane w historii, a obowiązujące wersje i stan są udostępniane przez projekcję. KnowledgeEvent i EventRecord nie stają się MemoryEventRecord; ledger rejestruje operacje dotyczące tych encji.

Nie istnieje równoległy, konkurencyjny model zapisu pamięci. Projekcji nie edytuje się poza zatwierdzoną ścieżką operacji domenowych. Odtworzenie projekcji nie tworzy nowych decyzji kanonicznych. Sposób migracji istniejących danych wymaga późniejszego kontraktu, bez uruchamiania drugiego ownera pamięci.

Logicznie obejmuje co najmniej:

- entities,
- facts,
- fact\_versions,
- relations,
- timeline,
- character knowledge,
- reader knowledge,
- provenance,
- conflicts,
- author decisions,
- protection state.

Dokładny schema SQL NIE jest przedmiotem tego ADR.

ADR ustala model odpowiedzialności.

---

# 8. ROZDZIELENIE ORIGIN, AUTHORITY, PROTECTION I CANON STATE

AgentPRO nie używa jednej prostej „drabiny authority”, która miesza pochodzenie danych, ich zatwierdzenie i ochronę.

Są to oddzielne pojęcia.

## 8.1 ORIGIN

Origin opisuje:

SKĄD INFORMACJA POCHODZI.

Przykładowe klasy:

RAW\_EVENT

RESEARCH\_CLAIM

INFERRED\_FACT

GENERATED\_FACT

USER\_PROVIDED\_FACT.

Origin opisuje pochodzenie informacji, nie wynik zatwierdzenia. Approval, authority i canon state są oddzielnymi wymiarami. Provenance nie zastępuje approval ani nie nadaje authority.

Dokładny enum zostanie określony w kontrakcie danych.

---

## 8.2 AUTHORITY

Authority opisuje:

KTO LUB JAKI ZATWIERDZONY PROCES MA PRAWO USTANOWIĆ ZMIANĘ KANONICZNĄ.

LLM ani Agent nie posiada samodzielnego prawa do finalnego ustanawiania Kanonu.

LLM może proponować zmianę.

Finalny commit wymaga spełnienia polityki authority.

Dokładne poziomy authority zostaną ustalone w kontrakcie procesu.

ADR zamraża zasadę:

LLM / AGENT
→ PROPOSAL ONLY

CANON SERVICE + POLICY
→ DECISION / COMMIT.

---

## 8.3 PROTECTION

Ochrona jest osobnym wymiarem.

Co najmniej:

frozen

author\_locked.

`author_locked=true` oznacza:

NIE ZMIENIAJ BEZ ZGODY AUTORA.

`frozen=true` oznacza:

FAKT NIE MOŻE ZOSTAĆ CICHO ZMIENIONY PRZEZ MODEL.

`AUTHOR_LOCK` nie jest poziomem authority.

Jest mechanizmem ochrony domenowej.

Narrative State Engine używa jedynego istniejącego DomainMutationGuard z GAP-009. Nie powstaje konkurencyjny guard. Authority, protection, canon state, wynik Impact Analysis i wymagane approval są wejściami do tej samej ścieżki ochrony, koordynowanej przez CanonService.

Dla chronionej zmiany Impact Analysis musi poprzedzać canonical commit. Jeżeli nadrzędne reguły wymagają zgody użytkownika, zgoda musi istnieć przed commitem i dotyczyć zatwierdzanej zmiany. W szczególności frozen zachowuje formalny proces proposal → Impact Analysis → user approval → wersjonowana zmiana → invalidation/rebuild → audit, a author_locked wymaga zgody autora. Flagi pozostają niezależne.

Policy, model ani confidence nie mogą zastąpić wymaganej zgody użytkownika. Wstępna ocena guarda nie jest uprawnieniem do zapisu; przed commitem ścieżka ochrony musi potwierdzić spełnienie wszystkich warunków, w tym wymaganego approval.

---

## 8.4 CANON STATE

Stan wersji faktu jest osobnym wymiarem.

Minimalnie:

ACTIVE

SUPERSEDED

RETCONNED.

Możliwe dodatkowe statusy mogą zostać określone w kontrakcie danych.

---

# 9. RETCON

Retcon nie jest poziomem authority.

Retcon jest operacją domenową.

Operacja retconu musi jawnie wskazywać:

- istniejący fakt lub wersję,
- nową wersję,
- źródło zmiany,
- authority,
- reason,
- provenance,
- zależność supersedes,
- wpływ temporalny.

Obowiązujący stan wynika co najmniej z:

- poprawnego authority,
- version ordering,
- valid\_from,
- valid\_to,
- jawnego supersedes,
- jawnego superseded\_by,
- reguł ochrony,
- reguł scope.

Późniejszy poprawnie zatwierdzony retcon może zastąpić wcześniejszy APPROVED CANON.

Nie dzieje się to dlatego, że „RETCON ma wyższy rank”.

Dzieje się dlatego, że jest poprawną nową wersją istniejącego stanu.

---

# 10. CANONICAL CHANGE PROPOSAL

Żaden LLM ani Agent nie zapisuje bezpośrednio do:

- Facts,
- Relations,
- Timeline,
- Character Knowledge,
- Reader Knowledge,
- Canonical State.

Jedyny dozwolony flow:

LLM / Agent / Human Edit / System Operation
↓
CanonicalChangeProposal
↓
Deterministic Validation
↓
Authority + Policy Gate
↓
CanonService
↓
Repository
↓
Persistent Commit.

CanonicalChangeProposal jest propozycją.

Nie jest jeszcze Kanonem.

## 10.1 EXTRACTION CANDIDATE != CANONICAL CHANGE PROPOSAL

StructuredMemoryExtractionCandidate z GAP-008 nie jest CanonicalChangeProposal. Dla danych pochodzących z ekstrakcji obowiązuje:

EXTRACTION
→ CANDIDATE SET
→ VERIFIER
→ PRECISION / COMPLETENESS
→ ACCEPT EXTRACTION
→ CANONICAL CHANGE PROPOSAL
→ IMPACT ANALYSIS
→ DOMAIN MUTATION GUARD
→ REQUIRED USER APPROVAL, jeśli dotyczy
→ CANONICAL COMMIT.

Ten przepływ uszczegóławia bramki domenowe ogólnego flow; CanonService koordynuje walidację i commit przez Repository, nie powstaje drugi owner. ACCEPT ekstrakcji oznacza wyłącznie poprawność ekstrakcji względem źródła, nigdy automatyczną promocję do Kanonu.

Gwarancje GAP-008 pozostają obowiązujące: verifier jest logicznie odrębną rolą/wywołaniem od extractora, osobno ocenia PRECISION i COMPLETENESS, weryfikuje provenance sceny/artefaktu, a ekstrakcja ma własne ACCEPT / REVISE / REJECT, limit prób i eskalację. memory_extraction_status jest niezależny od scene_quality_status.

Powiązany candidate set pozostaje jedną jednostką akceptacji i atomowego zapisu w project.db, z rollbackiem całości przy błędzie. Ewentualny zapis zweryfikowanej ekstrakcji nie jest canonical promotion. Późniejszy proposal wskazuje ten zestaw i jego weryfikację; canonical commit nie może obchodzić tych gwarancji ani częściowo promować zestawu. Propagacja poza project.db zachowuje odrębny protokół z §24.

---

# 11. WALIDACJA PROPOSAL

Przed commit CanonService musi wykonać wymagane kontrole.

Co najmniej:

SCHEMA VALIDATION

IDENTITY VALIDATION

SCOPE VALIDATION

VERSION VALIDATION

TEMPORAL VALIDATION

PROTECTION VALIDATION

REFERENCE INTEGRITY

CONFLICT DETECTION

AUTHORITY VALIDATION

POLICY VALIDATION.

Zmiana niespełniająca kontraktu nie trafia do obowiązującego stanu.

---

# 12. CANON SERVICE

CanonService jest serwisem domenowym.

Nie jest:

- Agentem LLM,
- Repository,
- częścią UI,
- middleware HTTP,
- częścią model routera.

CanonService odpowiada za:

- walidację CanonicalChangeProposal,
- egzekwowanie protection rules,
- egzekwowanie authority,
- wersjonowanie,
- superseding,
- retcon,
- conflict detection,
- materializację zatwierdzonego stanu,
- zapis provenance,
- wywołanie wymaganej Impact Analysis,
- przygotowanie atomowego planu persistence.

Repository odpowiada za fizyczne operacje storage.

Jest to rozszerzenie odpowiedzialności istniejącego app/p20_core/canon_service.py, nie utworzenie drugiego CanonService. Obecna logika plikowego kanonu wymaga późniejszego przeniesienia odpowiedzialności persistence do istniejącej warstwy Repository przy zachowaniu jednego ownera domenowego. Integracja ścieżki zapisu GAP-008 musi zachować §10.1; CanonService używa istniejącego DomainMutationGuard, a nie implementuje obok niego własnego guarda.

---

# 13. AUDYT CANONICAL CHANGE PROPOSAL

CanonicalChangeProposal nie jest bezpośrednio częścią obowiązującego Kanonu.

Nie może jednak znikać bez śladu.

Audyt musi pozwalać odtworzyć co najmniej:

proposal\_id
proposal\_hash
source
actor
run\_id
step\_id
validation\_result
authority\_result
policy\_result
decision
commit\_result
resulting\_versions
rejection\_reason
created\_at.

Możliwy wynik:

ACCEPTED

REJECTED

REQUIRES\_AUTHOR\_DECISION

CONFLICT

INVALID.

Dokładne statusy zostaną określone w kontrakcie procesu.

---

# 14. PROJECT SCOPE I SERIES SCOPE

Narrative State Engine działa zarówno dla:

PROJECT

jak i:

SERIES.

Series Scope przechowuje świadomie współdzielony stan serii.

Project Scope przechowuje stan konkretnej książki.

Nie obowiązuje zasada:

„Project zawsze nadpisuje Series”.

Projekt nie może cicho zmienić wspólnego Series Canon.

---

# 15. KONFLIKT PROJECT VS SERIES

Jeżeli propozycja Project Scope koliduje z obowiązującym Series Scope:

NIE WYKONUJ SILENT OVERRIDE.

Proces:

PROJECT CHANGE
↓
CONFLICT WITH SERIES?
↓
YES
↓
CanonicalChangeProposal
↓
Impact Analysis
↓
Authority + Policy Gate
↓
jedna z decyzji:

A. UPDATE SERIES CANON

B. CREATE EXPLICIT PROJECT EXCEPTION

C. REJECT CHANGE

D. ESCALATE TO AUTHOR.

Każdy wyjątek Project Scope wobec Series Scope musi być jawny i posiadać provenance.

---

# 16. PROVENANCE

Każda kanoniczna zmiana musi umożliwiać przejście do swoich źródeł.

Przykład:

FactVersion
→ CanonicalChangeProposal
→ MemoryEventRecord
→ Step
→ Run
→ Artifact
→ Source.

Provenance nie może być dekoracyjnym polem tekstowym.

Musi używać trwałych identyfikatorów.

---

# 17. TEMPORAL STATE

Narrative State Engine rozróżnia:

SYSTEM TIME

WORLD TIME

NARRATIVE ORDER.

Fakt może posiadać:

valid\_from
valid\_to.

System musi umożliwiać pytania:

- jaki fakt obowiązuje obecnie,
- jaki fakt obowiązywał w momencie X,
- jaki był stan relacji przed wydarzeniem Y,
- gdzie znajdowała się postać w określonym czasie,
- jaka wersja faktu obowiązywała dla konkretnej sceny.

ID rekordu nie określa chronologii.

---

# 18. KNOWLEDGE STATE

Prawda świata i wiedza postaci pozostają oddzielne.

Narrative State Engine musi wspierać co najmniej:

KNOWS

BELIEVES

SUSPECTS

DENIES

MISREMEMBERS.

Przykład:

PROJECT CANON:
FACT_TEST_001: TEST_CHARACTER_A żyje.

CHARACTER KNOWLEDGE:
TEST_CHARACTER_B BELIEVES TEST_CHARACTER_A nie żyje.

Context Builder dla sceny TEST_CHARACTER_B musi znać różnicę.

---

# 19. CONTRADICTION HANDLING

Sprzeczności nie są automatycznie rozstrzygane przez LLM.

System najpierw stosuje deterministyczne reguły:

- identity,
- scope,
- authority,
- version,
- validity,
- superseding,
- protection,
- existing AuthorDecision.

Jeżeli konflikt pozostaje nierozstrzygnięty:

tworzony lub aktualizowany jest ConflictRecord.

Stan może wymagać:

AUTHOR\_DECISION\_REQUIRED

lub:

RESEARCH\_REQUIRED.

Model nie może dostać jednej wersji nierozstrzygniętego konfliktu jako pewnej prawdy.

---

# 20. CONTEXT BUILDER — BRAK NOWEGO CONTEXT ASSEMBLERA

Narrative State Engine nie tworzy równoległego `ContextAssembler`.

Context Builder i ContextPackage są zaimplementowane. GAP-013 jest CLOSED, a aktywny `/agent/step` buduje ContextPackage przez Context Builder z propagowanymi identity project/book/series. Techniczny retry używa tego samego zapisanego ContextPackage.

Narrative State Engine staje się dla niego źródłem strukturalnego stanu.

Poprawny przepływ:

Narrative State Engine
↓
Jedyny Context Builder
↓
ContextPackage
↓
LLM.

Context Builder pozostaje właścicielem:

- selection,
- token budget,
- role-specific policy,
- mandatory context,
- semantic supporting retrieval,
- context trace,
- context hash.

---

# 21. MANDATORY-FIRST CONTRACT

Narrative State Engine dostarcza Context Builderowi dane pozwalające zbudować obowiązkową część ContextPackage.

Dla Novel Mode logicznie obejmuje to m.in.:

MANDATORY\_CANON

MANDATORY\_TEMPORAL\_STATE

MANDATORY\_KNOWLEDGE\_STATE

OPEN\_CONTINUITY\_LOCKS

TASK\_SPECIFIC\_STATE

AUTHOR LOCKS RELEVANT TO TASK

MUST\_INCLUDE.

Dopiero pozostały budżet może zostać wykorzystany na:

RETRIEVED\_SUPPORTING\_HISTORY.

Semantic ranking nie może wyrzucić danych mandatory.

---

# 22. STRUCTURED-FIRST RETRIEVAL

Narrative State Engine wzmacnia istniejącą zasadę:

STRUCTURED STATE FIRST.

Kolejność logiczna:

1. Project/Series Scope,
2. Canonical State,
3. Temporal State,
4. Knowledge State,
5. Relations,
6. Timeline,
7. Conflicts,
8. Threads / dependencies,
9. Supporting history,
10. semantic retrieval.

Embedding nie jest źródłem prawdy.

Semantic similarity nie rozstrzyga Kanonu.

---

# 23. SUPPORTING HISTORY

Semantic search, BM25, embeddings lub inne mechanizmy retrieval mogą być stosowane dla:

- starych Memory Events,
- raw sources,
- researchu,
- historycznych decyzji,
- komentarzy,
- artefaktów,
- dodatkowego kontekstu.

Są warstwą pomocniczą.

Nie zastępują Canonical State.

---

# 24. ATOMOWOŚĆ

Operacje wyłącznie wewnątrz pojedynczego SQLite database mogą korzystać z transakcji DB.

Nie wolno jednak deklarować globalnej transakcji ACID obejmującej jednocześnie:

project.db
\+
series.db
\+
artifact files
\+
inne zasoby filesystem.

Dla operacji DB + artefakty architektura docelowa wymaga kontrolowanego protokołu:

PREPARE
→ WRITE
→ VALIDATE
→ COMMIT
→ RECOVERY IF REQUIRED.

Dokładny protokół zostanie opisany osobnym kontraktem implementacyjnym.

Narrative State Engine musi respektować istniejącą zasadę:

nie pozostawiać stanu:

tekst zapisany, pamięć nie,

lub:

pamięć zapisana, artefakt nie.

---

# 25. IDEMPOTENCJA

Krytyczne operacje Narrative State Engine muszą realizować jeden docelowy kontrakt idempotency zgodny z Architekturą §102. Obecna implementacja zawiera jedynie częściowe zabezpieczenia, m.in. rozpoznanie identycznego payloadu przez DomainMutationGuard i deduplikację rozdziału; nie jest kompletnym mechanizmem dla poniższych operacji.

Rozszerzenie ma używać wspólnej operation identity i rozpoznawać ponowienie tej samej operacji, nie tworzyć drugiego równoległego systemu idempotency. Szczegóły trwałego zapisu wyniku i recovery należą do późniejszego kontraktu implementacyjnego.

Techniczny retry nie może tworzyć drugiego:

- MemoryEvent,
- FactVersion,
- CanonicalChangeProposal commit,
- Relation update,
- KnowledgeEvent,
- Timeline update,
- AuthorDecision.

---

# 26. IMPACT ANALYSIS

Impact Analysis określa skutki proponowanej zmiany zatwierdzonego stanu. Dla chronionej zmiany musi zostać wykonana PRZED canonical commit i przed wymaganą zgodą użytkownika; analiza po zapisie nie zastępuje tej bramki.

System musi móc wykryć zależności do:

- scen,
- rozdziałów,
- postaci,
- wiedzy postaci,
- relacji,
- wydarzeń,
- wątków,
- setups/payoffs,
- summaries,
- evaluations,
- tłumaczeń,
- Masterów,
- pochodnych indeksów.

Impact Analysis nie zgaduje automatycznie rozwiązania.

Określa zakres wpływu.

GAP-011 może rozpocząć się dopiero po akceptacji tego ADR i odrębnej decyzji implementacyjnej. Rozwija istniejący Graph / EdgeRecord / traversal z zamkniętego GAP-010, bez drugiego grafu.

Pełna implementacja Memory Ledger, CanonicalChangeProposal i Temporal State nie jest warunkiem rozpoczęcia podstawowego GAP-011. Wykorzystanie Impact Analysis w chronionym canonical commit wymaga późniejszego kontraktu Canonical Change określającego integrację bramek i zapisu. Niniejsza korekta nie rozpoczyna GAP-011.

---

# 27. INVALIDATION

Zmiana źródłowego stanu może unieważnić dane pochodne.

Przykładowo:

summary
embedding
retrieval cache
Context cache
QUALITY result
translation
localized Master.

Pochodny artefakt odnoszący się do starej wersji nie może być cicho traktowany jako aktualny.

---

# 28. MODEL LLM

LLM nie jest właścicielem pamięci.

LLM nie jest właścicielem Kanonu.

LLM nie jest właścicielem Timeline.

LLM nie jest właścicielem wiedzy postaci.

LLM może:

- analizować,
- pisać,
- klasyfikować,
- proponować,
- ekstrahować kandydatów faktów,
- wskazywać możliwe konflikty.

LLM nie może samodzielnie wykonywać finalnego canonical commit.

Zmiana modelu:

OpenAI
→ Anthropic
→ inny provider

nie może powodować utraty pamięci projektu.

---

# 29. MAPOWANIE NA ISTNIEJĄCĄ ARCHITEKTURĘ

| ElementScopeWłaściciel logikiPersistence |                        |                                                |                        |
| ---------------------------------------- | ---------------------- | ---------------------------------------------- | ---------------------- |
| Memory Event Ledger                      | Project / Series       | Repository persistence + domain event creation | project.db / series.db |
| Entities                                 | Project / Series       | Domain Services + Repository                   | project.db / series.db |
| Facts                                    | Project / Series       | CanonService                                   | project.db / series.db |
| Fact Versions                            | Project / Series       | CanonService                                   | project.db / series.db |
| Relations                                | Project / Series       | CanonService                                   | project.db / series.db |
| Timeline State                           | Project / Series       | CanonService                                   | project.db / series.db |
| Character Knowledge                      | Project / Series       | CanonService                                   | project.db / series.db |
| Reader Knowledge                         | Project / Series       | CanonService                                   | project.db / series.db |
| Provenance                               | Project / Series       | Domain Services + Repository                   | project.db / series.db |
| Conflict Records                         | Project / Series       | CanonService                                   | project.db / series.db |
| Author Decisions                         | Project / Series       | Domain Service                                 | project.db / series.db |
| CanonicalChangeProposal runtime object   | Runtime                | producer role / service                        | transient              |
| CanonicalChangeProposal audit record     | Project / Series audit | CanonService / audit layer                     | project.db / series.db |
| Validation                               | Domain                 | CanonService / validators                      | code domenowy          |
| Authority + Policy Gate                  | Domain                 | CanonService / policy layer                    | code domenowy          |
| Canonical Commit                         | Domain                 | CanonService                                   | przez Repository       |
| Physical persistence                     | Storage                | ProjectRepository / SeriesRepository           | project.db / series.db |
| Context selection                        | Runtime                | jedyny Context Builder                         | ContextPackage         |
| Semantic supporting retrieval            | Derived layer          | retrieval subsystem                            | rebuildable indexes    |
| Raw PDF / screenshot / large source      | Artifact               | artifact subsystem                             | artifact storage       |
| Artifact provenance                      | Project / Series       | Repository                                     | project.db / series.db |
| System configuration                     | System                 | system services                                | agentpro\_system.db    |

---

## 29.1 INTEGRACJA Z ZAMKNIĘTYMI GAP-AMI

- GAP-007 → rozszerzany przez Ledger + Materialized State w jednym modelu pamięci opisanym w §5–7.
- GAP-008 → Extraction / Candidate / Verification i ich gwarancje pozostają bez zmian; dopiero po weryfikacji powstaje odrębny CanonicalChangeProposal zgodnie z §10.1.
- GAP-009 → jedyny DomainMutationGuard jest ponownie używany przez ścieżkę CanonService; nie powstaje konkurencyjna ochrona.
- GAP-010 → istniejący Graph / traversal jest fundamentem GAP-011 Impact Analysis, zgodnie z §26.

GAP-001..GAP-010 pozostają CLOSED. To mapowanie definiuje przyszłą integrację, nie ponowne otwarcie ani modyfikację zamkniętych implementacji.

---

# 30. SERIES CANON

SeriesRepository przechowuje Series Scope.

Series Scope może obejmować:

- wspólne postacie,
- długoterminowe fakty,
- timeline,
- wiedzę postaci,
- tajemnice,
- wspólne miejsca,
- wspólne organizacje,
- relacje między tomami,
- wielotomowe wątki.

ProjectRepository nie kopiuje całej pamięci serii.

Projekt korzysta z dozwolonego Series Scope poprzez jawne powiązanie.

---

# 31. PROJECT STATE

ProjectRepository przechowuje dane należące do pojedynczej książki.

Projekt posiada własne:

- lokalne Facts,
- lokalne FactVersions,
- lokalne wydarzenia,
- lokalne relacje,
- knowledge state,
- Memory Events,
- Author Decisions,
- konflikty,
- provenance,
- artefakty,
- runy.

Niezależny projekt nie widzi danych innego projektu.

---

# 32. AGENTPRO\_SYSTEM.DB

Narrative State Engine nie przenosi danych narracyjnych do `agentpro_system.db`.

`agentpro_system.db` pozostaje magazynem systemowym.

Nie staje się globalną bazą pamięci książek.

---

# 33. ZAKAZY ARCHITEKTONICZNE

Narrative State Engine NIE może:

1. tworzyć osobnej bazy pamięci poza zatwierdzonym storage,
2. tworzyć równoległego Context Assemblera,
3. zapisywać Kanonu bez CanonService,
4. pozwalać LLM na bezpośredni zapis Facts,
5. traktować embedding similarity jako prawdy,
6. traktować confidence jako authority,
7. traktować author lock jako authority,
8. pozwalać Project Scope cicho nadpisywać Series Scope,
9. nadpisywać historię Memory Ledger,
10. mieszać fabularnego EventRecord z MemoryEventRecord,
11. wymagać Neo4j,
12. wymagać LangGraph,
13. tworzyć nowego runtime,
14. łamać izolacji projektów,
15. omijać audytu.

---

# 34. ODRZUCONE ALTERNATYWY

## 34.1 Czysty lossless event log

Odrzucone.

Powód:

zachowanie wszystkiego nie określa, co jest aktualną prawdą projektu.

---

## 34.2 LLM jako pamięć projektu

Odrzucone.

Powód:

model jest wymienny i posiada ograniczony kontekst.

---

## 34.3 LLM jako właściciel Kanonu

Odrzucone.

Powód:

niedeterministyczny drift i brak kontrolowanego authority.

---

## 34.4 Osobna baza memory.db

Odrzucone.

Powód:

dublowanie zatwierdzonych granic storage i ryzyko dwóch źródeł prawdy.

---

## 34.5 Osobny agent CanonManager z prawem zapisu

Odrzucone.

Agent może przygotować proposal.

Commit pozostaje domenową odpowiedzialnością CanonService.

---

## 34.6 Neo4j od początku

Odrzucone.

SQLite + jawne relations/edges wystarczą do pierwszej implementacji.

---

## 34.7 Nowy ContextAssembler

Odrzucone.

Context Builder i ContextPackage są zaimplementowane jako jedyny builder i package w architekturze. Narrative State Engine dostarcza mu dane, nie tworzy równoległego assemblera.

---

# 35. KONSEKWENCJE POZYTYWNE

Wprowadzenie Narrative State Engine daje:

- deterministyczny stan Kanonu,
- pełną historię zmian,
- kontrolę retconów,
- temporalność,
- możliwość query as-of,
- provenance,
- rozdzielenie prawdy i wiedzy postaci,
- ochronę Author Decisions,
- trwałą pamięć niezależną od modelu,
- możliwość audytu decyzji,
- bezpieczne Context Packages,
- mocną izolację projektów,
- możliwość obsługi serii,
- możliwość przyszłego testowania memory drift.

---

# 36. KONSEKWENCJE NEGATYWNE / KOSZTY

System staje się bardziej rygorystyczny.

Wymaga:

- wersjonowania,
- poprawnego proposal flow,
- jednoznacznego scope,
- discipline w provenance,
- poprawnego recovery,
- migracji schematów,
- nowych testów regresyjnych,
- Impact Analysis dla części zmian,
- utrzymywania materialized state.

Jest to świadomy koszt za deterministyczność i bezpieczeństwo Kanonu.

---

# 37. TESTABILITY REQUIREMENTS

Przyszła implementacja musi umożliwić testy co najmniej:

- append-only Memory Ledger,
- brak cross-project leakage,
- SERIES visibility wyłącznie dla członków serii,
- LLM nie zapisuje bezpośrednio Kanonu,
- rejected proposal nie zmienia Canonical State,
- accepted proposal tworzy poprawną nową wersję,
- superseded Fact przestaje być current,
- query\_as\_of zwraca prawidłowy historyczny stan,
- retcon posiada pełne provenance,
- chroniony Fact nie może zostać zmieniony bez pre-commit Impact Analysis i wymaganej zgody użytkownika; samo authority nie zastępuje approval,
- nierozstrzygnięty conflict trafia do Context Buildera jako warning,
- Knowledge State nie jest mylony z Project Truth,
- retry jest idempotentny,
- zmiana wersji stanu zmienia context\_hash,
- projekt nie nadpisuje cicho Series Canon,
- brak artefaktu przy commit failure nie pozostawia fałszywego stanu zaakceptowanego.

---

# 38. PRZYSZŁY MEMORY TORTURE TEST

Narrative State Engine powinien docelowo posiadać własny test skalowania pamięci.

Przykładowe poziomy:

1 000 zdarzeń

10 000 zdarzeń

100 000 zdarzeń

1 000 000 zdarzeń.

Testy powinny obejmować:

- aktualny stan faktu,
- stare wersje,
- retcony,
- conflicting facts,
- who\_knows,
- false beliefs,
- Series Scope,
- Project exceptions,
- provenance,
- retrieval supporting history,
- latency,
- idempotency,
- isolation.

Przykładowe metryki:

CURRENT STATE ACCURACY

AS-OF STATE ACCURACY

CONTRADICTION DETECTION RATE

KNOWLEDGE STATE ACCURACY

RECALL\@K DLA SUPPORTING HISTORY

FALSE CURRENT STATE RATE

P95 LATENCY.

Benchmark nie jest częścią pierwszej implementacji.

---

# 39. POZA ZAKRESEM TEGO ADR

Ten ADR NIE określa jeszcze:

- fizycznego schema SQL,
- nazw wszystkich kolumn,
- indeksów SQL,
- migracji,
- implementacji Python,
- API,
- endpointów,
- UI,
- finalnych enumów authority,
- finalnego algorytmu semantic retrieval,
- modelu embeddings,
- dokładnych wag rankingowych,
- wyboru bibliotek.

Te decyzje następują później.

---

# 40. KRYTERIA AKCEPTACJI ADR

ADR może przejść ze statusu:

PROPOSED

do:

ACCEPTED

dopiero gdy zostanie potwierdzone, że:

1. nie zmienia MASTER\_CANON\_AGENTPRO v2,
2. jest zgodny z ARCHITEKTURA\_AGENTPRO v1.2,
3. jest zgodny z ADR-0001,
4. zachowuje `project.db / series.db / agentpro_system.db`,
5. CanonService pozostaje serwisem domenowym ponad Repository,
6. LLM nie posiada direct canonical write,
7. authority, origin, protection i canon state są rozdzielone,
8. Project Scope nie nadpisuje cicho Series Scope,
9. MemoryEventRecord jest oddzielony od fabularnego EventRecord,
10. raw artifacts pozostają w istniejącym artifact storage,
11. CanonicalChangeProposal pozostaje audytowalny,
12. Context Builder określony w Architekturze jest zaimplementowanym jedynym builderem ContextPackage,
13. nie powstaje równoległy memory runtime,
14. implementacja nie rozpoczyna się przed osobną decyzją.

---

# 41. DECYZJA KOŃCOWA ADR

Narrative State Engine jest projektowany jako:

VERSIONED NARRATIVE STATE ENGINE

oparty o:

IMMUTABLE HISTORY
\+
MATERIALIZED CANONICAL STATE
\+
TEMPORAL STATE
\+
KNOWLEDGE STATE
\+
PROVENANCE
\+
CONFLICT CONTROL
\+
AUTHOR PROTECTION
\+
DETERMINISTIC CANON CHANGE FLOW.

Model językowy pozostaje wymiennym executorem.

Model nie jest pamięcią.

Model nie jest Kanonem.

Model nie ustanawia samodzielnie prawdy projektu.

AgentPRO pamięta historię projektu.

AgentPRO wie, która wersja informacji obowiązuje.

AgentPRO wie, kiedy obowiązywała.

AgentPRO wie, kto ją ustanowił.

AgentPRO wie, z czego wynika.

AgentPRO wie, kto w świecie powieści ją zna.

AgentPRO potrafi pokazać pełne provenance zmiany.

Narrative State Engine pozostaje częścią istniejących granic:

ProjectStorageContext
/
SeriesStorageContext

→ Repository

→ project.db / series.db.

Nie powstaje równoległe źródło prawdy.
