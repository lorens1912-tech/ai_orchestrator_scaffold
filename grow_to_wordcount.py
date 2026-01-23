
# grow_to_wordcount.py
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# COM/Word: najstabilniej Windows PowerShell 5.1
PS5 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def word_count_via_word_com(txt_path: Path) -> int:
    ps_script = f"""
$ErrorActionPreference = "Stop"
$path = '{str(txt_path)}'
if (!(Test-Path -LiteralPath $path)) {{ Write-Output 0; exit 0 }}

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$word.DisplayAlerts = 0

$doc = $word.Documents.Open($path, $false, $true)
$count = $doc.ComputeStatistics(0)

$doc.Close([ref]$false) | Out-Null
$word.Quit() | Out-Null

[System.Runtime.Interopservices.Marshal]::ReleaseComObject($doc) | Out-Null
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($word) | Out-Null
[GC]::Collect()
[GC]::WaitForPendingFinalizers()

Write-Output $count
"""
    ps_exe = PS5 if Path(PS5).exists() else "powershell.exe"

    r = subprocess.run(
        [ps_exe, "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if r.returncode != 0:
        raise RuntimeError(f"WordCount PS failed: {r.stderr.strip()}")
    out = (r.stdout or "").strip()
    try:
        return int(out)
    except ValueError:
        raise RuntimeError(f"WordCount parse failed. stdout={out!r} stderr={r.stderr!r}")


def tail_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    data = path.read_text(encoding="utf-8", errors="replace")
    return data[-max_chars:]


def append_utf8(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace", newline="\n") as f:
        f.write(text)
        if not text.endswith("\n"):
            f.write("\n")
        f.write("\n")


def generate_chunk(prompt: str, model: str, max_output_tokens: int, retries: int = 4) -> str:
    from llm_client import generate_text  # noqa

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            chunk = generate_text(prompt, model=model, max_output_tokens=max_output_tokens)
            if not chunk or not chunk.strip():
                raise RuntimeError("LLM returned empty text")
            return chunk.strip()
        except Exception as e:
            last_err = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"LLM failed after retries: {last_err}")


def resolve_output_path(args: argparse.Namespace) -> Path:
    in_path = Path(args.file).resolve()

    if args.newfile:
        outdir = Path(args.outdir).resolve() if args.outdir else in_path.parent.resolve()
        outdir.mkdir(parents=True, exist_ok=True)

        prefix = args.prefix or in_path.stem
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = outdir / f"{prefix}_{stamp}.txt"

        if in_path.exists():
            shutil.copyfile(in_path, out_path)
        else:
            out_path.write_text("", encoding="utf-8")

        return out_path

    if not in_path.exists():
        in_path.write_text("", encoding="utf-8")
    return in_path


def load_base_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        pf = Path(args.prompt_file).resolve()
        if not pf.exists():
            raise FileNotFoundError(f"prompt_file not found: {pf}")
        return pf.read_text(encoding="utf-8", errors="replace").strip() or args.base_prompt
    return args.base_prompt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="book_text.txt")
    ap.add_argument("--target", type=int, default=6000)
    ap.add_argument("--model", default="gpt-4.1-mini")
    ap.add_argument("--max_output_tokens", type=int, default=1200)
    ap.add_argument("--context_chars", type=int, default=2500)
    ap.add_argument("--base_prompt", default="Pisz dalej po polsku. Kontynuuj spójnie, bez nagłówków technicznych.")
    ap.add_argument("--prompt_file", default="", help="Ścieżka do pliku promptu (UTF-8). Nadpisuje --base_prompt.")

    ap.add_argument("--newfile", action="store_true")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--prefix", default="")

    args = ap.parse_args()

    txt_path = resolve_output_path(args)
    base_prompt = load_base_prompt(args)

    wc = word_count_via_word_com(txt_path)
    print(f"START WORD_COUNT={wc} TARGET={args.target} FILE={txt_path} MODEL={args.model}")

    safety_iters = 200
    it = 0
    while wc < args.target and it < safety_iters:
        it += 1

        context = tail_text(txt_path, args.context_chars)
        prompt = (
            f"{base_prompt}\n\n"
            f"=== OSTATNI FRAGMENT TEKSTU (kontekst, kontynuuj) ===\n"
            f"{context}\n"
            f"=== KONIEC KONTEKSTU ===\n\n"
            f"Kontynuuj dalej w tym samym stylu i wątku. Dopisz kolejne akapity."
        )

        chunk = generate_chunk(prompt, model=args.model, max_output_tokens=args.max_output_tokens)
        append_utf8(txt_path, chunk)

        wc = word_count_via_word_com(txt_path)
        print(f"ITER={it} WORD_COUNT={wc}")

    if wc >= args.target:
        print(f"DONE WORD_COUNT={wc} (>= {args.target})")
        return 0

    print(f"STOP safety_iters hit. WORD_COUNT={wc}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
