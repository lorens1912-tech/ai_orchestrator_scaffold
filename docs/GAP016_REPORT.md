# GAP-016 — raport wykonania

GAP-016 STATUS = CLOSED (2026-09-16).
AUDITED BASE HEAD = `e18ee3ffe51bb354afea356f8090f3ae89f9918f`.

## Zakres i naprawione przyczyny

Aktywny transport zwracał tylko końcowy model i uproszczone informacje o
temperature/retry; brakowało trwałego zapisu poszczególnych prób, użytej
polityki i usage/response identity. P20 ignorował także odmowę STRICT
zwracaną przez istniejący router. Dodano egzekwowanie tej odmowy (HTTP 422),
bez zmiany reguł routingu, modeli, presetów ani progu jakości.

Pełna macierz call sites, kontrakt v1 i granice recovery:
[GAP016_MODEL_PROVENANCE.md](GAP016_MODEL_PROVENANCE.md).

## Implementacja i kryteria

- `app/model_policy.py::resolve_model`: surowe wejścia, snapshot decyzji,
  policy version/hash. Dotychczasowe ModelDecision.to_dict zachowane.
- `app/llm_provider_openai.py::call_text`: jawny callback START/END na granicy
  Responses.create lub Completions.create; dokładne wysłane parametry,
  alias i reported model osobno, mierzone usage i identyfikatory, ograniczenie
  obserwowalności retry SDK. Brak parametrów nie staje się defaultem.
- `app/p20_core/model_provenance.py::ModelInvocationAudit` oraz istniejący
  `ProjectRepository`: lokalny, wersjonowany envelope; osobne invocation i
  attempt IDs, fingerprint konfiguracji oddzielony od fingerprintu wejścia,
  przypięty ContextPackage, model i polityka, izolacja, trwały wynik i recovery.
- `executor.invoke_memory_model` / `tools.memory_integrity_provider`:
  ekstraktor i niezależny verifier mają osobne ślady PROJECT/SERIES.
- `research._model_call/run_research/recover_operation`: ten sam adapter,
  osobne role, niepoprawna odpowiedź zachowana w historii przy retry;
  ukończona operacja nie powtarza SDK. Walidacja + wynik domenowy oraz
  recovery + attempt fencing są odpowiednio lokalnymi transakcjami.
- `executor.execute_p20`, `runtime._context_traces`, `canon_service.write_audit`:
  projekcja do istniejących artefaktów, audytu runu i odpowiedzi API run_state,
  bez wpływu na kontekst.
- `operator_api.read_research`: projekcja do istniejącego uwierzytelnionego
  odczytu researchu; surowe odpowiedzi nie są kopiowane do tej projekcji.
- `app/main.py`: istniejąca odmowa polityki mapowana na HTTP 422.
- `tests/test_gap016_model_provenance.py`: dowody SDK/API/SQLite/reopen/retry,
  izolacji, awarii, redakcji sekretów i kompatybilności.
- `tests/test_canonical_pipeline.py`: wyłącznie sygnatura starych adapterów
  testowych przyjmuje addytywny argument; nie osłabiono żadnej asercji.

REQUESTED / RESOLVED / SENT / PROVIDER-REPORTED IDENTITY = rozdzielone.
PARAMETER PROVENANCE / SDK KWARGS PARITY = sprawdzane na granicy SDK.
FALLBACK / DROPPED PARAMS / ATTEMPT TRACE = tylko istniejące temperature DROP;
niepowiązany błąd ani awaria callbacka nie uruchamia fallbacku.
UNKNOWN / FAILED / REFUSED OUTCOMES = odrębne od walidacji i jakości.
CONTEXT / INPUT / OUTPUT / ARTIFACT BINDING = ID/hash zapisanych pakietów,
wejścia i wyjścia, run/step/call_id, scope oraz referencje źródłowych artefaktów.
OLD EVIDENCE / HASH COMPATIBILITY = PASS: porównano niezmienione pliki kontraktów
oraz AST funkcji canonical_proposal_hash, _evidence_hash, verified_evidence
i validate_promotion_evidence z checkpointem; dowód frozen-contract-comparison.json.
Telemetryczny envelope pozostaje poza oryginalną
serializacją dowodów i poza ContextPackage; historyczne braki nie są uzupełniane.

## Wyniki bramek

