# AgentPRO — System 1: Grok / KreionAI / Narrative State

Data: 2026-09-23. STATUS ANALIZY = DONE. Implementacja nowych funkcji = NOT STARTED.

To przegląd pokrycia i rekomendacja kolejności prac, wykonane jednym modelem.
Nie jest to niezależny audyt ani decyzja rozszerzająca zaakceptowaną architekturę.
Nazwy Grok/Kreion oznaczają listę wymagań przekazaną przez użytkownika, a nie
zweryfikowane możliwości zewnętrznych produktów. Nie wykonywano researchu tych produktów.

## 1. Punkt wyjścia i granice

- Repo: `C:\AI\ai_orchestrator_scaffold`.
- Branch: `codex/agentpro-stabilization-freeze`.
- HEAD: `b8003d114174eaae5012dab642d22681042c3032`.
- Staging przed analizą: EMPTY.
- Porównano wszystkie 2546 wpisów `status/path`, nie tylko ich liczbę.
  Delta wobec `.test_storage/gap017_evidence/phase1/baseline-porcelain.txt`: 0.
- Powiązanie baseline z checkpointem zamknięcia:
  `.test_storage/gap017_evidence/phase5/closure/{manifest.md,finalization-results.txt}`.
  Katalog closure potwierdza dokładną zgodność baseline; pełna lista pozostaje
  w dowodzie phase1. Nie zastąpiono jej skróconym `git status --short`.
- Nowe dowody: `.test_storage/system1_coverage/20260923_coverage_01/` — ignorowane przez Git.
- Jedyny nowy plik poza dowodami: niniejszy raport. Kod, testy, kontrakty,
  roadmapa, indeks Git i dane projektów nie były zmieniane.
- Nie czytano ani nie hashowano rzeczywistych książek. Hashe obejmują wyłącznie
  jawne pliki kodu, testów i dokumentacji. Status/path nie jest inspekcją treści książek.

### Konkluzja

**AgentPRO ma znaczną część trwałego, kontrolowanego wykonania P20 oraz chronionej
pamięci PROJECT/SERIES. Nie ma jeszcze pełnego Narrative State Engine ani pełnego
produkcyjnego workflow pisania powieści.** Nie należy budować obok niego nowego
runtime'u, pamięci, grafu lub ContextBuildera.

GAP-017 zamknął trwałe oceny i idempotentną reevaluation. Nie był kontraktem
ogólnego Workflow Engine, literackiego evaluatora ani Book QA. Braki tych funkcji
nie są dowodem regresji GAP-017. Analogicznie F-002/F-006 nie stają się ponownie
otwarte tylko dlatego, że pełny retcon lub odbudowa pochodnych są dalszym zakresem.

## 2. Źródła, status decyzji i siła dowodu

### Źródła obowiązujące

| Skrót | Dokument / status / zakres |
|---|---|
| MC | `MASTER_CANON_AGENTPRO.md`, v2 APPROVED: WHAT; zwłaszcza §§5–6, 18–28, 38–58, 64, 69–87, 91, 102, 105–107. |
| ARCH | `ARCHITEKTURA_AGENTPRO.md`, v1.2 APPROVED: HOW; §§8–9, 14–44A, 57–79, 85–115, 132, 143–146. |
| ADR1 | `ADR-0001.md`, APPROVED/CLOSED: właściciele storage i rozdzielenie ochrony domenowej od locków. |
| NSE | `ADR-00XX_NARRATIVE_STATE_ENGINE.md`, ACCEPTED, 2026-09-14; przeczytano cały dokument §§1–41. TARGET ARCHITECTURE, nie deklaracja wdrożenia. |
| CCC | `CANONICAL_CHANGE_CONTRACT_AGENTPRO.md`, ACCEPTED v1.2: kontrolowany commit, operator, final guard; §15 rozszerza wejście o research PROJECT. |
| ADR2 | `ADR-0002_SERIES_GRAPH_CANONICAL_CHANGE.md`, ACCEPTED: wspólny graf, SERIES canonical change, lokalna transakcja series.db. |
| F4/F5 | `F004_RECOVERY_CONTRACT_AGENTPRO.md` IMPLEMENTED oraz `F005_CHAPTER_ARTIFACT_LINEAGE_CONTRACT_AGENTPRO.md` IMPLEMENTED v2. |
| STYLE | `ADR-Adaptive-Style-Composition.md`, ACCEPTED, z sparse semantics źródłowej biblioteki; sześć kontraktów, istniejący Composer, dwie bramki tego samego artefaktu. |
| G16/G17 | `docs/GAP016_MODEL_PROVENANCE.md`; `docs/GAP017_EVALUATION_CONTRACT.md`, ACCEPTED/FROZEN z RECOVERY v1. Statusy wykonania rozstrzyga nowsza roadmapa i closure, nie historyczny nagłówek kontraktu. |
| RM | `ROADMAPA_AGENTPRO.md`, istniejące ETAPY 0–20. ETAP 11 zawiera zamknięcia GAP-016 i GAP-017. |
| U | Bieżący załącznik użytkownika: lista Grok A–F i historyczna propozycja Kreion do porównania. Nie ustanawia SkillRegistry, RoutineEngine ani Development Memory jako nowych zaakceptowanych komponentów. |

Kontrakty GAP-007/008/013 sprawdzono również przez ich rzeczywiste schematy,
repozytoria i testy: `domain_records.py`, `memory_extraction.py`,
`project_repository.py`, `context_builder.py`, `context_runtime.py`.
Nie zakładano istnienia osobnego pliku o hipotetycznej nazwie kontraktu.

Historyczne statusy PARTIAL/NOT STARTED w raportach F-002 i w pierwotnych
sekcjach CCC zachowują opis wcześniejszego podzakresu. Nie przepisano ich jako
aktualnego stanu: SERIES sprawdzono w ADR2, kodzie i obecnych testach.

### Rejestr dowodów

