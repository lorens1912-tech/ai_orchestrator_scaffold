# AI Orchestrator – multi‑agent router (GPT‑5, GPT‑4, o3, Grok 4)

## Co to jest
Szkielet produkcyjny do uruchamiania zadań przez graf zadań (plan → wykonanie → weryfikacja → poprawka).
Działa jako **API (FastAPI)** oraz **CLI**. Router wybiera model zgodnie z zadaniem:
- **GPT‑5** – generowanie i optymalizacja (proza, kod, biznes)
- **GPT‑4** – redakcja, poprawki, SEO
- **o3** – inspektor logiki i kanonu (lint fabularny, consistency)
- **Grok 4** – skan trendów i szybkie inspiracje (opcjonalnie)

> Uwaga: klienci modeli to *stuby* – podmień na własne wywołania API i klucze (ENV).

## Instalacja (lokalnie)
```bash
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

## Uruchomienie zadań przez CLI
```bash
python run_task.py --task book_chapter --payload examples/book_payload.json
```

## Konfiguracja
- `config.yml` – mapowanie modeli i progi bezpieczeństwa
- `policy/` – reguły guardów (zdrowie itp.)
- `tasks/` – pipeline’y
- `agents/` – router i klienci modeli
- `tools/` – web/search, kalkulator, pamięć

## Przepływ – przykład (pisanie rozdziału)
1. **o3**: generuje/uzgadnia *outline JSON* i kanon, raportuje luki.
2. **GPT‑5**: pisze scenę z beatów (z kanonem jako constraints).
3. **o3**: linter fabularny (ciągłość czasu/rekwizytów/POV).
4. **GPT‑4**: redakcja dialogów i rytmu.
5. (opcjonalnie) **Grok 4**: trend-check lub inspiracje wizualne.

## Bezpieczeństwo
- Guardy blokują ryzykowne treści medyczne: agent zwraca **informację edukacyjną**, nie diagnozę.
- W logach brak danych wrażliwych – maskowanie PII.

## Integracje (opcjonalne)
- Google Docs/Sheets, Notion, Git, Android build (CI), newsletter (API) – dodaj w `tools/`.
