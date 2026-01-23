
import re
from pathlib import Path
from datetime import datetime

ROOT = Path(r"C:\AI\ai_orchestrator_scaffold")
PWSH = r"C:\Program Files\PowerShell\7\pwsh.exe"
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

# Files that should NOT be patched to PS7 (COM/Word needs Windows PowerShell 5.1)
EXCLUDE_FILENAMES = {
    "grow_to_wordcount.py",
}

def should_skip(p: Path, txt: str) -> bool:
    if p.name in EXCLUDE_FILENAMES:
        return True
    # Heuristic: COM automation / Word
    if ("-ComObject" in txt) or ("Word.Application" in txt):
        return True
    return False

def patch_file(p: Path) -> bool:
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if should_skip(p, txt):
        return False

    orig = txt

    # Replace powershell / powershell.exe string literals with PS7 path
    txt = re.sub(
        r'(["\'])powershell(?:\.exe)?\1',
        lambda m: f'{m.group(1)}{PWSH}{m.group(1)}',
        txt,
        flags=re.IGNORECASE,
    )

    # Replace hardcoded Windows PowerShell path literals with PS7 path
    txt = re.sub(
        r'(["\'])[^"\']*WindowsPowerShell\\v1\.0\\powershell\.exe\1',
        lambda m: f'{m.group(1)}{PWSH}{m.group(1)}',
        txt,
        flags=re.IGNORECASE,
    )

    if txt != orig:
        bak = p.with_suffix(p.suffix + f".bak_{stamp}")
        bak.write_text(orig, encoding="utf-8")
        p.write_text(txt, encoding="utf-8")
        return True
    return False

patched = []
for p in ROOT.rglob("*.py"):
    try:
        if patch_file(p):
            patched.append(str(p))
    except Exception:
        pass

print("PATCHED:")
for x in patched:
    print("-", x)
print("DONE")