| ID | Co rzeczywiście sprawdzono | Ograniczenie |
|---|---|---|
| E0 | `preflight.json`, `pre-status.txt`: checkpoint, dokładna lista status/path, pusty staging, ignorowanie nowych dowodów. | Nie jest hashem zawartości danych użytkownika. |
| E1 | Statyczny przegląd aktywnych wywołań i SQL; `scoped-code-search.txt`, `source-snapshot.json`. | Brak nazwy w wyszukiwaniu sam nie dowodzi braku funkcji; wnioski poniżej wynikają także z odczytu implementacji. |
| E2 | Zachowany `r01_reaudit/full-regression.log` i exit-code: **838 passed, 1 skipped, exit 0**, 1755.86 s. | Wynik wcześniejszego pełnego przebiegu, nie test uruchomiony w tym zadaniu. Nie dowodzi niezaimplementowanych funkcji ani jakości powieści. |
| E3 | Zachowane `gap017-final.xml`: **143 passed**; `integration-final.xml`: **100 passed**; oba exit 0. Odczytano logi/komendy, zaindeksowano 243 przypadki. 18/18 hashy finalnego snapshotu GAP-017 nadal zgodnych. | Snapshot obejmuje jawny zakres GAP-017, nie całe repo. Nie sumujemy 143+100 do wyniku regresji. |
| E4 | Odczytane testy canonical PROJECT/SERIES, grafu/impact, kontekstu, resume, transportu, lineage i stylu; konkretne symbole w macierzach. Historyczne raporty F002/F006 i PROJECT potwierdzają wcześniejsze wykonania ich podzakresów. | Sama definicja testu = test istnieje. Poza E3 nie przypisujemy każdej nazwie osobnego świeżego wyniku wykonania. E2 jest dowodem całej zachowanej bramki. |
| E5 | `proposal-source-search.txt`: przeszukane źródła główne, ADR, dokumenty docs i wskazany handoff nie dostarczają akceptacji Development Memory/Project Ledger ani nowego Skill/Routine Engine. | Nie mamy źródłowych załączników innych rozmów. Brak potwierdzenia akceptacji, nie twierdzenie, że nigdy jej nie było. |

Nowy katalog zawiera `reused-test-evidence.json` i `reused-junit-case-index.json`
wiążące E2/E3 z oryginalnymi dowodami. Nie uruchamiano pytest: nowe testy nie
były potrzebne do rozstrzygnięcia opisanych, statycznie widocznych granic.
NEW TARGETED TESTS = NOT RUN; NEW FULL REGRESSION = NOT RUN.
Nowe funkcje wymienione jako brakujące: wykonanie **NOT VERIFIED**.

Jedyny historyczny skip: `tests/test_110_canon_api_roundtrip.py:47`, legacy
`PATCH /canon/{book_id}` niewystawione przez aktywne app.main. Bez nowych skipów.
LIVE PROVIDER = NOT TESTED. PHYSICAL POWER LOSS = NOT TESTED.

## 3. Aktywna ścieżka i granice znaczeń

```text
app.main.agent_step (/agent/step)
  → runtime.run_agent_step
  → executor.execute_p20
  → build_runtime_context_package → ContextBuilder → zapisany ContextPackage
  → istniejący TOOLS[mode], StyleComposer/StyleCritic i Quality Evaluation
  → trwały wynik / F-004 projekcje / chapter_lineage
  → CanonService.process_accepted_artifact
  → EXTRACTOR + osobny VERIFIER → proposal → impact → guard
  → wymagana decyzja operatora → final guard → lokalny canonical commit
```

To mapa odpowiedzialności, nie jeden globalny commit: kontekst powstaje per
wywołanie; StylePerformance ma własne bramki, a zgoda/commit propozycji mogą
wystąpić później przez API operatora. PROJECT używa project.db, SERIES series.db.

`app/main.py:96–99` wywołuje P20. Executor importuje `app.tools.TOOLS` i wywołuje
rzeczywiste funkcje z tego rejestru. P0/legacy nie są podstawą poniższych DONE.

**Ważne ograniczenie wykonawcy:** `app/tools.py::tool_write` poza test mode
zwraca wejście z dopiskiem generatora offline; `tool_plan` jest stubem,
`tool_critic` produkuje stałe typy uwag, aktywne `tool_rewrite` wykonuje
deterministyczne dopiski. Potwierdza to macierz G16. Transport
`app.llm_provider_openai.call_text` jest podłączony do memory EXTRACTOR/VERIFIER
i researchu, nie do literackiego Writera. Metadane requested/effective model
nie dowodzą wywołania dostawcy.

StyleCritic oblicza zgodność zgłoszonego achieved genome z DNA/receptą;
`tool_write` kopiuje target genome do metadanych achieved. Jest to dowód
integracji kontraktów, **nie niezależnego pomiaru stylu wygenerowanej prozy**.
To ograniczenie docelowego ETAPU 9/12/13, bez wykazanego nowego regresyjnego
naruszenia zamrożonego GAP-017. Wdrożenie literackiego wykonawcy i pomiaru nie
może fabrykować osiągniętego stylu przez kopiowanie celu.

| Pojęcie | Obecna odpowiedzialność / granica |
|---|---|
| MODE | Rodzaj zadania, walidowany tryb i wybór narzędzia; nie poświadczenie autora. |
| ROLE/TEAM | Odpowiedzialność i konfiguracja zespołu/roli, odrębna od modelu. |
| PRESET | Zadeklarowana kolejność i parametry kroków; nie uniwersalny Skill ani harmonogram. |
| SKILL | Wymagana do porównania wersjonowana procedura; częściowy odpowiednik to registry narzędzi + preset + walidacja, bez kompletnego kontraktu lifecycle. |
| WORKFLOW | Przebieg orkiestratora i jego kroki; istniejąca kolejka P20 nie jest jeszcze pełną trwałą maszyną pause/resume. |
| ROUTINE | Kiedy wystartować zatwierdzony workflow; nie nowy tryb generowany przez model. |
| MEMORY | Stan domenowy i jego dowody, należące do właściwego projektu/serii. |
| STORAGE | SQLite i artefakty zarządzane przez istniejących ownerów; nie prywatna pamięć agenta. |

## 4. Grok Bot — sześć funkcji

DONE oznacza wyłącznie precyzyjnie nazwany podzakres. Całościowe A–F nie są
zamykane przez dowód jednej operacji. Nazwy nowych komponentów pozostają propozycjami.

