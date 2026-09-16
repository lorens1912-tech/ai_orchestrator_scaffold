# GAP-016 — Model parameter provenance

## Addytywny kontrakt v1

Źródła: MASTER_CANON_AGENTPRO v2 §38–43, §51–58, §70–72 oraz pełny audit;
ARCHITEKTURA_AGENTPRO v1.2 §104–110; ADR-0001 §5–8;
Canonical Change Contract 1.2 (ekstrakcja 1.0 / research proposal 2.0).
Rozszerzenie nie zmienia authority ani preimage istniejących hashy.

Właścicielem trwałości jest istniejący `ProjectRepository`, `project.db`,
`project_metadata/model_invocation.v1:<operation_id>`. Nie powstała migracja
SQL, drugi router ani globalny magazyn wywołań. Wywołania ekstrakcji SERIES
należą do uruchamiającego projektu; envelope wskazuje scope SERIES oraz
series_id, a kanoniczne dane SERIES nadal należą wyłącznie do series.db.

`ModelInvocationAudit` jest envelope wykonania istniejącego ModelInvocation
(`operation_id = call_id`), poza zamrożonym dowodem. Każde realne wykonanie SDK
ma attempt_id, ordinal, czas START/END, parametry SENT, status i jawny wynik
zdalny. Logical invocation ma własne ID. Nie zmieniono `ModelInvocation`
ekstrakcji, research invocation, ContextPackage ani ich serializacji/hashów.

## Cztery warstwy

- REQUESTED: dotychczasowa requested identity, surowe routing_inputs oraz
  request_sources z zachowaniem kluczy nieobecnych i null. W adapterze
  `request.parameters` rozróżnia brak temperature od jawnego null.
- RESOLVED: snapshot rzeczywistego ModelDecision i wejść polityki, wersja
  `model_policy.v1`, policy_hash. Dotychczasowe `to_dict()` jest bez zmian.
- SENT: model i dokładne parametry z kwargs SDK. Treść wejścia zastępuje hash
  oraz wskazanie pola input/messages; tekst pozostaje we własnym storage.
- PROVIDER-REPORTED: tylko zwrócony model, ID odpowiedzi/requestu i zmierzone
  liczniki tokenów. Nieznany snapshot/model_version pozostaje null. Wysłanie
  temperature nie oznacza potwierdzenia wewnętrznej wartości przez model.

Aktywny transport przyjmuje temperature; nie dodano top_p, seed, reasoning,
limitów ani narzędzi do istniejących wywołań. Pamięć i research nadal jawnie
przekazują temperature=None, więc SDK nie otrzymuje tego parametru.
Dotychczasowy fallback usuwa wyłącznie temperature po jej konkretnym
odrzuceniu. Nie zmienia modelu, promptu ani ContextPackage. Niezwiązane błędy
nie uruchamiają tego fallbacku. Odmowa STRICT nie uruchamia SDK.

## Trwałość i recovery

START jest commitowany przed SDK; END po odpowiedzi/błędzie, przed użyciem
wyniku. Żadna transakcja SQLite nie obejmuje wywołania sieciowego.
Zapis wyniku transportowego w tym samym projekcie służy odzyskaniu dokładnie
tej operacji, nie wyszukiwaniu podobnych ocen ani Evaluation Cache GAP-017.
Ponowienie ukończonego poprawnego wyniku nie wykonuje SDK. Istniejący retry
FAILED po niepoprawnej odpowiedzi researchu zachowuje jej wynik i hash w
historii prób, a wykonuje nową próbę z tym samym przypiętym wejściem. Zmienione wejście/model/parametry/API
tej samej operacji są odrzucane. Snapshot polityki jest przypięty.

Osierocony START oraz odebrana odpowiedź bez trwałego wyniku wymagają jawnego
recovery. Istniejące operatorskie recovery researchu odgradza również attempt
telemetrii. Późna odpowiedź odgrodzonej próby nie może zapisać wyniku.
Niepewny wynik zdalny pozostaje UNKNOWN; odebrana odpowiedź pozostaje znanym
RESPONSE_RECEIVED nawet po utracie zapisu wyniku. Nie deklarujemy exactly-once
u dostawcy. Przerwana ekstrakcja kanoniczna nadal podlega istniejącemu
PIPELINE_OUTCOME_REQUIRES_REVIEW; telemetria nie omija tej blokady.

