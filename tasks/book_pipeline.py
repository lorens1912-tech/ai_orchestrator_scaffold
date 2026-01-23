
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from llm_client import generate_text
from tools.memory import load_kanon, save_kanon

WRITING_STYLES = {
    "nonfiction_us": "Jasny, konkretny nonfiction. Amazon-ready. Zero lania wody.",
    "neo_noir": "Neo-noir. Krótkie zdania. Mrok. Zmysły. Napięcie.",
    "thriller": "Szybkie tempo. Wysokie stawki. Cliffhanger.",
    "literary": "Proza literacka. Rytm, podtekst, kontrolowana głębia.",
}

PROMPT_OUTLINE = """Zbuduj outline książki jako JSON (zwróć TYLKO JSON).
Wymagane pola:
- chapters: [{{
    "title": str,
    "goal": str,
    "key_points": [str],
    "cliffhanger": str
  }}]
- narrative_arc: str

Kanon (JSON):
{kanon_json}

Temat: {topic}
"""

PROMPT_WRITE = """Napisz rozdział na temat: "{topic}".
Docelowa długość: {words} słów.

Styl:
{style}

Zasady:
- jakość profesjonalna, Amazon-ready
- zero lania wody
- mocne otwarcie i mocne zamknięcie
- spójność z kanonem
- tempo zgodne ze stylem

Outline (JSON):
{outline_json}

Kanon (JSON):
{kanon_json}
"""

def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    root_dir = Path(__file__).resolve().parents[1]
    output_path = root_dir / "book_text.txt"

    topic = payload.get("topic", "Untitled topic")
    words = int(payload.get("words", 2000))
    style_key = payload.get("style", "nonfiction_us")
    style = WRITING_STYLES.get(style_key, WRITING_STYLES["nonfiction_us"])

    model_outline = payload.get("outline_model", payload.get("model", "gpt-4.1-mini"))
    model_write = payload.get("write_model", payload.get("model", "gpt-4.1-mini"))

    kanon: Dict[str, Any] = load_kanon() or {}
    kanon_json = json.dumps(kanon, ensure_ascii=False, indent=2)

    outline_json = generate_text(
        PROMPT_OUTLINE.format(kanon_json=kanon_json, topic=topic),
        model=model_outline,
        max_output_tokens=int(payload.get("outline_max_output_tokens", 1200)),
    ).strip()

    chapter = generate_text(
        PROMPT_WRITE.format(
            topic=topic,
            words=words,
            style=style,
            outline_json=outline_json,
            kanon_json=kanon_json,
        ),
        model=model_write,
        max_output_tokens=int(payload.get("write_max_output_tokens", 2400)),
    )

    kanon["last_run"] = {
        "topic": topic,
        "style": style_key,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    save_kanon(kanon)

    output_path.write_text(chapter, encoding="utf-8", errors="replace")

    return {"status": "OK", "output": str(output_path.name)}