| ID / wymaganie | Źródło i status | Owner, plik/symbol | Test / dowód | Stan | Dokładny brak → minimalne uzupełnienie |
|---|---|---|---|---|---|
| A. Trwały Run: ID, wersja workflow, postęp, IO, próby, błędy, historia | U-A; MC §§5,71–75; ARCH §§103–110 — wymaganie zaakceptowane | `runtime.run_agent_step`; `executor._bind_execution_input`, `_write_sequence_artifact`, `execute_p20`; `CanonService.save_run_state`; ProjectRepository | E1–E3; `tests/test_124_run_state_and_resume.py::TestP20RunState`; G17 testy bindingu/zmiany queue i input | PARTIAL | Są run/project/book/step, przypięty input+queue+preset, step artifacts, próby i trwałe envelope ocen/model calls. To realny częściowy odpowiednik wersji workflow. Brak jednolitego wersjonowanego lifecycle całego workflow i checkpointu następnego kroku obejmującego każdy tryb. `state.json` jest podsumowaniem po pętli. Rozszerzyć istniejącą orkiestrację/persistence, nie wprowadzać nowego runtime'u. |
| B1. Technical retry/recovery znanych operacji | CCC §§7–12, F4/F5, G16/G17 — ACCEPTED/IMPLEMENTED | `evaluation.start_evaluation`; `ProjectRepository.evaluation_transaction`; `executor._completed_step`; `CrossStoreRecoveryService.recover`; `operator_api.recover_quality_evaluation` | E2/E3; R01/R02, `test_r01_start_commit_is_recoverable_and_fenced`; `test_each_crash_boundary_recovers_after_reopen_without_duplicates` | DONE w granicach tych operacji | Odtworzenie COMPLETED+VALID, fenced recovery osieroconej oceny, pinned context, projekcje i receipt. Nie rozciągać gwarancji na wszystkie przyszłe procedury ani nie dodawać timeout takeover. |
| B2. Pause/resume całego workflow, bezpieczny stop backendu | U-B; MC §84; ARCH §§110–114,132 — cel zaakceptowany, szczegóły stanów wymagają kontraktu | `runtime.run_agent_step`; `canon_service.resolve_resume_run_id`; `executor.execute_p20`; `lock_service` | E1; test `test_resume_reuses_latest_run_id_for_same_book` dowodzi reuse run ID, nie wznowienia dowolnej kolejki | PARTIAL | `resume=True` wybiera właściwy run; technical retry to odrębna, silniej związana ścieżka. Brak aktywnego STOP_REQUESTED→PAUSED i ogólnej komendy wznowienia od ostatniego checkpointu. Utrwalić stan/pozycję zatwierdzonej kolejki i safe boundary w P20; obsłużyć pozostałe locki przez istniejącą warstwę operatora. |
| C1. Autor zatwierdza chronioną zmianę kanonu | U-C; CCC §§5–7, ADR2, G17-G — ACCEPTED | `canon_service.operator_proposal_review`, `record_operator_decision`, `commit_canonical_proposal`; `operator_api`; `DomainMutationGuard.evaluate_canonical` | E2/E4; `test_protected_operator_commit_and_legacy_file_cannot_bypass`; `test_protected_series_change_impact_approval_final_guard_and_reopen` | DONE w zakresie canonical approval | AWAITING_USER_APPROVAL, authenticated review/challenge, APPROVE/REJECT, stale detection i osobny final commit istnieją. QUALITY ACCEPT nie jest zgodą autora. Bez nowego równoległego Approval Engine. |
| C2. ApprovalGate jako trwałe zatrzymanie i dalszy ciąg całego workflow | U-C; ARCH §§85,110–115; Source Master MC §64 | Właściwy owner: P20 + istniejące API operatora; dziś CanonService zatrzymuje konkretną mutację | E1/E4; brak dowodu ogólnego workflow gate | PARTIAL | Brak ogólnego powiązania pending gate z wersją kolejki i kolejnym krokiem. Commit kanonu nie jest automatycznym wznowieniem wszystkich zadań. Rozszerzenie B2 o jawny resume condition; Source Master pozostaje osobnym ETAPEM 15. |
| D. Skill Registry | U-D — lista do porównania; MC §§38–40 / ARCH §§85,105–108 akceptują istniejące rozdzielenie ról i procedur | `executor.resolve_modes`, `_validate_modes`, `_resolve_step_team`, `_models_for_step`, `_preset_steps`; `app.tools.TOOLS`; kontrakt API `AgentStepRequest` | E1; E3 executor boundary; `test_p20_2_auto_retry_policy.py`, `test_p20_3_retry_feedback_loop.py` jako istniejące testy procedur | PARTIAL | Rejestr dozwolonych narzędzi, presetów i policy istnieje. Brak pełnego per-procedure kontraktu wersji, schematów IO i uprawnień do skutków. Najmniejsze rozszerzenie: formalizacja istniejącego registry/presetu i jego bindingu w A/B; nazwa/nowa warstwa SkillRegistry nie są konieczne ani zatwierdzone. |
| E. Routine Engine / kiedy uruchamiać procedury | U-E; MC §91, ARCH §143, RM ETAP 19 — scheduler jest późniejszym zakresem | Dziś caller `/agent/step` uruchamia preset, executor ma ograniczoną regułę quality_retry | E1: `_quality_retry_steps`; brak aktywnego trwałego schedulera/routine registry w P20 | NOT_REQUIRED_YET dla ogólnego Routine Engine | Zdarzeniowy/czasowy start zatwierdzonych workflows nie jest ukończony. Obecnej bounded retry nie nazywać routine engine. Po A/B i produkcyjnym workflow rozwinąć ETAP 19: trwały trigger, dedup, limity, fairness i zgody. Bez Computer Use/external triggers teraz. |
| F. Domain Events i idempotentne skutki | U-F; NSE §§5,24–25; CCC §12; F4/G17 — część zaakceptowana, nazwa Event Bus nie | CanonService + repozytoria + F4: `canonical_commit.v1`, proposal versions, operation envelopes, evaluation, artifact projection | E1–E4: receipt replay, rollback, reopen, brak duplikacji downstream | PARTIAL | Istnieją jawne zapisy skutków i identities, ale nie wspólny append-only zapis wszystkich domenowych zdarzeń/odrzuceń i projekcja od nich. Ta luka to N1/N2 poniżej, nie drugi brak do implementacji. Uzupełnić ownerów i kontrakt zdarzeń bez Event Busa. |

### Co znaczy „zamknąć aplikację”

