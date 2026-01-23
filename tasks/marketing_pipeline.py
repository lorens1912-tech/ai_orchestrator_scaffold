
from __future__ import annotations

from llm_client import generate_text

PROMPT_MKT = """Przygotuj pakiet marketingowy:
- 5 nagłówków reklam (30–60 znaków)
- 3 warianty CTA
- 5 postów social (X/FB/IG)
- 2 wersje long-form (LinkedIn, blog)
- Brand voice (3 punkty)

Produkt/usługa: {product}
Grupa docelowa: {audience}
Rynek/język: {locale}
"""

PROMPT_EDIT = """Przeredaguj pod SEO PL (bez lania wody), zachowaj sens.
Zadbaj o:
- konkret
- brak powtórzeń
- nagłówki czytelne
- lekko sprzedażowy ton

TEKST:
{draft}
"""

def run(payload):
    product = payload.get("product", "Produkt")
    audience = payload.get("audience", "Ogólna")
    locale = payload.get("locale", "PL")

    draft_model = payload.get("draft_model", payload.get("model", "gpt-4.1-mini"))
    edit_model  = payload.get("edit_model", payload.get("model", "gpt-4.1-mini"))

    draft = generate_text(
        PROMPT_MKT.format(product=product, audience=audience, locale=locale),
        model=draft_model,
        max_output_tokens=int(payload.get("draft_max_output_tokens", 1200)),
    )

    edit = generate_text(
        PROMPT_EDIT.format(draft=draft),
        model=edit_model,
        max_output_tokens=int(payload.get("edit_max_output_tokens", 1200)),
    )

    return {"marketing": edit}
