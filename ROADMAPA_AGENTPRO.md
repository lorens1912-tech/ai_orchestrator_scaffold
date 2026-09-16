# ROADMAPA_AGENTPRO
## WERSJA 1.0 — BIEŻĄCA ROADMAPA OPERACYJNA
## ZGODNA Z MASTER_CANON_AGENTPRO v2, ARCHITEKTURA_AGENTPRO v1.2 I ADR-0001

======================================================================
0. ROLA ROADMAPY
======================================================================

MASTER_CANON_AGENTPRO określa WHAT.
ARCHITEKTURA_AGENTPRO określa HOW.
ROADMAPA_AGENTPRO określa NOW / NEXT / LATER / TARGET.

Roadmapa nie jest dowodem istniejącej implementacji.

Każdy etap rozpoczyna się dopiero po zamknięciu zależności z wcześniejszych etapów albo po jawnej decyzji o zmianie kolejności.

Klasyfikacja audytowa:
DONE
PARTIAL
MISSING
LEGACY
CONFLICT
NOT_REQUIRED_YET.

======================================================================
ETAP 0 — AUDYT OBECNEGO AGENTPRO
======================================================================

CEL:
Ustalić rzeczywisty stan obecnego P20.x względem Master Canonu v2, Architektury v1.2 i ADR-0001.

Audyt obejmuje co najmniej:
- app.main jako owner API,
- P20.x / P0 / legacy boundary,
- storage i test isolation,
- Project Truth,
- Book Bible,
- canon rebuild,
- run/step/artifacts,
- resume i locki,
- MODE/ROLE/PRESET/MODEL,
- quality,
- pamięć,
- brakujące elementy architektury docelowej.

WYNIK:
AUDIT_AGENTPRO_V2
GAP_LIST_AGENTPRO_V2.

Nie implementujemy nowych funkcji przed uzyskaniem Gap List.

======================================================================
ETAP 1 — FUNDAMENT DANYCH I REPOZYTORIÓW
======================================================================

CEL:
Oddzielić logikę domenową od fizycznego storage i przygotować migrację do architektury project.db / series.db / agentpro_system.db.

Zakres:
- ProjectStorageContext,
- SeriesStorageContext,
- Storage Resolver,
- Repository Layer,
- SQLite + WAL,
- schema version,
- migracje,
- bezpieczny dostęp do istniejących artefaktów plikowych,
- brak rozproszonych bezpośrednich ścieżek w logice domenowej.

Etap nie może zniszczyć istniejących projektów.

======================================================================
ETAP 2 — TOŻSAMOŚĆ DOMENOWA I SCHEMATY
======================================================================

CEL:
Wprowadzić stabilne ID i formalne kontrakty encji.

Zakres co najmniej:
ProjectRecord
SeriesRecord
BookRecord
ActRecord
SequenceRecord
ChapterRecord
SceneContract
CharacterProfile
VoiceProfile
CharacterArc
RelationshipRecord
FactRecord
EventRecord
PlaceRecord
RouteRecord
ThreadRecord
SetupPayoffRecord
ResearchRecord
ResearchSource
ResearchClaim
TermRecord
AuthorDecision
ConflictRecord
EvaluationRecord
ContextPackage.

frozen i author_locked pozostają osobnymi polami.

======================================================================
ETAP 3 — PAMIĘĆ STRUKTURALNA NOVEL MODE
======================================================================

CEL:
Przenieść pamięć powieści z luźnych pomocniczych struktur do wersjonowanej pamięci strukturalnej.

Zakres:
- fakty,
- timeline,
- stany,
- provenance,
- wersje,
- snapshoty,
- canonical read/write path,
- zgodność z Book Bible i chapter artifacts.

======================================================================
ETAP 4 — GRAF ZALEŻNOŚCI I ANALIZA WPŁYWU
======================================================================

CEL:
Zbudować jawny graf relacji i dependency system.

Zakres:
- edges,
- graph repository,
- traversal,
- dependency queries,
- Impact Analysis,
- invalidation danych pochodnych.

Pierwsza wersja używa SQLite; brak obowiązku Neo4j.

======================================================================
ETAP 5 — STRUKTURA FABULARNA
======================================================================

