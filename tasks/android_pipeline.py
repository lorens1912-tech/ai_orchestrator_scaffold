
from __future__ import annotations

from llm_client import generate_text

PROMPT_ANDROID = """Wygeneruj szkic aplikacji Android (Kotlin + Jetpack Compose):
- architektura (MVVM), moduły, ekrany, repozytoria
- przykładowe testy jednostkowe
- TODO lista do wdrożenia produkcyjnego

Funkcja apki:
{idea}
"""

PROMPT_REVIEW = """Zrób code-review i listę poprawek. Wypisz:
- ryzyka/bugi
- brakujące elementy
- poprawki architektury
- checklistę do wdrożenia

KOD:
{draft}
"""

def run(payload):
    idea = payload.get("idea", "Notatnik AI")
    model = payload.get("model", "gpt-4.1-mini")
    review_model = payload.get("review_model", model)

    draft = generate_text(
        PROMPT_ANDROID.format(idea=idea),
        model=model,
        max_output_tokens=int(payload.get("max_output_tokens", 1800)),
    )
    review = generate_text(
        PROMPT_REVIEW.format(draft=draft),
        model=review_model,
        max_output_tokens=int(payload.get("review_max_output_tokens", 1200)),
    )
    return {"android_scaffold": draft, "review": review}
