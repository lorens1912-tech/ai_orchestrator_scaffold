
from __future__ import annotations

from llm_client import generate_text

def guard_health(text: str) -> str:
    # Placeholder: zostawiamy tekst bez zmian (żeby nie było zależności od policy.health_guard)
    return text

PROMPT_DIET = """Ułóż plan żywieniowy na {days} dni.

Założenia użytkownika:
- kalorie: {kcal} kcal/d
- białko: {protein_g} g/d
- fosfor: ≤ {phos_mg} mg/d
- preferencje: {prefs}

Wymagania formatu:
- 3 główne posiłki + 1 przekąska (lub wg preferencji)
- podawaj makro i mikro DLA KAŻDEGO SKŁADNIKA i posiłku:
  białko/tłuszcz/węglowodany, kcal, fosfor, potas, sód
- zero lania wody, praktyczne porcje, gotowe do użycia

Dodatkowe dane:
{extra}
"""

def run(payload):
    days = int(payload.get("days", 7))
    kcal = int(payload.get("kcal", 2500))
    protein_g = int(payload.get("protein_g", 145))
    phos_mg = int(payload.get("phos_mg", 800))
    prefs = payload.get("prefs", "3 posiłki + 1 przekąska, brak ryb, 2 jajka na śniadanie")
    extra = payload.get("extra", "(brak)")

    model = payload.get("model", "gpt-4.1-mini")
    max_tokens = int(payload.get("max_output_tokens", 1800))

    text = generate_text(
        PROMPT_DIET.format(
            days=days, kcal=kcal, protein_g=protein_g, phos_mg=phos_mg, prefs=prefs, extra=extra
        ),
        model=model,
        max_output_tokens=max_tokens,
    )
    return {"diet": guard_health(text)}