1. **Zamknięcie UI:** utrwalony stan należy do backendu. Samo zamknięcie okna
   nie oznacza STOP_REQUESTED ani decyzji PAUSED. Nie wykonano nowego testu
   rozłączenia klienta w trakcie requestu; dalsze wykonanie konkretnego
   in-flight requestu po disconnect = NOT VERIFIED.
2. **Zatrzymanie backendu:** trwałe wyniki i operacje F4/G17 można odczytać po
   ponownym otwarciu. Osierocone PREPARED/RUNNING Evaluation wymaga jawnego
   operator recovery; nie wolno ponawiać losowo z latest context. Brak
   ogólnego safe-stop workflow pozostaje B2.
3. **Wyłączenie komputera:** SQLite, fsync i atomic replace są mechanizmami,
   a nie dowodem odporności na rzeczywistą utratę zasilania. E2/E3 obejmują
   wstrzyknięte awarie i reopen; fizyczny power-loss test nie został wykonany.

## 5. KreionAI — historia i ciągłość

| ID / wymaganie | Źródło i status | Owner, plik/symbol | Test / dowód | Stan | Dokładny brak → minimalne uzupełnienie |
|---|---|---|---|---|---|
| K1. Powieść: decyzje i uzasadnienia | U-Kreion historyczna propozycja; NSE §§5,13,16 oraz ARCH §27 — przyjęty odpowiedni cel | CanonService proposal/decision/receipt; `research.AuthorDecision`; project/series metadata | E1/E4; `test_mixed_operator_rejection_preserves_whole_set`; canonical/research approval tests | PARTIAL | Decyzje o kanonie i research fiction mają wiązania; nie każda decyzja autora/planistyczna ma wspólny historyczny zapis i własne uzasadnienie. Przyjęta ścieżka canonical decision przechowuje decyzję i impact, nie ogólny tekst reason od autora. Domknąć N1; nie tworzyć równoległego DecisionLedger. |
| K2. Powieść: zmiany i odrzucenia bez utraty historii | NSE §§5,7,9,13 — ACCEPTED | `ProjectRepository.apply_canonical_record_set`, odpowiednik SERIES; `save_canonical_proposal_for_review`, research operation history | E1/E4: `canonical_versions.v1`, wersje propozycji, immutable decision; mieszany commit i rollback | PARTIAL | Historia wersji kanonu i odrzuconych propozycji istnieje, ale nie uniwersalny append-only ledger wszystkich zdarzeń, human edits i retconów. Wspólna luka N1/N4. Obecne JSON envelopes/histories nie stają się z definicji append-only MemoryEventRecord. |
| K3. Powieść: źródło→run/step→artefakt/hash→decyzja | CCC §12, F5, G16/G17, NSE §16 — ACCEPTED | `chapter_lineage`; `executor` step_doc; ContextPackage; CanonService; ModelInvocationAudit/EvaluationRecord | E2/E3/E4; `test_api_p20_persists_complete_chapter_lineage_retry_and_isolation`; `test_api_p20_reaches_existing_transport_with_distinct_calls_context_audit_and_retry` | DONE dla wdrożonych pipeline'ów | Łańcuch artefaktu/wywołań/proposal/receipt jest realny. Ogólne ogniwo MemoryEvent i historia dowolnych human edits nadal N1; nie odbudowywać istniejącej lineage. |
| K4. Powieść: zmiana sesji/modelu, miejsce wznowienia i app-owned state | MC §§14,73; NSE §§28–32; U | Project/SeriesRepository, zapisane ContextPackages, canon source snapshots, model/evaluation envelopes; runtime resume | E1–E3; reopen/cache/recovery/isolation | PARTIAL | Trwałość nie zależy od pamięci modelu. Pełne odtworzenie pozycji całego workflow zależy jednak od B2, a znaczenie historycznego stanu od N3. Nowy model nie dostaje prawa do zmiany kanonu ani podmiany przypiętego model/context technical retry. |
| K5. Historia rozwoju AgentPRO przez Git/ADR/handoff/raporty | U — porównanie istniejących narzędzi; nie domena powieści | Git HEAD; zaakceptowane ADR; `docs/GAP017_*`; closure manifest i logi | E0/E2/E3; obecny checkpoint i zgodne hashe snapshotu | DONE jako ręcznie odtwarzalna dokumentacja sprawdzonego zakresu | Da się ustalić checkpoint, kontrakt, naprawy i bramki GAP-017. Nie jest to dowód bezstratnego zapisu każdej rozmowy ani wszystkich decyzji w historii aplikacji. Zachować istniejące źródła, nie kopiować do canon/memory książki. |
| K6. Automatyczne Development Memory / Project Ledger | U — historyczna propozycja; źródłowa akceptacja NOT FOUND w sprawdzonych dokumentach | Brak potwierdzonego zaakceptowanego ownera runtime; Git/ADR/raporty już pokrywają część potrzeby | E5; brak testu automatycznego odtworzenia wszystkich ustaleń | NOT_REQUIRED_YET | Najpierw osobna decyzja o wymaganej kompletności, źródłach i granicy development data. Nie ustanawiać nowego storage/komponentu przez ten raport. Nie blokuje pisania powieści. |

## 6. Narrative State Engine — pełne pokrycie ADR

NSE jest zaakceptowanym celem. Tabela nie utożsamia „target jeszcze niewdrożony”
z konfliktem architektonicznym. Nie wykazano powodu do zmiany kanonu ani
ponownego otwarcia zamkniętych GAP-ów.

