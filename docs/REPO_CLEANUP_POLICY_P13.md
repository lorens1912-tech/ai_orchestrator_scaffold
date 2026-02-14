# REPO CLEANUP POLICY P13

## Wersjonowane
- app/**
- tests/**
- scripts/**
- docs/**
- config/**
- audits/release/**

## Archiwizowane (poza historią kodu)
- runs/**
- reports/P13_*.txt
- reports/P13_*.json
- reports/P13_PYTEST_*.txt
- archive/untracked/*.zip
- HANDOFF_EXIT_*.md
- __pycache__/
- .pytest_cache/
- *.log

## Procedura clean working tree bez utraty danych
1) Zapisz listę untracked.
2) Spakuj untracked do archive/untracked/*.zip.
3) Wykonaj git stash -u z timestampem.
4) Potwierdź czystość: git status --porcelain.
5) Odtwarzanie: git stash list -> git stash apply stash@{N}.
