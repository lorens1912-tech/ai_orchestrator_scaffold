# GAP-015 — Research / evidence / canonical promotion

Status testów i checkpointu: [raport](GAP015_REPORT.md).
Kontrakt: [Canonical Change §15](../CANONICAL_CHANGE_CONTRACT_AGENTPRO.md).

## Właściciele i granice

| Wymaganie | Istniejący właściciel / uzupełnienie | Dowód |
|---|---|---|
| ResearchRecord, ResearchSource, ResearchClaim, ConflictRecord, AuthorDecision | `research.py`; trwałość przez ProjectRepository, project.db/research.v1 | `test_gap015_research.py` |
| Import tekstu i wersje | `import_source`, append-only wersje; hash treści oblicza backend | import/reopen/idempotency |
| Niezależna ekstrakcja i weryfikacja | `run_research` → ContextBuilder → istniejący `llm_provider_openai.call_text` | SDK Responses.create jako jedyny zastępowany element |
| Canonical proposal i zgoda | istniejący CanonService, operator review/decision, DomainMutationGuard | CREATE bez zgody, protected update, REJECT, stale, rollback |
| Aktywny runtime | `tool_factcheck` → domenowy serwis, bez wewnętrznego HTTP | TestClient(app.main.app), P20, persisted context |
| Izolacja, recovery | ProjectRepository; lokalne transakcje i attempt fencing | projekt obcy, reopen, rollback, retry |

Nie powstał nowy engine, repository ani Context Builder. Brak zmiany schematu
SQL: wersjonowane dokumenty używają istniejącego project_metadata.
Brak migracji danych użytkownika. P0 nie uczestniczy w przepływie.

## Obsługiwany import

`POST /operator/projects/{project_id}/research/sources` przyjmuje tekst UTF-8
w polu JSON `content`. Treść jest zachowana w project.db, razem z wersją,
pochodzeniem i hashem. Pola brakujące (autor, data publikacji, reliability)
pozostają null. Ten sam operation_id z inną treścią jest błędem.

Typy WEB/PDF/BOOK/MAP/REPORT/DOCUMENT/USER_NOTE/OTHER klasyfikują źródło.
Nie oznaczają automatycznego pobrania URL ani parsowania pliku. `content=null`
zapisuje REFERENCE_ONLY; ekstrakcja zwraca błąd SOURCE_CONTENT_UNAVAILABLE.
Pole binarnego uploadu/formatu nie należy do kontraktu i jest odrzucane.
Nie dodano crawlera, OCR ani parsera PDF. Live provider smoke jest odrębną
bramką operacyjną, nigdy równoważną kontrolowanemu testowi SDK.

## Oficjalne endpointy operatorskie

Wszystkie poniższe ścieżki wymagają loopback oraz istniejącego Bearer operatora.
Uwierzytelnienie nie oznacza approval. Nie przekazuj tokenu do LLM ani payloadu
`/agent/step`. Instrukcja nie inicjalizuje rzeczywistego poświadczenia.

- `POST /research/questions`: operation_id, research_id, question, purpose,
  related_entity_refs (opcjonalne, istniejące ID właściwego projektu).
- `POST /research/sources`: operation_id, source_id, version, source_type, title,
  opcjonalne content i dane bibliograficzne.
- `GET /research/records/{research_id}`: claims, przypięte źródła, bieżące wersje,
  obie strony konfliktów i jawne decyzje.
- `POST /research/execute`: operation_id, research_id, action EXTRACT/VERIFY,
  source_refs [{source_id,version}], claim_ids, run_id, step_id, opcjonalny model.
  Model rozwiązuje istniejący model_policy. Nie ma zastępczego sukcesu.
- `POST /research/operations/{operation_id}/recover`: jawnie odgradza przerwaną
  próbę; następne execute używa pierwotnych danych i zapisanego kontekstu.
- `POST /research/proposals`: proposal_id, operation_id ukończonego VERIFY,
  target_fact_ids (claim_id → FACT-id), fiction_decision albo null.
- Następnie dotychczasowe `/proposals/{proposal_id}/review`, `/decision`, `/commit`.

Prefiks wszystkich ścieżek: `/operator/projects/{project_id}`.

## Wykonywalny przykład izolowanego przepływu

Przykład API z rzeczywistym request/response i syntetycznym operatorem:
`tests/test_gap015_research.py::test_api_import_extract_verify_approval_commit_reopen_retry`.
Uruchomienie w PowerShell nie wymaga serwera ani prawdziwego tokenu:

```powershell
Set-Location 'C:\AI\ai_orchestrator_scaffold'
python -m pytest tests/test_gap015_research.py::test_api_import_extract_verify_approval_commit_reopen_retry -q
```

Request tworzący pytanie:

```json
{"operation_id":"question","research_id":"RESEARCH-neutral-question","question":"When does the synthetic station open?","purpose":"Neutral test"}
```

Response zawiera zapisany ResearchRecord: powyższe dane, `project_id`,
`requested_by` z uwierzytelnionego operatora, `version=1`, `status=OPEN`,
serwerowe `created_at`, `completed_at=null` i `related_entity_refs=[]`.
W teście odpowiedź EXTRACT dostarcza claim_id używane bezpośrednio w VERIFY
i target_fact_ids. Propozycja zwraca `contract_version=2.0`,
`source_kind=RESEARCH`, `status=AWAITING_USER_APPROVAL` oraz proposal_hash.
Review dostarcza challenge_id; decision zwraca canonical_commit=false;
dopiero commit zwraca COMMITTED, canonical_commit=true i resulting_versions.

## P20 FACTCHECK i kontekst

`/agent/step`, mode FACTCHECK, payload.research:
`{operation_id,research_id,claim_ids,action:"VERIFY"}`. Te same pola źródeł
umożliwiają osobny EXTRACT. P20 wywołuje serwis bez requestów do operator API.
Modele nie mają dostępu do decyzji ani poświadczeń operatora.

Odpowiedź `research` i artefakt FACTCHECK pokazują execution_status, claims,
ISSUES, canonical_commit=false. Brak researchu daje NOT_PERFORMED z problemem;
błąd modelu daje FAILED. Żaden z nich nie jest raportowany jako udana kontrola.
UNCERTAIN/DISPUTED nie są błędem wykonania; są wynikiem weryfikacji i wymagają
dalszych badań/decyzji. Jawna zatwierdzona fikcja nie zmienia źródłowego statusu.

Właściwy projekt i jawny research_id ograniczają kontekst. Rola CANON dostaje
treść źródeł, Writer statusy/provenance/claims/konflikty/decyzje bez pełnych
tekstów źródłowych. Research jest oznaczony RESEARCH_ONLY_NOT_CANON.
Nowy kontekst uwzględnia zmiany dowodów, technical retry odtwarza zapisany pakiet.

Audit lineage: operation.request → inputs → invocation (model, call_id,
ContextPackage/hash, input_hash, output) → wynik/hash → proposal/evidence_hash
→ impact → approval/authorization_ref → final guard → receipt i AuthorDecision.
Zestaw i audit commitowane lokalnie razem; pełny rollback na błędzie elementu.
