# F-002 / F-006 — raport implementacji i dowodów

Data: 2026-09-15. Baza: `c67ba9870157c681e85fac5e2d6bc32a7322d840`.
Kontrakt: `CANONICAL_CHANGE_CONTRACT_AGENTPRO.md` v1.1, bez zmian.

## Status

- F-002 = PARTIAL / NOT CLOSED.
- F-006 = PARTIAL / NOT CLOSED.
- F-004 = NOT STARTED.
- GAP-014 = NOT STARTED.

## Zakres potwierdzony

Rzeczywisty `TestClient(app.main.app)` uruchamia `/agent/step` i P20.
Kontrolowane są wyłącznie adaptery zewnętrznego modelu WRITE oraz
EXTRACTOR/VERIFIER. Procesy, repozytoria, uwierzytelnienie, analiza i guard
nie są zastępowane atrapami.

Zaakceptowany tekst ma niezmienną kopię, wersję, hash i referencję w metadanych
projektu. Zachowano powiązanie z artefaktem kroku WRITE i jego ContextPackage.
Osobne wywołania EXTRACTOR i VERIFIER korzystają z istniejącego ContextBuildera;
wejścia, wyniki i invocation IDs są zapisane w project.db. Istniejący verifier
sprawdza obie osie oraz provenance. REVISE wykorzystuje istniejący limit prób;
REJECT i eskalacja nie tworzą propozycji.

Kompletny zestaw obsługiwanych FACT przechodzi przez osobną, zapisaną propozycję,
hash, analizę grafu i DomainMutationGuard. Wstępna zgoda nie zastępuje kontroli
końcowej. Snapshot wersji/payloadów i wszystkich krawędzi jest ponownie
sprawdzany pod `BEGIN IMMEDIATE`. Fizyczny zapis w repository ponawia ten sam
guard. Bieżące rekordy, historia wersji, receipt, lokalne metadane invalidation
i audyt commitu należą do jednej transakcji project.db.

### Kontrakt → implementacja → dowód

| Sekcja | Implementacja | Dowód w tests/test_canonical_pipeline.py |
| --- | --- | --- |
| §1–3 | CanonService.process_accepted_artifact; executor.invoke_memory_model; istniejący verifier | test_active_api_commit_reopen_retry_context_and_isolation; test_verifier_reject_and_bounded_revision |
| §4–5 | istniejące analyze_impact; DomainMutationGuard.evaluate_canonical | test_actual_impact_path_and_unavailable_derived_rebuild; test_guard_denial_rolls_back_whole_set |
| §6–7 | istniejące review/decision; uwierzytelniony POST /operator/projects/{project_id}/proposals/{proposal_id}/commit | test_protected_operator_commit_and_legacy_file_cannot_bypass; test_independent_protection_flags |
| §7 | snapshot i approval związane z propozycją i poświadczeniem | test_stale_basis_and_operator_rotation; test_target_changed_by_another_approved_proposal_is_stale; test_upstream_evidence_tamper_and_seeded_review_cannot_commit |
| §8–9, §12 | ProjectRepository.apply_canonical_record_set; receipt i historia w tej samej DB | test_real_analysis_error_and_audit_rollback; test_rejection_and_concurrent_commit_replay; test_active_api_commit_reopen_retry_context_and_isolation |
| §8, §11 | jawne odmowy bez zastępowania SERIES przez PROJECT | test_unsupported_series_fails_closed_without_project_fallback; test_unsupported_protection_schema_rejects_whole_candidate_set |

### Konkretny łańcuch chronionego zapisu z dowodu API

- Projekt: `PROJ-pipeline-proof`; książka: `BOOK-pipeline-proof`.
- Run/step: `run-pipeline` / `step-pipeline`.
- Artefakt WRITE: `runs/run-pipeline/steps/001_WRITE.json`.
- Hash zaakceptowanego tekstu: `d21cae50408e7479a5147bf1ad4706cdddb26d87b067f291886369dec487d6c6`.
- Proposal: `proposal-77730ef21ba93cdf509e2653702e1713c1559b1995093ff7c135635279baed9a`.
- Proposal hash: `0f4ec510f07287a8bd0a6ed3e133a37368b4a921be18375319ac7b9829238f9a`.
- Candidate: `candidate-77730ef21ba93cdf509e2653702e1713c1559b1995093ff7c135635279baed9a-1`.
- Verification hash: `333d107280f7fb1dbfe0843f0ca569b90341447e2bd068bbe4a3989324128697`.
- Impact: `impact-0f4ec510f07287a8bd0a6ed3e133a37368b4a921be18375319ac7b9829238f9a`.
- Zależność: `SCENE-dependent REQUIRES FACT-pipeline-proof`, krawędź
  `edge-protected-impact`, wersja 1; analiza zwraca DIRECT, głębokość 1.
