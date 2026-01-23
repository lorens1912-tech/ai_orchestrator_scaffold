
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from llm_client import generate_text


def read_text(p: Path) -> str:
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def write_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8", errors="replace")


def append_text(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.exists()
    size = p.stat().st_size if existed else 0

    with p.open("a", encoding="utf-8", errors="replace", newline="\n") as f:
        if size > 0 and not read_text(p).endswith("\n"):
            f.write("\n")
        f.write(s.rstrip() + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book_dir", required=True)
    ap.add_argument("--delta_file", required=True)
    ap.add_argument("--current_file", required=True)

    ap.add_argument("--critic_model", default="gpt-4.1-mini")
    ap.add_argument("--critic_max_output_tokens", type=int, default=900)

    args = ap.parse_args()

    book_dir = Path(args.book_dir).resolve()
    delta_file = Path(args.delta_file).resolve()
    current_file = Path(args.current_file).resolve()

    notes = book_dir / "notes.md"
    outline = book_dir / "outline.md"
    qc_last = book_dir / "qc_last.md"

    delta = read_text(delta_file).strip()
    if not delta:
        write_text(qc_last, "QC: delta jest pusta — nic do oceny.\n")
        return 0

    notes_txt = read_text(notes)
    outline_txt = read_text(outline)

    qc_prompt = f"""
Jesteś surowym redaktorem i continuity checkerem (thriller/proza).
Masz ocenić TYLKO nowo dopisany fragment (DELTA) w kontekście notatek.

ZASADY:
- Pisz po polsku.
- Zero metakomantarzy o AI.
- Wykryj sprzeczności z NOTATKAMI (imiona, fakty, relacje, czas).
- Wypisz też: styl, tempo, logika, powtórki, spójność.
- Na końcu daj 3 konkretne rekomendacje na następny fragment.

NOTATKI (pamięć fabuły, fakty ustalone wcześniej):
{notes_txt if notes_txt.strip() else "(brak)"}

OUTLINE (jeśli jest):
{outline_txt if outline_txt.strip() else "(brak)"}

DELTA (nowy fragment):
{delta}
""".strip()

    qc = generate_text(
        qc_prompt,
        model=args.critic_model,
        max_output_tokens=args.critic_max_output_tokens,
    ).strip()

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    write_text(
        qc_last,
        f"# QC LAST — {stamp}\n\n"
        f"ŹRÓDŁO DELTY: {delta_file.name}\n\n"
        f"{qc}\n",
    )

    notes_prompt = f"""
Zaktualizuj NOTATKI fabularne na podstawie nowej DELTY.
Wypisz TYLKO fakty i wątki (bez lania wody), w formacie:

- POSTACIE: ...
- FAKTY: ...
- WĄTKI OTWARTE: ...
- RYZYKA/SPRZECZNOŚCI: ...

DELTA:
{delta}
""".strip()

    notes_update = generate_text(
        notes_prompt,
        model=args.critic_model,
        max_output_tokens=500,
    ).strip()

    append_text(notes, f"## Update — {stamp}\n{notes_update}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
