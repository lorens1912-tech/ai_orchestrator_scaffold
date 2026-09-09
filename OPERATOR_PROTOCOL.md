# OPERATOR_PROTOCOL
# WARSTWA OPERACYJNA / SPOSÓB PRACY Z SYSTEMEM

## 0. CEL
- Ten dokument definiuje sposób pracy operatorskiej z projektem.
- Ten dokument nie redefiniuje architektury systemu.
- W razie konfliktu pierwszeństwo ma `MASTER_CANON_AGENTPRO.md`.

## 1. OKNA
- 🔵 = LEWE okno = SERWER.
- 🟢 = PRAWE okno = KLIENT.
- Nie używać słowa „zielone”; używać kółeczka 🟢.
- Rozdział ról okien jest obowiązkowy.

## 2. ZASADA SERWERA
- W 🔵 LEWYM oknie uruchamia się wyłącznie serwer.
- Do 🔵 LEWEGO okna wkleja się tylko:
  - start uvicorn,
  - ewentualnie zatrzymanie przez `CTRL+C`.
- Nie wykonuje się tam testów, curl, `Invoke-RestMethod`, skryptów naprawczych ani komend roboczych.

## 3. ZASADA KLIENTA
- W 🟢 PRAWYM oknie wykonuje się:
  - wszystkie testy,
  - wszystkie komendy PowerShell,
  - wszystkie `Invoke-RestMethod`,
  - wszystkie operacje na plikach,
  - wszystkie audyty,
  - wszystkie odczyty wyników.
- Jeśli komenda nie jest startem serwera, trafia do 🟢.

## 4. KOMENDY
- Zawsze podawać pełne komendy 1:1 do wklejenia.
- Bez skrótów.
- Bez opisów typu „zrób to”.
- Bez odsyłania do szukania czegoś w czacie.
- Bez patchy, jeśli możliwa jest podmiana całego pliku.
- Preferowana forma to pełny content pliku do `Set-Content`.

## 5. ŚCIEŻKI I RUN_ID
- W ścieżkach nie używać placeholderów typu `<WKLEJ_RUN_ID>`.
- Używać:
  - realnego `run_id`, albo
  - zmiennej PowerShell, np. `$r.run_id`.
- Ścieżki mają być gotowe do wklejenia bez ręcznej rekonstrukcji.

## 6. TRYB ODPOWIEDZI
- Odpowiedzi mają być konkretne, techniczne i wykonawcze.
- Bez esejów.
- Bez zbędnych wstępów.
- Gdy potrzebny jest stan projektu, używać formatu:
  - KANON
  - STAN
  - JEDEN NASTĘPNY KROK
- Stan ma być handoff-ready i od razu używalny.

## 7. ZASADA PLIKU NAD PATCHEM
- Preferowana jest podmiana całych plików.
- Patch jest wyjątkiem, nie standardem.
- Jeśli plik ma zostać poprawiony, operator dostaje pełny content pliku 1:1.

## 8. ZASADA LOGISTYKI
- Operator nie ma być proszony o ręczne szukanie rzeczy w projekcie.
- Operator ma dostać:
  - dokładną ścieżkę,
  - dokładną komendę,
  - dokładną kolejność.
- System ma minimalizować logistyczny chaos.

## 9. SERWER START
- Komenda startu serwera ma być zawsze jawnie podana, gdy praca tego wymaga.
- Start serwera należy podawać osobno jako komendę dla 🔵.
- Stop serwera odbywa się przez `CTRL+C` w 🔵.

## 10. ZASADA CIĄGŁOŚCI
- Po przejściu do nowego czatu stan ma być krótki i operacyjny.
- Handoff ma pozwalać natychmiast kontynuować pracę.
- Nie tworzyć długich raportów tam, gdzie wystarczy stan wykonawczy.

## 11. ZASADA PRAWDY OPERACYJNEJ
- Jeżeli coś nie zostało sprawdzone, nie wolno mówić, że działa.
- Najpierw komenda lub audyt, potem wniosek.
- Hipoteza nie jest wynikiem.

## 12. ZASADA DOMYŚLNA
- 🔵 tylko serwer.
- 🟢 cała reszta.
- Komendy pełne 1:1.
- Zero placeholderów w ścieżkach.
- Zero mieszania warstwy operatorskiej z konstytucją systemu.