TARGETED TESTS = 37 passed in 76.26s, exit code 0; uzupełniająca para
success/error sekretów: 2 passed in 10.97s, exit code 0. Łącznie 38 unikalnych
przypadków w test_gap016_model_provenance.py (przypadek błędu wykonano ponownie).
INTEGRATION TESTS = 223 passed in 482.76s, exit code 0; po końcowych korektach
projekcji API/integralności wyniku: 4 passed in 29.63s, exit code 0.
FULL REGRESSION = 695 passed, 1 skipped in 743.45s (12:23), exit code 0.
Jeden pełny przebieg po finalnej zmianie kodu/testów. Skip:
`tests/test_110_canon_api_roundtrip.py:47` — legacy PATCH /canon/{book_id}
nie jest udostępniane przez aktywne app.main. Bez nowych skipów.

PERSISTENCE / REOPEN / RETRY / RECOVERY = PASS.
PROJECT ISOLATION / SECRET REDACTION = PASS (sukces i błąd transportu).
RESEARCH / EXTRACTION / SERIES / STYLE REGRESSION = PASS.
ACTIVE INVOCATION COVERAGE = wszystkie znalezione aktywne call sites P20;
macierz i symbole w kontrakcie. Lokalne narzędzia bez LLM nie mają fikcyjnych prób.

Wynik historyczny poprzedniego etapu 657/1 nie jest dowodem GAP-016.
Komendy, exit codes i logi są w `.test_storage/gap016_evidence`:
`targeted-command.txt`, `targeted-final.txt`, `targeted-exit-code.txt`,
`integration-command.txt`, `integration.txt`, `integration-exit-code.txt`,
`full-command.txt`, `full-regression.txt`, `full-exit-code.txt`.
Uzupełnienia: `integration-followup-command.txt` / `integration-followup.txt` /
`integration-followup-exit-code.txt`; `secret-command.txt` / `secret.txt` /
`secret-exit-code.txt`.
Rzeczywisty syntetyczny trace SDK → P20 → audit: `api-proof.json`.

## Ograniczenia i zachowane zakresy

LIVE ADAPTER SMOKE = NOT RUN. Żadnych płatnych wywołań ani rzeczywistych
poświadczeń. Sieć zastępują Responses.create/Completions.create; repozytoria,
router, ContextBuilder, guard, operator i P20 są wykonywane rzeczywiście.
Dotychczasowe testy jednostkowe canonical mają własne stare adaptery testowe;
nie są przedstawiane jako nowy dowód transportu.

P0 RUNTIME PARTICIPATION = NO.
GAP-017 = NOT STARTED.
GAP-015 pozostaje tekstowym PROJECT research (bez URL download, PDF/OCR,
bez samodzielnej promocji researchu do SERIES). Nie zmieniono authority,
operator approval, Style Library/DNA, DERIVED ani LEGACY PROTECTION.
Ustawienia modelu Codexa nie były zmieniane; dokładny wariant i poziom sesji
nie są potwierdzane przez narzędzie wykonawcze. Nie dotyczą modeli AgentPRO.

## Kontrola danych i Git

Baseline: 2546 wpisów `git status --porcelain=v1 -uall`: 3 zastane tracked
modyfikacje i 2543 untracked pliki. Zapis: `baseline-status.txt`.
Testy: neutralne projekty i `isolated_agentpro_storage`, guard zapisów poza
syntetycznym storage; operator DPAPI wyłącznie w nowym TEMP poza repo.
Nie czytano zawartości ani nie fingerprintowano rzeczywistych książek.
REAL USER DATA / CANON MUTATED DURING TESTS = NO.

DIFF / STAGING REVIEW = PASS: dokładnie 16 jawnych plików GAP-016;
git diff --check i git diff --cached --check bez błędów; brak zastanych zmian w stagingu.
PRE-EXISTING WORKTREE CHANGES PRESERVED = YES (2546/2546 wpisów baseline zachowane; końcowe porównanie w evidence).
COMMIT / CHECKPOINT = po przejściu bramek, hash w raporcie sesji.

## Dokładne komendy wykonanych bramek

### Targeted

```powershell
python -m pytest tests/test_gap016_model_provenance.py -q --disable-warnings --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap016-targeted-final-810d0a8ac6114ae0a11e233d4f5b262e
```

### Secret success/error

```powershell
python -m pytest tests/test_gap016_model_provenance.py::test_runtime_secrets_not_in_audit_or_artifacts -q --disable-warnings --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap016-secret-35fdf06aa50341a2b12742cbcf75b674
```

