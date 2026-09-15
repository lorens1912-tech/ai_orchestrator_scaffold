# F-002 — istniejące rekordy PROJECT: raport podzakresu

Data: 2026-09-15. Baza: `82f90501ed7015d52e3e57c72df9631e371aaaa1`.
Kontrakt: `CANONICAL_CHANGE_CONTRACT_AGENTPRO.md` v1.1 ACCEPTED, bez zmian.
Uzupełnienie historycznego `F002_F006_IMPLEMENTATION_REPORT.md`.

## Status i zakres

F-002 = PARTIAL / NOT CLOSED. F-006 = PARTIAL / NOT CLOSED.
SERIES nie rozszerzano. F-004 = NOT STARTED. GAP-014 = NOT STARTED / wstrzymany.
Commit zapisuje wyłącznie zweryfikowany podzakres PROJECT.

## Przyczyna i zmiany

Dotychczas CanonService odrzucał każdy zweryfikowany zestaw zawierający typ inny
niż FACT, a schema dostawcy obejmowała wyłącznie FactRecord. Siedem istniejących
typów GAP-007/GAP-008 nie miało jawnych pól ochrony kanonicznej.

- `domain_records.py`, `memory_extraction.py`: opcjonalne, walidowane pola
  `frozen` i `author_locked` w istniejących klasach. Brak wartości zachowuje
  dotychczasową serializację starych rekordów; nie oznacza zgody na zmianę.
- `executor.py`: wspólny rejestr ośmiu istniejących schematów w wejściu modelu.
- `canon_service.py`: wspólna walidacja typów i referencji całego zestawu;
  mapowanie istniejących typów na namespace grafu wyłącznie na granicy analizy.
  Typ pamięci w propozycji, historii i storage pozostaje oryginalny.
- `domain_mutation_guard.py`: stary rekord bez jawnych bool ochrony blokuje
  aktualizację (`CURRENT_PROTECTION_UNKNOWN`). Nie wykonano migracji ani
  automatycznego odblokowania. Kandydat bez flag blokuje cały zestaw.
- `context_builder.py`: naprawiono odtworzony błąd następnego kroku po zapisie
  THREAD/SETUP z domenowym `importance=8/7`. Kontrakt nie definiuje przeliczenia
  na skalę rankingu 0–1. Wartość pozostaje w payloadzie; wynik rankingu jest
  niedostępny według istniejącej polityki, z jawnym uzasadnieniem w kontekście.

## Typ → obsługa → rzeczywisty dowód

Wszystkie poniższe typy są obsługiwane w PROJECT dla poprawnego, kompletnego
zestawu z jawną ochroną i bez zablokowanego wpływu na dane pochodne.
Dowód: `tests/test_canonical_project_records.py`, test
`test_all_project_types_mixed_commit_operator_reopen_retry_and_isolation`.

| Typ | Status podzakresu | Rekord i powiązanie potwierdzone przez API/P20 |
| --- | --- | --- |
| FACT | Obsługiwany; zachowana regresja | FACT-gap008-door → EVENT-gap008-door |
| CHARACTER_STATE | Obsługiwany | Dwa CONTEXT: stany CHAR-ada i CHAR-bo |
| EVENT | Obsługiwany | EVENT-gap008-door, uczestnicy i źródłowa scena |
| KNOWLEDGE_EVENT | Obsługiwany | KNOWLEDGE-gap008-ada-marker → FACT, EVENT, CHAR |
| THREAD | Obsługiwany | THREAD-gap008-archive → PAYOFF-gap008-marker |
| SETUP | Obsługiwany | SETUP-gap008-marker → źródłowa i payoff scena |
| PAYOFF | Obsługiwany | PAYOFF-gap008-marker → SETUP-gap008-marker |
| RELATIONSHIP_CHANGE | Obsługiwany | REL-gap008-ada-bo-trust → CHAR-ada / CHAR-bo |

## Przepływ i bramki

Rzeczywisty `TestClient(app.main.app)` wywołuje `/agent/step`, P20 oraz endpointy
operatora review/decision/commit. Podmienione są wyłącznie zewnętrzne adaptery
WRITE i EXTRACTOR/VERIFIER. Repozytoria, analiza, guard, DPAPI i proces pozostają
rzeczywiste. Testowy sekret powstaje wyłącznie w izolowanym katalogu testu.

- Dziewięć rekordów / osiem typów: wspólna ekstrakcja, niezależna weryfikacja,
  jedna propozycja i lokalna transakcja. CREATE zapisuje wersję 1.
- UPDATE wszystkich chronionych rekordów zatrzymuje się na
  AWAITING_USER_APPROVAL. Próba commit bez zgody niczego nie zmienia.
  APPROVE oraz końcowy guard ALLOW zapisują wszystkie wersje 2; obie flagi
  ochrony pozostają true. Receipt zawiera authorization_ref i impact_id.
