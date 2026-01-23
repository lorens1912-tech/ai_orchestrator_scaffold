
from __future__ import annotations

from llm_client import generate_text

PROMPT_BIZ = """Stwórz zwięzły biznesplan (1–2 strony):
- Problem, Rozwiązanie, Rynek, Model przychodu, Go-to-market
- Koszty startowe (PLN), prognoza P&L na 12 mies., punkty ryzyka
- 3 scenariusze (pesymistyczny/bazowy/optymistyczny) z liczbami

Kontekst/branża:
{context}
"""

def run(payload):
    context = payload.get("context", "Brak dodatkowego kontekstu.")
    model = payload.get("model", "gpt-4.1-mini")
    max_tokens = int(payload.get("max_output_tokens", 1400))

    plan = generate_text(
        PROMPT_BIZ.format(context=context),
        model=model,
        max_output_tokens=max_tokens,
    )
    return {"plan": plan}
