
from __future__ import annotations

from llm_client import generate_text

PROMPT_AGENT = """Zaprojektuj agenta AI:
- cel, polityka działania, narzędzia (search, kalkulator, dokumenty, pamięć)
- automaty stanowe, retry/backoff, logowanie
- metryki sukcesu i testy E2E

Usecase:
{usecase}
"""

PROMPT_AUDIT = """Sprawdź spójność specyfikacji, wskaż luki i ryzyka.
Daj poprawki w punktach.

SPEC:
{spec}
"""

def run(payload):
    usecase = payload.get("usecase", "Pisanie rozdziałów + linter")
    model = payload.get("model", "gpt-4.1-mini")
    audit_model = payload.get("audit_model", model)

    spec = generate_text(
        PROMPT_AGENT.format(usecase=usecase),
        model=model,
        max_output_tokens=int(payload.get("max_output_tokens", 1600)),
    )
    audit = generate_text(
        PROMPT_AUDIT.format(spec=spec),
        model=audit_model,
        max_output_tokens=int(payload.get("audit_max_output_tokens", 1200)),
    )
    return {"agent_spec": spec, "audit": audit}
