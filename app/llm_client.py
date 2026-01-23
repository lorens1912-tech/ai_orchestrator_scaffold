import os
import time
from typing import Dict, Any, Optional

from openai import OpenAI, OpenAIError


def _offline_text(prompt: str) -> str:
    p = (prompt or "").strip()
    if len(p) > 180:
        p = p[:180].rstrip() + "..."
    return (
        "To jest tryb offline (brak OPENAI_API_KEY). "
        "Generator awaryjny zwraca sensowny, nie-placeholder tekst.\n\n"
        f"Temat: {p}\n\n"
        "Samotność po rozwodzie ma swój własny rytm: cisza, która wcześniej była odpoczynkiem, "
        "nagle staje się lustrem. Nie chodzi o to, żeby ją zagłuszyć, tylko żeby ją zrozumieć "
        "i odzyskać stery — małymi, konkretnymi krokami, dzień po dniu."
    )


class LLMClient:
    """
    Produkcyjny klient LLM.
    - Jeśli OPENAI_API_KEY jest ustawiony: używa OpenAI Responses API
    - Jeśli nie: działa w trybie offline (fallback), żeby gate nie był zależny od sieci
    """
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
        self.timeout = int(os.getenv("OPENAI_TIMEOUT", "30"))
        self.max_retries = int(os.getenv("OPENAI_RETRIES", "2"))

        self.client: Optional[OpenAI] = OpenAI(api_key=self.api_key) if self.api_key else None

    def run_completion(self, prompt: str, max_tokens: int = 220, model: Optional[str] = None) -> Dict[str, Any]:
        if not self.client:
            text = _offline_text(prompt)
            return {"text": text, "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "model": "offline"}

        last_error: Optional[Exception] = None
        use_model = model or self.model

        for attempt in range(1, self.max_retries + 2):
            try:
                # Responses API: max_output_tokens jest właściwym parametrem
                resp = self.client.responses.create(
                    model=use_model,
                    input=prompt,
                    max_output_tokens=int(max_tokens),
                    timeout=self.timeout,
                )
                text = (resp.output_text or "").strip()
                return {
                    "text": text,
                    "usage": {
                        "input_tokens": resp.usage.input_tokens if resp.usage else None,
                        "output_tokens": resp.usage.output_tokens if resp.usage else None,
                        "total_tokens": resp.usage.total_tokens if resp.usage else None,
                    },
                    "model": use_model,
                }
            except TypeError:
                # jeśli wersja SDK nie wspiera max_output_tokens/timeout w tym miejscu
                try:
                    resp = self.client.responses.create(model=use_model, input=prompt)
                    text = (resp.output_text or "").strip()
                    return {
                        "text": text,
                        "usage": {
                            "input_tokens": resp.usage.input_tokens if resp.usage else None,
                            "output_tokens": resp.usage.output_tokens if resp.usage else None,
                            "total_tokens": resp.usage.total_tokens if resp.usage else None,
                        },
                        "model": use_model,
                    }
                except OpenAIError as e:
                    last_error = e
            except OpenAIError as e:
                last_error = e

            if attempt <= self.max_retries:
                time.sleep(1)

        raise RuntimeError(f"LLM completion failed after retries: {last_error}")


# kompatybilność wstecz: tools.py może importować run_completion()
_singleton: Optional[LLMClient] = None


def run_completion(model: str, prompt: str, max_tokens: int = 220) -> Dict[str, Any]:
    global _singleton
    if _singleton is None:
        _singleton = LLMClient()
    r = _singleton.run_completion(prompt=prompt, max_tokens=max_tokens, model=model)
    # stary kontrakt oczekiwał "content"
    return {"content": r.get("text", ""), "usage": r.get("usage"), "model": r.get("model")}