### Integration

```powershell
python -m pytest tests/test_gap015_research.py tests/test_canonical_pipeline.py tests/test_canonical_project_records.py tests/test_canonical_series_pipeline.py tests/test_memory_transport_integration.py tests/test_local_operator.py tests/test_p20_domain_mutation_guard.py tests/test_p20_context_builder.py tests/test_p20_runtime_context_integration.py tests/test_p20_runtime_identity_integration.py tests/test_p20_core_storage_isolation.py tests/test_p20_cross_store_recovery.py tests/test_gap014_adaptive_style.py tests/test_style_library_24.py tests/test_p20_active_legacy_boundary.py tests/test_010_model_switching_no_lie.py tests/test_A10_write_model_force_telemetry.py tests/test_p20_orchestrator_tools_storage_isolation.py -q --disable-warnings --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap016-integration-97b3d561b7c34fedb02ba8a454a9c3c2
```

### Integration follow-up

```powershell
python -m pytest tests/test_gap015_research.py::test_api_import_extract_verify_approval_commit_reopen_retry tests/test_gap015_research.py::test_active_p20_factcheck_context_audit_retry_and_fiction tests/test_gap015_research.py::test_explicit_recovery_fences_in_flight_attempt tests/test_memory_transport_integration.py::test_full_mixed_project_e2e_uses_only_sdk_boundary_and_survives_reopen -q --disable-warnings --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap016-integration-final-16f5e426e6e343a6888e7ddb1a225684
```

### Full regression

```powershell
python -m pytest -q --disable-warnings -rs --basetemp=C:\Users\Admin\AppData\Local\Temp\agentpro-gap016-full-6e88416bcfa04ad9853532cd9c5cef11
```


## Files changed / zakres commita

- `ROADMAPA_AGENTPRO.md`
- `app/llm_provider_openai.py`
- `app/main.py`
- `app/model_policy.py`
- `app/operator_api.py`
- `app/p20_core/canon_service.py`
- `app/p20_core/executor.py`
- `app/p20_core/model_provenance.py`
- `app/p20_core/project_repository.py`
- `app/p20_core/research.py`
- `app/p20_core/runtime.py`
- `app/tools.py`
- `docs/GAP016_MODEL_PROVENANCE.md`
- `docs/GAP016_REPORT.md`
- `tests/test_canonical_pipeline.py`
- `tests/test_gap016_model_provenance.py`

## Rzeczywisty neutralny trace z pełnego testu

```json
{
  "invocation_id": "INV-86b1e59ad44e4059b01c006258f2f248",
  "operation_id": "context:PROJ-pipeline-proof:run-provenance-api:step-provenance:canonical:bf3c9547ff40dff8dc1eb47c1db02dad11ad21db79da5cf14a64bf3ae1ccd31c:extract:1",
  "project_id": "PROJ-pipeline-proof",
  "book_id": "BOOK-pipeline-proof",
  "role": "EXTRACTOR",
  "context_package_id": "CONTEXT-fa9b48ae7624e9dcd9239538860dac64",
  "context_hash": "6f610112ae6011adaed18812bddae7cbb13c68ea4172fbf1baa67240d0b58e18",
  "artifact_refs": [
    "project_metadata:canonical_pipeline.v1:bf3c9547ff40dff8dc1eb47c1db02dad11ad21db79da5cf14a64bf3ae1ccd31c#source",
    "runs/run-provenance-api/steps/001_WRITE.json"
  ],
  "requested_model": "gpt-memory-requested",
  "resolved": {
    "allowlist_ok": false,
    "effective_model": "gpt-memory-effective",
    "note": "BLOCKED(gpt-memory-requested) => fallback(gpt-memory-effective)",
    "requested_model": "gpt-memory-requested",
    "source": "blocked"
  },
  "sent": {
    "model": "gpt-memory-effective"
  },
  "provider_reported": {
    "model": "gpt-memory-provider-returned",
    "model_version": null,
    "request_id": null,
    "response_id": null,
    "system_fingerprint": null,
    "usage": null
  },
  "validation": "VALID"
}
```

Wyłącznie zewnętrzne Responses.create jest kontrolowane w tym dowodzie.
Test porównuje kwargs SDK z SENT i odpowiedź API run_state z trwałym audit.json.
Verifier ma osobny invocation_id i ContextPackage. Całość: api-proof.json.