Status transportu, walidacja odpowiedzi i wynik jakości są odrębne.
Walidacja researchu i jego wynik domenowy są zapisywane w jednej transakcji.
Błąd tego zapisu wycofuje claims, pozostawiając stan wymagający recovery.
Odmowa/niepoprawny JSON nie stają się sukcesem ani pustą ekstrakcją.

Zainstalowany SDK OpenAI 2.15.0: sprawdzono lokalny `_base_client.BaseClient`
(`_should_retry`). SDK może wykonywać wewnętrzne retry; zapisujemy rzeczywistą
konfigurację max_retries, wersję SDK i ograniczenie obserwowalności.
`http_attempt_count=null`, `http_retry_observability=NOT_INSTRUMENTED`.
Liczba aplikacyjnych prób nie jest deklaracją liczby żądań HTTP.

## Powiązanie i odczyt

Envelope wiąże project/book/series/scope, run/step/operation, role/mode,
ContextPackage ID/hash, wejście, output_hash i źródłowe artefakty. Zapisane
metadane są projektowane do istniejących step artifacts, audit.json oraz
run_state w odpowiedzi /agent/step; odczyt
operatorski research/records zawiera powiązane model_provenance. Projekcja
nie zawiera surowej odpowiedzi. ContextBuilder używa nadal niezmienionego
research_context; telemetryczne ID/czasy nie wpływają na jego hash.

Dla historycznych operacji bez envelope niczego nie uzupełniamy: brak śladu
oznacza NOT RECORDED. Starsze dowody zachowują pierwotną serializację.
Sekrety, pełne env/config/SDK i komunikaty wyjątków nie są kopiowane do audytu.

## Macierz aktywnych wywołań

| Ścieżka | Routing i adapter | Trwały dowód |
|---|---|---|
| /agent/step → P20 → canonical PROJECT/SERIES → EXTRACTOR | executor._models_for_step → invoke_memory_model → tools.memory_integrity_provider → call_text | model_invocation.v1, context, canonical evidence, audit.json |
| Niezależny VERIFIER pamięci | odziedziczona rzeczywista decyzja kroku, osobny ContextPackage i SDK call | odrębne invocation/attempt, wspólny run i source artifact |
| /operator/.../research/execute EXTRACT/VERIFY | resolve_model → research.run_research → _model_call → call_text | osobne ślady, research operation, operatorski odczyt |
| /agent/step FACTCHECK | routing P20 → research.factcheck → ten sam run_research | step artifact, audit.json, research operation |
| WRITE/REWRITE/CRITIC/QUALITY/STYLE i pozostałe lokalne tools | deterministyczne/offline, brak LLM | bez fikcyjnych SDK attempts |
| ContextBuilder / semantic retrieval | lokalne struktury/retrieval, brak aktywnego embedding API | zachowane dotychczasowe provenance indeksu |
| debug_model_router / openai_direct, llm_client, team_runner | debug/legacy poza aktywnym P20; compat shim nie wykonuje produkcyjnego transportu P20 | nie aktywowano ani nie przebudowano |

## Kanoniczna serializacja

Fingerprint v1: SHA-256 z UTF-8 JSON, sort_keys=True, separators=(",", ":"),
ensure_ascii=True, allow_nan=False. configuration_hash nie zawiera wejścia,
czasu ani losowych ID; input_fingerprint wiąże input_hash i context_hash.
Trwały wynik ma dodatkowy result_hash sprawdzany przed ponownym użyciem;
uszkodzona odpowiedź lub metadane nie są odtwarzane jako poprawny wynik.
Nie deklarujemy deterministycznego tekstu dostawcy na podstawie parametrów.

## Granice

LIVE ADAPTER SMOKE = NOT RUN. Testy zastępują wyłącznie granicę SDK;
nie dowodzą jakości literackiej ani zachowania żywego dostawcy.
Ograniczenia GAP-015 pozostają: tekstowy PROJECT research, bez URL fetch,
binarnego PDF/OCR i samodzielnej promocji do SERIES.
GAP-017 = NOT STARTED. Wyniki bramek: [GAP016_REPORT.md](GAP016_REPORT.md).
