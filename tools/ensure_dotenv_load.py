from pathlib import Path

p = Path(r"app\main.py")
txt = p.read_text(encoding="utf-8").splitlines()

needle_import = "from dotenv import load_dotenv"
needle_call = "load_dotenv()"

# wstaw na samą górę po ewentualnym shebang/encoding
insert_at = 0
while insert_at < len(txt) and (txt[insert_at].startswith("#!") or "coding" in txt[insert_at]):
    insert_at += 1

changed = False
if needle_import not in "\n".join(txt):
    txt.insert(insert_at, needle_import)
    changed = True
    insert_at += 1

if needle_call not in "\n".join(txt):
    txt.insert(insert_at, needle_call)
    changed = True

if changed:
    p.write_text("\n".join(txt) + "\n", encoding="utf-8")
    print("[OK] dotenv enabled in app/main.py")
else:
    print("[OK] dotenv already enabled")