- Wstępny guard: REQUIRE_USER_APPROVAL. Bez decyzji rekord pozostaje w wersji 1.
- Authorization: `authorization-1ce4052ea66a45139f80181761d78ca1`.
- Decyzja API: APPROVE, canonical_commit=false. Osobny uwierzytelniony commit
  odczytuje decyzję z backendu i sprawdza operator_id, credential_id/version,
  projekt/scope, hash propozycji i hash impact.
- Final guard: ALLOW. Receipt: COMMITTED, canonical_commit=true.
- `FACT-pipeline-proof`: wersja 1 → 2; frozen=true i author_locked=true zachowane.
- Operation ID: `9a36d6ae7c7dfc158ef567a492dbe39510226dc93db1df2847f8f569ea847cc4`.

Pełny zapis tego przebiegu znajduje się w izolowanym katalogu testowym:
`.test_storage/sessions/43131418fbce4ae19c2876319fc20bfe/isolated/tests_test_canonical_pipeline.py_test_protected_operator_commit_and_legacy_file__9ec3467dfda1/protected-api-proof.json`.
ID/hash propozycji i decyzji zawierające czas albo losowe ID zmieniają się między
niezależnymi uruchomieniami testu; w obrębie retry pozostają identyczne.

## Wcześniejsza ścieżka plikowa

`commit_chapter_to_canon` i rebuild aktualizują rejestr zaakceptowanych rozdziałów.
Nie importują encji z tekstu ani pól `facts`/`proposed_mutations` rozdziału do
structured memory. Test wstrzykuje do rzeczywistego pliku wersję 999 i zdjęcie
obu blokad, uruchamia obie funkcje, następnie porównuje FACT w DB oraz pola
facts/timeline/decisions pliku. Chroniony rekord pozostaje w wersji 1 do
uwierzytelnionego commitu. To dowód granicy aktywnej ścieżki; nie ochrona przed
dowolną ręczną edycją DB lub plików przez administratora Windows.

## Trwałość, retry, izolacja

- Ponowne otwarcie ProjectRepository odtwarza zapis i historię.
- Technical retry tego samego zdarzenia nie uruchamia ponownie ekstrakcji
  i nie tworzy kolejnej propozycji ani wersji; commit retry zwraca receipt.
- Równoległe retry commitów zwracają identyczny wynik.
- Nowy krok buduje kontekst zawierający zatwierdzony stan; obcy projekt go nie
  otrzymuje. Historyczne ContextPackage pozostają dowodem pierwotnego wykonania.
- Zmiana celu przez drugi zatwierdzony commit albo zmiana grafu unieważnia
  podstawę starej zgody. Błędny hash/scope, obcy projekt i stare poświadczenie
  nie uprawniają do zapisu.
- SQLite trigger wymuszający błąd audytu wycofuje cały zestaw rekordów i historię.

## Ograniczenia — dlaczego findings pozostają NOT CLOSED

1. Promocja obejmuje FACT w PROJECT. Inne istniejące typy nie mają wspólnego
   kontraktu obu flag ochrony; mieszany zestaw jest blokowany w całości:
   CANONICAL_RECORD_PROTECTION_SCHEMA_UNSUPPORTED. Nie przyjęto nowych
   domyślnych flag ani nie zmieniono kontraktu domeny.
2. `app.llm_client.run_completion` pozostaje istniejącym shimem bez transportu.
   Test dopuszczonego adaptera dowodzi integracji procesu, nie wywołania live LLM.
   Bez adaptera przepływ zwraca FAILED i nie zapisuje kanonu. Live LLM: NOT VERIFIED.
3. SERIES graph/canonical commit nie są zaimplementowane. Żądanie SERIES jest
   jawnie blokowane; nie jest to pełna obsługa F-006.
