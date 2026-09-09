## UI_LAYER_RULE (NADRZĘDNE)

- Interfejs użytkownika (UI) jest wyłącznie warstwą operatorską.
- UI nie zawiera logiki biznesowej ani decyzyjnej.
- UI komunikuje się z backendem wyłącznie przez oficjalne endpointy API.
- Backend musi działać w 100% poprawnie bez UI.
- UI nigdy nie może wpływać na pamięć, kanon ani deterministykę systemu.
- Zmiana UI nie może wymagać zmiany rdzenia agenta.
## NOVEL_MODE_RULE (NADRZĘDNE)

- Tryb powieściowy jest priorytetowym trybem systemu.
- Powieść = jeden projekt aktywny w danym czasie (single-focus mode).
- Każdy projekt powieściowy posiada obowiązkowy book_bible.json.
- Struktura fabularna (akty, bohaterowie, timeline) musi być kontrolowana przed każdym zapisem rozdziału.
- Kanon jest nadrzędny wobec generacji tekstu.
- Każdy rozdział musi być zapisywany jako osobny artefakt (chapter_XXX.json).
- Niedozwolone jest pisanie rozdziałów bez walidacji kanonu.
- Powieść ma być projektowana pod 100k+ słów jako pełna forma literacka.
## MULTI_GUIDE_MODE_RULE (NADRZĘDNE)

- Tryb poradnikowy dopuszcza równoległą pracę na 5–7 projektach jednocześnie.## ENGINE_PRIORITY_RULE (NADRZĘDNE)

- P20.x jest jedynym silnikiem produkcyjnym.
- P0 jest silnikiem eksperymentalnym / testowym.
- P0 nie może być używany jako runtime aplikacji produkcyjnej.
- P0 służy wyłącznie do badań architektury i testów izolowanych.
- UI oraz endpoints użytkownika końcowego muszą korzystać wyłącznie z P20.x.
- Wszelkie nowe funkcje powieściowe rozwijane są na branchach feature/* i dopiero po stabilizacji mogą trafić do P20.x.
- Zabronione jest mieszanie logiki P0 i P20 w tym samym runtime.

- Każdy poradnik posiada izolowany run i izolowaną pamięć.
- Zabronione jest współdzielenie treści między poradnikami bez jawnej kontroli podobieństwa.
- Każdy zapis sekcji musi przejść kontrolę anty-duplikacji.
- Multi-project nie może wpływać na tryb powieściowy.
- Tryb powieściowy i poradnikowy są logicznie oddzielone.
- Silnik nie może mieszać kanonu powieści z treścią poradników.
# AGENT_IDENTITY_LOCK (NADRZĘDNE)

## Tożsamość systemu
- Prywatny profesjonalny agent-autorski książek na Amazon USA.
- To nie jest generator treści.
- Wysoka wartość literacka i rynkowa tekstu.

## Zakres form
- Długie formy 100k+ słów.
- Krótsze poradniki jako drugi tryb pracy.

## Tłumacz/adaptacja
- Inteligentny moduł tłumaczenia i adaptacji stylu do rynku docelowego.
- Adaptacja obejmuje idiom, rytm, ton i naturalność języka.

## Multi-project
- Multi-project jest integralną cechą platformy.
- Równoległa praca na wielu projektach przy twardej izolacji pamięci.

## Krytyczny warunek techniczny (nienegocjowalne)
- Pamięć izolacyjna per projekt: zero przecieków między książkami.
- Brak przenoszenia kontekstu między projektami bez jawnego importu.
- Wznowienia tylko w granicach tego samego projektu/runu i audytu.

## Anty-duplikacja poradników
- Przy wielu poradnikach system ma wykrywać i blokować duplikowanie treści.
- Kontrola podobieństwa outline/sekcji przed zapisem (quality gate).

## Jakość i audyt
- Deterministyczna orkiestracja.
- Pełny audyt run/step/artifacts.
- Bramka jakości: ACCEPT / REVISE / REJECT.