CEL:
Wprowadzić strukturę SERIES→VOLUME→ACT→SEQUENCE→CHAPTER→SCENE jako dane.

Zakres:
- akty,
- sekwencje,
- rozdziały,
- Scene Contracts,
- narrative order,
- cele, konflikt, stawki, outcome,
- stan wejścia/wyjścia,
- causality.

======================================================================
ETAP 6 — PAMIĘĆ POSTACI, WIEDZY I STANU
======================================================================

CEL:
Pełna kontrola ciągłości postaci.

Zakres:
- CharacterProfile,
- CharacterState,
- KnowledgeEvent,
- beliefs/secrets,
- ReaderKnowledgeEvent,
- Relationships,
- CharacterArc,
- stan postaci w określonej scenie.

======================================================================
ETAP 7 — PAMIĘĆ SERII I SAGI
======================================================================

CEL:
Kontrolowany Series Scope dla wielu tomów.

Zakres:
- Series Canon,
- Series Memory,
- series.db,
- SeriesMembership / SeriesAccessContext,
- VolumeClosingSnapshot,
- cross-volume relationships,
- character knowledge,
- reader knowledge,
- multi-volume threads.

Niezależna książka nie może widzieć danych serii.

======================================================================
ETAP 8 — CONTEXT BUILDER
======================================================================

CEL:
Zastąpić ręczne/sklejane prompty kontrolowanym systemem retrievalu.

Zakres:
- ContextPackage,
- ContextPolicy,
- ContextProfile per role,
- must_include,
- hard context budget przed retrievalem,
- structured retrieval,
- graph/timeline/knowledge/thread/place retrieval,
- semantic retrieval,
- relevance scoring,
- narrative recency,
- unresolved conflict warnings,
- overflow policy,
- context trace,
- context_hash,
- context_package_id,
- retry reuse.

Provenance semantic retrievalu zapisuje model/wersję embeddingu lub indeksu.

======================================================================
ETAP 9 — SYSTEM STYLU I GŁOSÓW POSTACI
======================================================================

CEL:
Trwały, wersjonowany styl projektu i różne głosy postaci.

Zakres:
- StyleProfile,
- FunctionalStyleProfile,
- VoiceProfile,
- style drift,
- relation/state-specific speech,
- naturalność literacka.

======================================================================
ETAP 10 — RESEARCH I WERYFIKACJA FAKTÓW
======================================================================

CEL:
Pełny przepływ źródło→claim→verify→decision→canon.

Status wykonawczy 2026-09-16: **GAP-015 = CLOSED** — tekstowy import źródeł,
niezależna weryfikacja i kontrolowana promocja PROJECT przez istniejący
Canonical Change; pełna regresja 657 passed, 1 skipped.
Dowody i ograniczenia formatów: [raport GAP-015](docs/GAP015_REPORT.md).
API i użycie: [Research](docs/GAP015_RESEARCH.md).
Live adapter smoke = NOT RUN. GAP-016 nie został rozpoczęty.

Zakres:
- ResearchRecord,
- ResearchSource,
- ResearchClaim,
- verification status,
- confidence,
- provenance,
- real vs fictional,
- fictional overlay,
- AuthorDecision,
- ConflictRecord,
- miejsca i fizyczna wiarygodność.

======================================================================
ETAP 11 — ROLE I ROUTER MODELI
======================================================================

CEL:
Ustabilizować role i wymienność modeli.

Zakres:
- ROLE oddzielone od MODE/PRESET,
- Model Router,
- requested_model,
- effective_model,
- fallback policy,
- provider-neutral policy,
- pełny audit model invocation,
- efektywne parametry wykonania.

======================================================================
ETAP 12 — PRODUKCYJNY PIPELINE ROZDZIAŁU
======================================================================

CEL:
Pełny workflow rozdziału na nowych fundamentach.

Przykładowa orkiestracja:
PLAN
→ WRITE
→ CRITIC
→ REWRITE
→ EDIT
→ CONTINUITY
→ QUALITY.

Zakres:
- canon precheck,
- Scene/Chapter ContextPackage,
- wersjonowanie tekstu,
- chapter_XXX.json,
- provenance,
- trwała akceptacja rozdziału,
- kontrolowany commit pamięci i artefaktów.