| ID / wymaganie | Źródło i status | Owner, plik/symbol | Test / dowód | Stan | Dokładny brak → minimalne uzupełnienie |
|---|---|---|---|---|---|
| N1. Append-only MemoryEventRecord, odrębny od wydarzenia fabuły | NSE §5, §6 — ACCEPTED target | Docelowo istniejące Project/SeriesRepository. Dziś `domain_records.EventRecord` = wydarzenie fabuły; metadata canonical/model/evaluation = wyspecjalizowane historie | E1: sprawdzono SQL i producentów, nie tylko brak nazwy. E4 ma testy historii kanonu, brak testu wspólnego ledger/replay | MISSING jako wspólny kontrakt/proces | Nie ma API/store dopisującego niezmienne MemoryEvent z event identity, aktorem, payloadem i źródłami przez wszystkie właściwe ścieżki. Dodać do istniejących repozytoriów i transakcji; raw artifacts pozostawić w ich storage. Zakres i migrację zatwierdzić zgodnie z NSE §§7,39–40. |
| N2. Current state + historia wersji + projekcja z ledger | NSE §7, §31; GAP-007 fundament | `ProjectRepository.apply_canonical_record_set` / `SeriesRepository.apply_canonical_record_set`: bieżące rows i `canonical_versions.v1` | E1/E4: historia [1,2], current v2, rollback i reopen w `test_all_project_types_mixed_commit_operator_reopen_retry_and_isolation` | PARTIAL | Bieżący stan i historia są; brak deterministycznej materializacji całości z Memory Ledger. Nie nadpisywać historii migracją ani kopiować stanu do nowej pamięci. Po N1 dodać kontrolowaną projekcję/rebuild w obecnym ownerze. |
| N3. System time / world time / narrative order / query as-of | NSE §17; ARCH §§9,22,26,32,36 | `FactRecord.valid_from/to`, `CharacterState`, `KnowledgeEvent`, `EventRecord.narrative_order`; `ContextBuildRequest`; `list_structured_memory_records` | E1/E4: walidacja pól czasowych i `test_recency_uses_explicit_metric_not_entity_id`; brak testu pełnego query-as-of | PARTIAL | Pola czasu istnieją. Retrieval czyta aktualne payloady; request nie ma kontraktu trzech osi czasu ani as-of snapshotu. Narrative_order nie jest filtrem „wiedza dostępna w tej scenie”. Zdefiniować semantykę granic, potem dodać zapytania/projekcję w repository i podłączyć do istniejącego buildera. |
| N4. Superseding i retcon domenowy | NSE §§8.4,9 — ACCEPTED target | CanonService oznacza zastąpioną wersję proposal `superseded`; historie rekordów przechowują wersje; guard przyjmuje CREATE/UPDATE/REPLACE | E1: brak retcon lifecycle; version/stale tests nie dowodzą retconu | MISSING jako operacja retcon | Superseded proposal nie jest RETCONNED/SUPERSEDED stanem faktu. Brak jawnego supersedes/superseded_by i temporal impact całego retconu. Rozszerzyć istniejący proposal/CanonService po N1/N3; zachować old/new/reason/authority i ochronę. |
| N5. Rozdzielenie world truth i wiedzy/przekonań postaci | NSE §18; MC §§22–24; ARCH §§36–37 | `FactRecord`, `KnowledgeEvent.knowledge_status`, `CharacterState`; `EdgeRelationType.KNOWS/BELIEVES`; ekstrakcja ośmiu typów | E1/E4: mixed records i wiedza jako osobny rekord/graf; series knowledge transfer | PARTIAL | Rozdzielenie danych jest. Brak pełnego wyznaczania epistemicznego stanu POV na chwilę/scenę, zasad aktualizacji belief i zapobiegania ujawnieniu przyszłej wiedzy. Po N3 rozbudować istniejące projekcje/retrieval, nie utożsamiać belief z kanonem świata. |
| N6. Wiedza czytelnika | MC §23; ARCH §38; NSE temporal/knowledge intent | `SceneContract.reader_knowledge_added` zawiera referencje. W aktywnych modelach brak pełnego ReaderKnowledgeEvent i jego projekcji | E1: pole referencji nie ma odpowiadającego procesu odczytu/zapisu; functional proof NOT VERIFIED | PARTIAL (referencje), brak właściwego procesu | Wdrożyć zaakceptowany typ i jego historyczny stan/revelation filtering w istniejącej pamięci po N1/N3; test spoilerów przed/po scenie i niezależności od wiedzy postaci. |
| N7. Origin / authority / protection / canon state jako osobne osie | NSE §8; CCC; ADR1/2 | `DomainMutationGuard.evaluate_canonical` authority/policy; proposal.source_kind/provenance; FactRecord reality/verification/protection; SeriesStateKind | E1/E4: testy authority, flags, stale i research evidence | PARTIAL | Ochrona i authority nie są confidence i to działa w wdrożonych wariantach. Brak jednolitego pełnego origin/canon_state w całym stanie; reality_status lub SeriesStateKind nie zastępuje ACTIVE/SUPERSEDED/RETCONNED. Doprecyzować additive schema zgodnie z NSE §39; nie zmieniać authority/preimage hashy przy okazji. |
| N8. Frozen i author_locked, legacy fail-closed | NSE §8.3; ADR1; CCC §§5–7; ADR2 | `DomainMutationGuard.evaluate`, `evaluate_canonical`, final physical repository guard | E2/E4; `test_independent_protection_flags`, `test_legacy_unknown_protection_and_derived_rebuild_remain_blocked`, odpowiednik SERIES | DONE dla wdrożonego canonical pipeline | Nie odblokowywać legacy bez flag. Zgoda nie zdejmuje flag. Nowy ledger/projekcje mają zachować te same reguły, nie zastąpić guarda. |
| N9. Extraction candidate ≠ proposal, niezależna weryfikacja | NSE §§10–11,28; CCC §§1–3,13 | `executor.invoke_memory_model`; `tools.memory_integrity_provider`; `memory_extraction`; `canon_service.process_accepted_artifact` | E2/E4; `test_api_p20_reaches_existing_transport_with_distinct_calls_context_audit_and_retry`; verifier reject/invalid JSON cases | DONE do granicy SDK w wdrożonym zakresie | Osobne wywołania i konteksty, walidowany cały zestaw, brak promocji po błędnej weryfikacji. LIVE PROVIDER NOT TESTED; nie mylić z jakością ekstrakcji na realnej powieści. |
| N10. Proposal→impact→policy/authority→zgoda→final guard→CanonService | NSE §§10–13; CCC; ADR2 | `commit_canonical_proposal` + `canonical_proposal_transaction` + `apply_canonical_record_set` | E2/E4: chronione PROJECT/SERIES, STALE, auth, rollback audytu, retry receipts | DONE dla obsługiwanych CREATE/UPDATE/REPLACE | Scope-local BEGIN IMMEDIATE wiąże final validation i zapis. Nie dowodzi retconu, wyjątku PROJECT względem SERIES ani rebuild pochodnych. Te braki są odrębnie poniżej. |
| N11. PROJECT/SERIES i izolacja | NSE §§4,14,30–32; ADR1/2 | `StorageResolver`, `SeriesAccessContext`, `SeriesRepository`, scope-aware `GraphNodeRef`, wspólny traversal | E2/E4; `test_series_foreign_membership_and_scope_do_not_fallback_to_project`; `test_series_graph_access_and_cross_series_isolation`; E3 runtime identity | DONE w zaimplementowanych granicach | series.db jest ownerem SERIES; brak PROJECT fallback. Zachować lokalność transakcji. Nie deklarować pełnego sagi workflow na podstawie tego podzakresu. |
| N12. Konflikty PROJECT/SERIES i jawne wyjątki | NSE §15,19; ARCH §28 | `ContextBuilder._deduplicate_project_series`; research ConflictRecord i operator decision; CanonService | E3: `test_f007_different_variant_is_preserved_with_warning`; E1 `_validate_review_basis` i STALE | PARTIAL | Warianty nie znikają: zostają oba i mandatory warning. Brak wspólnego procesu UPDATE_SERIES / EXPLICIT_PROJECT_EXCEPTION / REJECT / ESCALATE z trwałym wyjątkiem i jego semantyką odczytu. Dopisać do obecnego proposal/guard/repository po N1; nie rozstrzygać rankingiem ani silent override. |
| N13. Provenance wersji, propozycji, źródeł, run/step/artifacts | NSE §16; CCC §12; F5; G16/17 | CanonService pipeline evidence/receipt, chapter_lineage, ContextPackage, ModelInvocationAudit i EvaluationRecord | E2–E4; chapter lineage i transport/context/audit/retry tests | PARTIAL dla pełnego NSE; wdrożone łańcuchy DONE | Brakuje wspólnego ogniwa MemoryEvent→FactVersion/decision oraz wszystkich human edits; powiązać N1 z istniejącymi IDs i hashami. Nie regenerować starych hashy ani fałszywej proweniencji historycznej. |
| N14. Contradiction handling | NSE §19; MC §106; ARCH §28 | `research` conflicts; ContextBuilder warning; canonical snapshot/version checks | E1/E3/E4; F007 variants i research uncertain/disputed checks | PARTIAL | Konflikt wersji/danych wejściowych i research ≠ wszystkie sprzeczności domenowe/timeline/knowledge. Dodać kontrolowane reguły temporal/knowledge po N3/N5 i trwałe resolution w istniejącym procesie decyzji. Nie dopisywać LLM-owi authority. |
| N15. Mandatory-first / structured-first, jeden ContextBuilder | NSE §§20–23; MC §§43–52; ARCH §§57–76; GAP-013 | `ContextBuilder._collect_candidates`, `_select`, `_candidate_representations`, `reuse_for_retry`; `context_runtime.build_runtime_context_package`; executor | E3: mandatory/overflow/semantic/isolation/retry/hash tests; E4 API transport test pokazuje oba zapisane konteksty | DONE dla obecnych źródeł i polityk | Realnie wpięty P20; mandatory ma pierwszeństwo, semantic nie zastępuje wymaganych danych. Nowe ledger/as-of źródła trzeba podłączyć tutaj. Brak filtrowania historycznego to N3, nie brak obecnego buildera. |
| N16. Supporting history / opcjonalny semantic retrieval | NSE §§22–23,39 — target, embedding szczegóły odroczone | ContextBuilder SemanticRetrievalProvider i provenance; structured retrieval działa bez niego | E3: `test_no_semantic_provider_does_not_break_structured_builder`, `test_semantic_retrieval_is_late_supporting_layer_with_provenance` | PARTIAL | Opcjonalna granica providera istnieje; ogólnego retrievalu Memory Ledger jeszcze nie ma. Podłączyć supporting history po N1/N3; żywy embedding/index nie jest warunkiem pierwszego wdrożenia. |
| N17. Atomowość lokalna, DB/artefakty, retry i recovery | NSE §§6,24–25; CCC; F4/F5; G17 | Scope-local CanonService/repos; `CrossStoreRecoveryService`, `chapter_lineage`, Evaluation fencing | E2–E4; crash boundary/reopen/rollback, R01/R02, retry idempotency, immutable result | DONE dla obecnych protokołów | series.db local commit nie wymaga F4. F4 służy realnym granicom storage. Nie ma globalnej transakcji wszystkich plików/DB. Nowy MemoryEvent i bieżący stan muszą współdzielić lokalną transakcję; ewentualna projekcja plikowa użyje F4. |
| N18. Impact Analysis | NSE §26; MC §87; GAP-010/011 + ADR2 | `impact_analysis.analyze_impact`, `project_graph`, repository traversal; `CanonService._canonical_impact` | E2/E4; `test_direct_transitive_derived_and_provenance`, `test_authorized_series_impact_uses_series_repository_without_project_fallback` | DONE jako bounded graph impact; PARTIAL względem temporal NSE | Jawne ścieżki i zakres istnieją. Implementacja wprost nie deklaruje temporal filtering ani nieodnotowanych dependencies. Wzbogacić graf/semantykę po N3, zachowując jeden traversal i jawny coverage. |
| N19. Invalidation i odbudowa pochodnych | NSE §27; MC §85; ARCH §§77–78; CCC §8 | `CanonService._canonical_impact`, receipt.invalidation; builder tworzy nowy kontekst; historyczne pakiety pozostają niezmienne | E1/E4: `DERIVED_REBUILD_UNSUPPORTED`, tests PROJECT/SERIES fail-closed | PARTIAL | Istnieją oznaczenia affected_ids/HISTORICAL_ONLY/NEXT_CONTEXT_BUILD. Operacja wymagająca DERIVED rebuild jest blokowana. Brak kompletnego version-aware invalidation/rebuild summary/evaluation/manuscript/translation. Potrzebny własny kontrakt pochodnych. F4 odzyskuje zapis, nie wylicza poprawnej treści pochodnej. |
| N20. LLM jako wymienny wykonawca, nie pamięć/owner prawdy | NSE §§28–34; MC §§14,39; ADR1 | app.main→P20, repozytoria, guard, ContextBuilder; wyjątki/odmowy walidacji zamiast success fallback | E1–E4; transport errors/reject, context scope, auth tests | DONE dla sprawdzonych ścieżek | Zachować granicę przy rozbudowie. Debug/legacy nie stanowią dowodu runtime. Brak podstaw do nowego memory.db, Neo4j, CanonManager z prawem zapisu ani ContextAssemblera. |
| N21. Testability, skalowanie i szczegóły późniejsze | NSE §§35–41, zwłaszcza §§37–40 | Istniejące tests i repozytoria; przyszłe rozszerzenia ich testów | E1–E4; brak proof append-only/as-of/retcon, milionowego benchmarku i fizycznej awarii zasilania | PARTIAL dla §37; NOT_REQUIRED_YET dla §38 | Testy nowych własności powstają z implementacją N1–N19. Milion zdarzeń, embedding/index, finalne SQL/API i pełne enumy nie są dostarczonym kontraktem pierwszego wdrożenia. Nie udawać akceptacji tych szczegółów. |

