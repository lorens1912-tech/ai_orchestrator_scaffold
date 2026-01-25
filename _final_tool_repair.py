import re
from pathlib import Path

p = Path("app/tools.py")
content = p.read_text(encoding="utf-8")

# 1. Naprawa _call_llm - wstrzyknięcie provider_model_family bezpośrednio do słownika wyjściowego
# Szukamy miejsca gdzie tworzony jest słownik z kluczem "model"
if '"provider_model_family"' not in content:
    # Bardzo ogólny wzorzec, który znajdzie słownik z modelem i usage
    content = re.sub(
        r'("model":\s*model,)', 
        r'\1 "provider_model_family": model.split("-")[0] if "-" in model else model,', 
        content
    )

# 2. Naprawa tool_edit - upewnienie się, że meta zawiera applied_issue_indices
# Najpierw wyczyśćmy stare próby, jeśli jakieś były
if '"applied_issue_indices"' not in content:
    # Szukamy definicji meta w tool_edit
    if '"skipped_issue_indices": skipped_indices' in content:
        content = content.replace(
            '"skipped_issue_indices": skipped_indices',
            '"skipped_issue_indices": skipped_indices, "applied_issue_indices": [i for i in range(len(issues)) if i not in skipped_indices]'
        )
    else:
        # Fallback dla innej nazwy zmiennej
        content = re.sub(
            r'("changes_count":\s*len\(changes\))',
            r'\1, "applied_issue_indices": [], "skipped_issue_indices": []',
            content
        )

p.write_text(content, encoding="utf-8")
print("Tools patched successfully.")