4. Analiza obejmuje zapisany graf PROJECT. Budżet i coverage są jawne. Zmiany
   wymagające odbudowy pochodnych encji są blokowane DERIVED_REBUILD_UNSUPPORTED.
   Zapisane metadane invalidation i kolejny ContextBuilder nie dowodzą ogólnej
   odbudowy całej pamięci pochodnej ani niezapisanych zależności temporalnych.
5. Nie zaimplementowano automatycznego wznowienia przerwanego upstream ani
   automatycznego przygotowania nowej wersji po STALE/FAILED. Istniejący wynik
   jest odtwarzany, a nierozstrzygnięte przygotowanie wymaga diagnostyki.
6. Referencje FACT muszą być rozwiązywalne w pełnym zestawie/bieżącej structured
   memory albo w tożsamości źródła i projektu. Brak takiej podstawy blokuje zapis.
   Nie deklaruje to pełnej walidacji semantycznej wszystkich typów domenowych.

F-004 pozostaje poza zakresem: brak transakcji obejmującej project.db + series.db
+ pliki, propagacji między magazynami, outbox i recovery między magazynami.
Lokalny receipt/audyt i atomowość pojedynczej DB wynikają z §8–9 tego kontraktu.

## Walidacja

Główny gate celowany (PowerShell):

```powershell
python -m pytest tests/test_canonical_pipeline.py tests/test_local_operator.py tests/test_p20_runtime_context_integration.py tests/test_p20_runtime_identity_integration.py tests/test_082_resume_missing_run_folder_creates_new.py -q -x
```

Pełny gate:

```powershell
python -m pytest tests -q
```

- Syntax/import validation: compileall — PASS.
- Pipeline + operator + F-001/F-003 + resume: 40 passed.
- Pakiet GAP-001..013 i granica executora: 326 passed w trzech przebiegach
  (321 fundamenty pamięci/repozytoriów/grafu/guard/context/executor,
  3 taksonomia jakości, 2 granica active/legacy).
- Współbieżność po poprawce: 10 kolejnych przebiegów istniejącego testu — PASS.
- Pełna regresja: **537 passed, 1 skipped**, 216.22 s. Pominięty test dotyczy
  legacy PATCH /canon/{book_id}, niewystawionego przez aktywne app.main.
- Fingerprint: 7152 pliki przed i po, 0 różnic. SHA-256 przed = po:
  `F8D556DCD3B5F599BB3EFFEA559EA24CD58F699EFF3AB5AF509B23CEF51740BA`.
  Metoda: sortowane pełne ścieżki `path|SHA256`, połączone LF, UTF-8,
  końcowy SHA-256; korzenie books/runs/novel_runs/audit/projects/series/system DB.
- Pełna lista zastanych zmian: 558 pozycji, 0 różnic po odjęciu zakresu zadania;
  3 pliki zmodyfikowane i 555 pozycji nieśledzonych zachowane.
- Rzeczywiste poświadczenie operatora nie zostało zainicjalizowane.
- Diff check: PASS. Commit i finalny git status są podane w raporcie końcowym
  sesji; status zostaje też zapisany w tymczasowym katalogu dowodowym.

Przebieg ograniczonego procesu zakończył się błędami Windows SetAccessControl
(UnauthorizedAccessException) przed testami wymagającymi poświadczenia.
Ponowny gate z rzeczywistymi uprawnieniami do DACL przeszedł bez zmiany
implementacji uwierzytelnienia. Nie zastąpiono DPAPI atrapą.

Pierwsza pełna regresja: 534 passed, 1 skipped, 2 failed. Ujawnione błędy:

- Równoległe pierwsze otwarcie system DB: SQLITE_BUSY w PRAGMA journal_mode=WAL.
  Odtworzono w szóstym przebiegu testu współbieżności. Dodano ograniczone
  czasowo ponowienie wyłącznie inicjalizacji WAL, z zamknięciem połączenia także
  przy jej błędzie; transakcje domenowe nie są automatycznie powtarzane.
- WinError 5 przy zastąpieniu run_state.json. json_write ponawia tylko replace
  dla Windows 5/32, najwyżej pięć prób; trwała odmowa nadal przerywa zapis.
  Test wymusza chwilową i trwałą odmowę oraz sprawdza treść docelowego pliku.

Test współbieżności zachowuje teraz traceback błędu workera zamiast ograniczać
diagnostykę do repr listy wyjątków. Warunki sukcesu nie zostały osłabione.