### Rejestr pokrycia całego ADR

| Sekcje NSE | Miejsce rozliczenia |
|---|---|
| 1–3 cel, kontekst, target | §§1–2 raportu, rozdzielenie decyzji i implementacji |
| 4 storage; 5 ledger; 6 artifacts; 7 current state | N11, N1, N17, N2 |
| 8 osie/protection; 9 retcon | N7–N8, N4 |
| 10–13 proposal/validation/CanonService/audit | N9–N10, N13, K1–K3 |
| 14–15 scope/konflikt; 16 provenance | N11–N13 |
| 17 czas; 18 wiedza; 19 sprzeczności | N3, N5–N6, N14 |
| 20–23 builder/mandatory/structured/supporting | N15–N16 |
| 24–25 atomowość/idempotencja; 26 impact; 27 invalidation | N17–N19 |
| 28 model; 29/29.1 istniejące granice/GAP-y; 30–32 właściciele danych | N20, N11, plan poniżej |
| 33–34 zakazy/odrzucone alternatywy | N20 i niezmieniane fundamenty poniżej |
| 35–36 korzyści/koszty; 37 testy; 38–40 odroczenia/decyzja wdrożenia | N21, ryzyka i bramki planu |
| 41 podsumowanie docelowej architektury | Łączny werdykt PARTIAL; nie deklaracja ukończenia targetu |