======================================================================
ETAP 13 — QUALITY ENGINE I AUTONOMICZNA PĘTLA POPRAWY
======================================================================

CEL:
Jakość jako formalny silnik, nie jednorazowa opinia modelu.

Zakres:
ACCEPT / REVISE / REJECT,
quality decision ≠ execution status,
EvaluationRecord,
criteria version,
prompt version,
model identity,
context_hash,
Evaluation Cache,
retry vs reevaluation,
targeted revise loop.

======================================================================
ETAP 14 — KONTROLA CAŁEJ KSIĄŻKI
======================================================================

CEL:
Hierarchiczny Book QA.

Zakres:
- structure,
- causality,
- pacing,
- tension,
- arcs,
- continuity,
- canon,
- character knowledge,
- reader knowledge,
- threads,
- setup/payoff,
- style,
- voices,
- redundancy,
- research/facts,
- terminology,
- final quality.

======================================================================
ETAP 15 — SOURCE MASTER
======================================================================

CEL:
Formalne zamknięcie wersji źródłowej.

Zakres:
- Candidate Master,
- final user approval,
- Source Master,
- immutable/versioned lineage,
- rollback/reference to previous manuscript versions.

Source Master powstaje tylko po decyzji użytkownika.

======================================================================
ETAP 16 — TŁUMACZENIE I LOKALIZACJA
======================================================================

CEL:
Kontrolowana, naturalna translacja od Source Master.

Zakres:
- explicit start,
- Translation Bible,
- niezależne en-US i en-GB,
- terminology consistency,
- style/voice preservation,
- translation QA,
- target-language naturalness.

======================================================================
ETAP 17 — PRZYGOTOWANIE WYDAWNICZE
======================================================================

CEL:
Przygotować zaakceptowany Master do procesu wydawniczego bez naruszania źródłowej prawdy tekstu.

Zakres może obejmować:
- formatowanie/eksport,
- metadane,
- materiały publikacyjne,
- KDP-ready outputs,
- kontrolę wersji eksportu.

======================================================================
ETAP 18 — PEŁNA APLIKACJA OPERATORSKA PL/EN
======================================================================

CEL:
Operator UI dla gotowego backendu.

UI ma być PL/EN i korzystać wyłącznie z oficjalnego API.

Zakres ekranów m.in.:
- projekty,
- runy,
- postacie,
- głosy,
- styl,
- research,
- miejsca,
- canon,
- quality,
- Source Master,
- translations.

UI nie przenosi do siebie logiki domenowej.

======================================================================
ETAP 19 — WIELOPROJEKTOWOŚĆ DO 7 KSIĄŻEK
======================================================================

CEL:
Uruchomić pełne skalowanie do 7 projektów na fundamentach przygotowanych od Etapu 1.

Zakres:
- scheduler,
- kolejki,
- limity providerów,
- fairness,
- stop/resume,
- izolacja,
- brak cross-project context,
- Series Scope tylko jawny.

Novel Mode zachowuje przyjęte ograniczenia aktywnej pracy nad powieścią; Guide Mode może wykorzystywać równoległość zgodnie z osobną polityką.

======================================================================
ETAP 20 — HARDENING PRODUKCYJNY I FINALNY AUDYT
======================================================================

CEL:
Zamknąć AgentPRO jako system produkcyjny zgodny z Master Canonem.

Zakres:
- pełna regresja,
- migration tests,
- backup/restore,
- crash recovery,
- safe stop,
- idempotency,
- lock stress,
- project/series isolation,
- context builder tests,
- quality cache tests,
- audit completeness,
- runtime boundary,
- brak przypadkowego legacy w ścieżce produkcyjnej,
- final gap audit.

======================================================================
ZASADA KOŃCOWA ROADMAPY
======================================================================

Najpierw wykonujemy ETAP 0 i tworzymy aktualny Gap List.

Dopiero potem zaczynamy implementację Etapu 1.

Nie przeskakujemy do późniejszych funkcji tylko dlatego, że są atrakcyjne.

Każdy wcześniejszy etap ma pozostawić fundament kompatybilny z wymaganiami późniejszymi.