- Jawny REJECT operatora oraz próba zdjęcia ochrony zachowują cały poprzedni
  zestaw. Wadliwa referencja PAYOFF, błędny czas EVENT, brak flagi RELATIONSHIP
  oraz obcy project_id KNOWLEDGE blokują cały zestaw.
- Rzeczywisty trigger SQLite przerywa zapis audytu commitu: rollback usuwa
  wszystkie nowe rekordy, historię i receipt; zachowany jest wynik niepowodzenia.
- Reopen daje te same rekordy. Powtórzony commit zwraca identyczny receipt;
  retry API/P20 nie wywołuje ponownie ekstrakcji. Historia każdego rekordu: [1,2].
- Następny WRITE oraz EXTRACTOR otrzymują zatwierdzoną wersję 2 przez istniejący
  ContextBuilder. Obcy projekt z tymi samymi lokalnymi ID nie otrzymuje tej treści.
- Dotychczasowe testy FACT obejmują brak obejścia przez wcześniejszy zapis plikowy,
  STALE po zmianie bazy, rotację poświadczenia, niezależne flagi i replay.
  STALE nie jest automatycznie ponawiany ze starą zgodą.

Przykładowy zachowany dowód mieszanej aktualizacji:
proposal `proposal-ea56ad1c188687d3cf403115e3399c1290d50766194fdc26d556254a37ebfff5`,
hash `f4ca681a25d163ecf156521d2793b02fdb77be246ccd8c1d5a15aaccd377c65d`.
Artefakt lokalny: `%TEMP%/agentpro-project-records-20260915/mixed-project-proof.json`.

## Granica F-004 i pozostałe ograniczenia

Nie wykazano zależności od F-004 wynikającej wyłącznie z typu rekordu. Wykazano
zależność konkretnej operacji: aktualizacja EVENT-gap008-door przy krawędzi
CONTEXT-derived REQUIRES EVENT-gap008-door wymaga unieważnienia i odbudowy
pochodnego kontekstu. Brakuje gwarancji takiej odbudowy i spójności wymaganych
dodatkowych zapisów. CanonService `_canonical_impact` nadal zwraca
`DERIVED_REBUILD_UNSUPPORTED`, bez częściowej mutacji. Podstawa: zaakceptowany
kontrakt §8 (atomowość, invalidation/rebuild, granica cross-store).

Starsze rekordy z nieznaną ochroną pozostają nieaktualizowalne tym przepływem.
Referencje muszą rozwiązać się w lokalnym zestawie/docelowym stanie projektu
lub do źródła propozycji; nie dodano nowego rejestru postaci ani importu obcych
rekordów. Nie dodano SERIES, cross-store recovery ani automatycznej kontynuacji
niekompletnej operacji. Pełna obsługa F-002/F-006 nie jest deklarowana.

Rzeczywisty transport modelu: NOT VERIFIED. `app/llm_client.py:run_completion`
nadal jawnie odrzuca brak skonfigurowanego transportu. Adapter testowy dowodzi
integracji komponentów, nie jakości ani dostępności rzeczywistego dostawcy.

## Testy

- Syntax/import: compileall zmienionych modułów — PASS.
- GAP-007/GAP-008: 32 passed.
- Celowane: `python -m pytest tests/test_canonical_project_records.py tests/test_canonical_pipeline.py tests/test_p20_structured_memory.py tests/test_p20_memory_extraction_integrity.py tests/test_p20_context_builder.py -q` — 111 passed.
- Nowy zakres po dodaniu asercji następnego WRITE: 16 passed.
- Pełna regresja `python -m pytest tests -q`: **553 passed, 1 skipped** (256.37 s).

## Dane i kontrola zmian

7152 pliki użytkownika w books/runs/novel_runs/audit/projects/series oraz
agentpro_system.db: porównanie ścieżek i SHA256 wykazało 0 różnic.
Łączny fingerprint PRE = POST:
`F8D556DCD3B5F599BB3EFFEA559EA24CD58F699EFF3AB5AF509B23CEF51740BA`.
Manifesty: `%TEMP%/agentpro-project-records-20260915/data-before.txt` i `data-after.txt`.
Rzeczywisty `%LOCALAPPDATA%/AgentPRO/Security/operator.dpapi` nie istnieje.

Zastany staging był pusty. Zachowano trzy zmodyfikowane pliki użytkownika oraz
555 nieśledzonych pozycji. Dokładny baseline statusu zapisano poza repozytorium.
Zakres commitu: sześć modułów, dwa pliki testowe i ten raport. Hash commitu oraz
końcowy HEAD są podane w odpowiedzi końcowej; raport należy do tego commitu.
Pełny końcowy status: `%TEMP%/agentpro-project-records-20260915/status-final.txt`.