## 7. Jeden uporządkowany plan

To rekomendacja, nie zmiana roadmapy i nie otwarcie nowego GAP-018. Numery P1–P8
poniżej oznaczają wyłącznie kolejność w tym raporcie. Wcześniejsze etapy roadmapy
mają wdrożone podzakresy; CLOSED pojedynczego GAP nie zamyka automatycznie całego
etapu docelowego. Nie proponuję pomijania wcześniejszych zależności.

### Fundamenty pozostające bez zmian

P20 jako jeden runtime; app.main jako API; project.db / series.db /
agentpro_system.db i obecne repozytoria; shared graph/traversal; DomainMutationGuard;
Canonical Change + uwierzytelnienie operatora; ContextBuilder i pinned retries;
F4/F5; sparse Style Library, DNA, Composer, oddzielne StyleEvaluation i Quality;
GAP-015 research, GAP-016 model provenance i GAP-017 trwałe Evaluation.
Nie zmieniać zamrożonych hashy, authority, routingu, progów ani ochrony legacy.

| Kolejność / mapowanie | Minimalny zakres i zależność | Definition of Done i główne ryzyko | Znaczenie dla powieści |
|---|---|---|---|
| P1 — RM ETAP 3: Memory Ledger i projekcja bieżącego stanu, rozszerzenie GAP-007/008 | N1/N2 oraz wspólny brak Grok-F/K1/K2. Najpierw decyzja wdrożeniowa wymagana przez NSE §40: wersja event schema, coverage producentów, bootstrap/migracja istniejących danych i granica PROJECT/SERIES. Potem zapis zdarzenia i skutku w jednej lokalnej transakcji, źródła/hash bez nowego storage. | Append-only wymuszone w ownerze; accepted/rejected/change event mają stable ID i references; retry nie duplikuje; rollback nie zostawia event bez skutku lub skutku bez event; reopen/projekcja dają ten sam stan; dwa projekty/dwie serie izolowane; brak fabrykowania historii starych danych. Ryzyko: migracja i błędne uznanie zmiennego envelope za immutable ledger. | Blokuje deklarację bezstratnej historii i pełnego NSE. Nie blokuje obecnego pojedynczego kroku WRITE. |
| P2 — RM ETAPY 5–7 i 3: temporal/knowledge/retcon/conflict | Po P1: N3–N7, N12, N14; stan na scene/world/system time, wiedza POV/czytelnika, superseding/retcon i jawny wyjątek PROJECT/SERIES. Doprecyzować nierozstrzygnięte semantyki w osobnym kontrakcie; zachować CanonService/guard. | Syntetyczna historia z retrospekcją, fałszywym przekonaniem, ujawnieniem czytelnikowi i retconem daje powtarzalne odpowiedzi przed/po; stare fakty audytowalne; wyjątek bez zgody/stale nie działa; poprawny ContextPackage nie zdradza przyszłej wiedzy. Ryzyko: pomieszanie czasu zapisu, świata i kolejności narracji. | Blokuje wiarygodną ciągłość długiej powieści/sagi; nie można zastąpić większym promptem. |
| P3 — RM ETAP 4 i 8: pochodne i context integration | Po P1/P2: N18/N19 oraz historyczne źródła N15/N16. Zatwierdzić kontrakt stale/invalidation/rebuild według source versions. F4 tylko dla fizycznej granicy store. | Zmieniony fakt oznacza właściwe pochodne jako stale; nieaktualna ocena nie akceptuje nowej wersji; rebuild jest idempotentny i odtwarzalny; historical retry zachowuje dawny kontekst; nowe wykonanie używa nowego stanu. Dopiero taki dowód pozwala ograniczyć DERIVED_REBUILD_UNSUPPORTED. | Blokuje bezpieczne duże poprawki/retcony istniejącej książki; poprawny fail-closed dziś jest celowy. |
| P4 — RM ETAP 12, zależności safe-stop z 20: trwały workflow Run | Po P1 i przed długimi wykonaniami. A/B2/C2: wersjonowana/pinned procedura, postęp kolejki, STOP_REQUESTED→PAUSED, awaiting approval, resume od checkpointu. Rozszerzyć runtime/executor/repository i operator API; nie nowy engine. | Awaria przed/po każdym kroku, BOOK_LOCKED, duplikaty i reopen zachowują IDs/kolejkę; pojedyncze wykonanie skutku; bez latest substitution; autor odmawia/zgadza się na dokładny gate; błąd bindingu = intervention. Ryzyko: potraktowanie reuse run_id jako recovery workflow. | Blokuje bezobsługowe, wielosesyjne wykonanie pełnej książki; UI-close nie jest gwarancją pause. |
| P5 — RM ETAPY 9/11/12/13: rzeczywisty pipeline literacki | Po wcześniejszych zależnościach. Podłączyć Writer i właściwe role przez istniejący transport, routing, ContextPackage i G16, z zachowaniem G17. Ocena osiągniętego stylu musi dotyczyć tekstu, nie kopii target genome. Obecna procedura/preset to punkt wyjścia; doprecyzowanie procedur D ma nastąpić tutaj, bez nowego Skill Engine. | API/P20 wykorzystuje zapisany kontekst i abstract style; SDK-boundary proof, błędy/fencing/retry, realne IO i provenance; osobny autoryzowany live-provider proof oraz ocena jakości prozy; REVISE/REJECT bez StylePerformance; kanon tylko po własnych bramkach. Ryzyko: metadane modelu lub deterministyczny stub przedstawione jako generacja/ocena literacka. | Bezpośredni blocker generowania kompletnej powieści przez AgentPRO. Obecny użytkownik może dostarczyć tekst, ale to nie zastępuje tego pipeline'u. |
| P6 — RM ETAPY 13–15: pętla poprawy, Book QA i Source Master | Po P2–P5: rozszerzyć kontrolowane poprawki, hierarchical QA całej książki, finalne approval autora i wersjonowany master. Trwałe Evaluation G17 pozostaje ownerem oceny, nie jest zastępowane. | Syntetyczna wielorozdziałowa książka z celowymi błędami arcs/knowledge/threads/style; poprawki tylko dotkniętych części, limit/escalation; brak finalnego mastera bez użytkownika; lineage wersji. Ryzyko: potraktowanie ACCEPT sceny jako jakości całej książki. | Blokuje deklarację ukończonej i zatwierdzonej książki. |
| P7 — RM ETAPY 18–20: operator UX, scheduler/routines, skala i hardening | Po stabilnym workflow/gates: oficjalne API, routine trigger/dedup, limity i fairness; backup/restore, process-kill i osobno fizyczny power-loss proof. Nie dokładamy external triggers/Computer Use bez osobnej akceptacji. | 7 projektów bez leakage, deterministyczny start zatwierdzonych procedur, bez ukrytej authority UI/modelu; prawidłowy stop/resume i dowody trwałości. Ryzyko: skalowanie niepełnego lifecycle. | Scheduler i Computer Use mogą poczekać przy jednej książce; podstawowa odporność na przerwanie nie może. |
| P8 — osobna propozycja: Development Memory; późniejsze RM 16/17 | Development Memory tylko po akceptacji zakresu i ownera, poza novel memory; translation/publication zgodnie z roadmapą dopiero od Source Master. | Dowód odtworzenia decyzji ze źródłami i brak mieszania domen; dla tłumaczeń jawny start i lineage zatwierdzonego mastera. | Nie blokuje napisania źródłowej powieści. Nie powstaje nowy GAP ani obowiązkowy komponent w tym raporcie. |

