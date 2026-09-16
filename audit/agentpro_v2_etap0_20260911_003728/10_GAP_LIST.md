# Gap List

Priorytety:
- P0: naruszenie integralnosci / Canon / project isolation / P20 boundary.
- P1: blokuje nastepny etap.
- P2: wymagane pozniej.
- P3: usprawnienie.

| GAP-ID | STATUS | WYMAGANIE | STAN OBECNY | DOWOD | RYZYKO | ZALEZNOSCI | ETAP ROADMAPY | PRIORYTET |
|---|---|---|---|---|---|---|---|---|
| GAP-001 | MISSING | ProjectStorageContext / StorageResolver / ProjectRepository | Jest centralny storage root, ale nie ma kontekstu projektu i repozytorium domenowego. | `storage_paths.py`; brak `ProjectRepository` w `app/`; ADR-0001. | Bez repozytorium nie da sie bezpiecznie dodac Structured Memory. | Brak. | ETAP 1 | P1 |
| GAP-002 | MISSING | SeriesStorageContext / SeriesRepository | Brak strukturalnej serii i `series.db`. | Brak `SeriesRepository`; ADR-0001. | Przecieki serii lub brak shared memory dla sag. | GAP-001. | ETAP 1 | P1 |
| GAP-003 | MISSING | `project.db`, `series.db`, `agentpro_system.db` | Obecnie storage plikowy `books/runs/audit/novel_runs`. | Brak symboli DB i migracji w `app/`. | Brak atomowego commita domenowych encji sceny. | GAP-001, GAP-002. | ETAP 1 | P1 |
| GAP-004 | MISSING | Strukturalny Project/Series Scope | Scope oparty o `book_id` i sciezki, nie repo context. | `runtime.py`, `canon_service.py`, `storage_paths.py`. | Trudno formalnie dowiesc izolacji miedzy projektami/seriami w v2. | GAP-001. | ETAP 1 | P1 |
| GAP-005 | PARTIAL | Oczyszczenie aktywnego executora P20 | `runtime.py` uzywa `app.orchestrator_stub.execute_stub`, ktory zawiera historyczne wrappery. | `runtime.py`, `orchestrator_stub.py`. | Ryzyko regresji przy rozbudowie v2, ale obecny suite przechodzi. | GAP-001 moze poprzedzac ekstrakcje. | ETAP 1/20 | P1 |
| GAP-006 | MISSING | Stable domain IDs i record contracts | Brak ProjectRecord, SceneContract, FactRecord itd. w produkcji. | Brak symboli w `app/`; `ROADMAPA_AGENTPRO.md` ETAP 2. | Structured Memory nie ma stabilnych referencji. | GAP-001-004. | ETAP 2 | P1 |
| GAP-007 | MISSING | Structured Memory base | Brak FactRecord, EventRecord, CharacterState, KnowledgeEvent, Thread, Setup, Payoff. | Brak symboli w `app/`. | Agent nie ma trwalej, audytowalnej pamieci powiesciowej v2. | GAP-006. | ETAP 3 | P1 |
| GAP-008 | MISSING | Structured Memory Extraction Integrity | §44A jest w architekturze, ale brak extractor/verifier/status/atomic commit. | `ARCHITEKTURA_AGENTPRO.md §44A`; brak kodu. | LLM extraction mogloby zanieczyscic kanoniczna pamiec, jesli wdrozone bez guarda. | GAP-001, GAP-006, GAP-007. | ETAP 3 | P1 |
| GAP-009 | MISSING | Domain Mutation Guard / frozen / author_locked | Brak domenowych guardow i flag. | Brak symboli `frozen`, `author_locked`, Domain Mutation Guard w `app/`. | Nie ma kontroli zmian zaakceptowanych faktow/postaci. | GAP-006, GAP-007. | ETAP 2/3 | P1 |
| GAP-010 | MISSING | Graph / EdgeRecord / dependency traversal | Brak grafu zaleznosci. | Brak `EdgeRecord` i traversal w `app/`. | Brak Impact Analysis i kontekstowych zaleznosci. | GAP-007. | ETAP 4 | P2 |
| GAP-011 | MISSING | Impact Analysis | Brak analizy skutkow mutacji. | Brak produkcyjnego modulu. | Zmiany kanonu/pamieci moga miec ukryte skutki. | GAP-010. | ETAP 4 | P2 |
| GAP-012 | MISSING | Series Canon / Series Memory / VolumeClosingSnapshot | Brak serii w runtime. | Brak rekordow i repozytorium serii. | Brak obslugi sag/wielotomowosci. | GAP-002, GAP-007. | ETAP 7 | P2 |
| GAP-013 | MISSING | Context Builder | Brak ContextPackage, ContextPolicy, ContextProfile, trace. | Brak symboli w `app/`. | Dlugie ksiazki beda mialy slaby, niepowtarzalny kontekst. | GAP-007, GAP-010. | ETAP 8 | P2 |
| GAP-014 | MISSING | StyleProfile / VoiceProfile | Tryb STYLE istnieje, ale brak profili i glosow. | `app/tools.py` `tool_style`; brak `VoiceProfile`. | Brak konsekwentnego stylu i glosow postaci. | GAP-013. | ETAP 9 | P2 |
| GAP-015 | MISSING | ResearchRecord / ResearchSource / ResearchClaim | Brak research repository. | `tool_factcheck` jest stubem. | Ryzyko halucynacji i slabej fact verification. | GAP-001, GAP-006. | ETAP 10 | P2 |
| GAP-016 | PARTIAL | Model parameter provenance | Czesc metadanych istnieje, ale nie pelna provenance wszystkich wywolan. | `model_policy.py`, `debug_model_router.py`, `orchestrator_stub.py`. | Ograniczony replay/audit model calls. | GAP-013, GAP-017. | ETAP 11/13 | P2 |
| GAP-017 | MISSING | EvaluationRecord / Evaluation Cache | Brak docelowych rekordow i cache. | Brak symboli w `app/`. | Reevaluation i replay nie sa formalne. | GAP-013, GAP-016. | ETAP 13 | P2 |
| GAP-018 | MISSING | Source Master / Book QA / Candidate Master / user approval | Brak finalnego QA i approval gate. | Brak produkcyjnych endpointow/modulow. | Brak kontrolowanego finalnego wydania ksiazki. | GAP-007, GAP-013, GAP-017. | ETAP 14/15 | P2 |
| GAP-019 | MISSING | Translation Bible / en-US / en-GB branches | Brak lokalizacyjnych branchy. | `tool_translate` passthrough; brak Translation Bible. | Brak audytowalnej adaptacji jezykowej. | GAP-018. | ETAP 16 | P3 |
| GAP-020 | PARTIAL | P20.2/P20.3 scripts/integration | Istnieja testy start scripts, ale nie pelna docelowa architektura etapow v2. | `tests/test_123_start_scripts_point_to_p20.py`; roadmap. | Ryzyko mylenia etykiet etapow z gotowym runtime. | GAP-001-018. | ETAP 20 | P3 |

## Liczniki luk

- P0_GAPS = 0
- P1_GAPS = 9
- P2_GAPS = 9
- P3_GAPS = 2

## First blocking gap

FIRST_BLOCKING_GAP = `GAP-001 ProjectStorageContext / StorageResolver / ProjectRepository`

Uzasadnienie: bez tego nie da sie poprawnie wdrozyc domain records, Structured Memory, atomowego commita memory extraction, graph, Context Buildera ani EvaluationRecord.