W P1–P3 nie można ominąć istniejących blokad w imię „domknięcia” NSE. Szczególnie
`DERIVED_REBUILD_UNSUPPORTED` nie jest usterką recovery F4. Potrzebuje znaczenia
odbudowy danego pochodnego obiektu, którego sam outbox nie dostarcza.

### Jedno następne rekomendowane zadanie wdrożeniowe

**ETAP 3 — minimalne wdrożenie append-only Memory Ledger i powiązania z bieżącym
stanem w istniejących repozytoriach oraz CanonService (P1).**

To najwcześniejsza wspólna zależność Narrative State, domenowego Decision Ledger
i Domain Events. Zadanie powinno zacząć się od małego kontraktu wykonawczego
wymaganego przez NSE §§7,39–40: dokładny event schema/coverage, mapping istniejących
receipts, bootstrap historycznych danych i granice transakcji. Akceptacja tego
kontraktu jest bramką **przed migracją i implementacją**; sam obecny raport nie
udziela jej. Dalej jeden przebieg implementacji i dowodów w istniejących ownerach,
bez drugiej pamięci i bez drugiego Event/Recovery Engine. Nie zaczęto tego zadania.

## 8. Samokontrola kompletności i końcowy stan

- Grok A–F rozliczone, z rozdzieleniem UI/backend/power loss i quality/author approval.
- Kreion: osobno powieść i rozwój aplikacji; Development Memory bez nieudowodnionej akceptacji.
- NSE: wszystkie sekcje 1–41 zmapowane; ledger odróżniony od fabularnego EventRecord,
  obecnych receipts/historii i ogólnego workflow; wspólne braki policzone raz.
- Stan potwierdzony kodem oddzielony od istniejących wyników testów i od rekomendacji.
- Brak nowych testów, pełnej regresji, wywołań modeli lub pracy na realnych projektach.
- Nie ma nowej decyzji architektonicznej. Szczegóły migracji/temporalności,
  pochodnych, workflow gates i Development Memory nie zostały samowolnie zatwierdzone.
- Ten przegląd jest własną analizą pokrycia; INDEPENDENT AUDIT = NOT PERFORMED.
- Nowy raport jawnie doliczony do autoryzowanych zmian. Końcowa kontrola
  dokładnych status/path, hashy odczytanych źródeł i scoped diff/check jest zapisana
  w `final-verification.json` oraz `report-diff-check.txt` w katalogu dowodów.

Potwierdzony zakres końcowy: 2546 zachowanych wpisów baseline + 1 nowy raport
(2547 wpisów); BASELINE DELTA = 0; STAGING = EMPTY; HEAD bez zmian.
37 hashy jawnych źródeł pozostaje bez zmian. Scoped diff/check nie wykazał
błędów whitespace; porównanie nowego pliku przez no-index ma exit 1 wyłącznie
z powodu obecności różnicy względem pustego pliku.
Nie jest to CLEAN worktree. COMMIT/PUSH = NOT PERFORMED.
