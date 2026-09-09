# MASTER_CANON_AGENTPRO
# WERSJA NADRZĘDNA / KONSTYTUCJA SYSTEMU

## 0. STATUS DOKUMENTU
- Ten dokument jest najwyższym źródłem prawdy dla AgentPRO.
- W razie konfliktu pierwszeństwo ma ten dokument nad:
  - handoffami,
  - opisami w czatach,
  - lokalnymi README,
  - kodem tymczasowym,
  - shimami,
  - doraźnymi fixami,
  - niejawnie przyjętymi praktykami.
- Ten dokument definiuje tożsamość, granice i reguły systemu.
- Ten dokument nie opisuje wszystkiego; definiuje to, czego nie wolno naruszyć.

## 1. TOŻSAMOŚĆ SYSTEMU
- AgentPRO jest prywatną, profesjonalną aplikacją-autorską do tworzenia książek na Amazon USA.
- AgentPRO nie jest generatorem treści.
- AgentPRO jest systemem autorskim klasy produkcyjnej.
- Agent jest wewnętrznym engine’em systemu, nie tożsamością całego produktu.
- Celem systemu jest wysoka wartość literacka, rynkowa i operacyjna.
- System ma obsługiwać:
  - długie powieści,
  - poradniki,
  - tłumaczenie,
  - adaptację stylu,
  - ciągły proces autorski od kanonu do gotowego tekstu.
- Pamięć, kanon, audyt, wznowienia, jakość i izolacja projektów są obowiązkowe.

## 2. ZASADA APP-FIRST
- System jest aplikacją, nie skryptem, nie runtime’em pomocniczym i nie zbiorem testowych obejść.
- Backend musi działać poprawnie bez UI.
- UI jest klientem systemu, a nie współautorem logiki.
- Core systemu nie może zależeć od UI, terminala, układu okien ani ręcznych procedur operatorskich.
- Architektura systemu jest nadrzędna wobec wygody chwilowej.
- Nie wolno poświęcać architektury dla doraźnego obejścia.

## 3. ZASADA ŹRÓDŁA PRAWDY
- Oficjalny porządek źródeł prawdy jest następujący:
  1. MASTER_CANON_AGENTPRO
  2. projektowy canon / book_bible / project truth
  3. zamknięte decyzje projektowe
  4. handoff
  5. bieżący krok wykonawczy
- Niższa warstwa nie może cicho redefiniować wyższej.
- Model nie może stać się źródłem prawdy przez samą generację.
- Tekst wygenerowany przez model nie jest automatycznie prawdą projektową.
- Każda trwała zmiana stanu musi być jawna, zapisana i audytowalna.

## 4. ZASADA UI
- UI jest wyłącznie warstwą operatorską.
- UI nie zawiera logiki biznesowej ani decyzyjnej.
- UI komunikuje się tylko przez oficjalne API.
- UI nie wpływa na pamięć, kanon, deterministykę ani zasady wykonawcze.
- UI nie może wymuszać zmian w core engine.
- UI nie jest źródłem prawdy.
- System musi zachować tę samą logikę niezależnie od obecności lub braku UI.

## 5. ZASADA SILNIKA
- P20.x jest jedynym silnikiem produkcyjnym.
- P0 jest wyłącznie eksperymentalny lub testowy.
- Zakaz mieszania P0 i P20.x w jednym runtime.
- Endpointy użytkownika i UI działają wyłącznie na P20.x.
- Compat, fallback, hotfix i shim są dopuszczalne wyłącznie przejściowo.
- Żadna warstwa przejściowa nie może stać się trwałym fundamentem systemu.
- Każda funkcja docelowa ma zostać przeniesiona do czystego P20.x albo usunięta.

## 6. ZASADA JEDNEGO RUNTIME’U PRODUKCYJNEGO
- System ma mieć jeden oficjalny runtime produkcyjny.
- Równoległe ścieżki wykonawcze mogą istnieć wyłącznie tymczasowo i muszą mieć plan usunięcia.
- Test przechodzący na shimie nie jest dowodem czystej architektury.
- Każda publiczna ścieżka wykonania musi być zgodna z produkcyjnym ownerem systemu.
- Publiczny kontrakt API nie może zmieniać znaczenia w zależności od wewnętrznego obejścia.

## 7. PRIORYTET TRYBÓW
- Novel mode ma absolutny priorytet strategiczny.
- Najpierw domykany jest novel mode.
- Guide mode nie może rozmywać, opóźniać ani destabilizować novel mode.
- Multi-project jest dozwolony tylko w guide mode.
- Rozwój guide mode jest wtórny wobec stabilnego toru novel mode.

## 8. NOVEL MODE
- Novel mode oznacza tryb produkcyjnego pisania długiej powieści.
- Novel mode jest projektowany dla 100k+ słów.
- Spójność długiej formy jest ważniejsza niż szybkość generacji.
- W novel mode może istnieć tylko jeden aktywny projekt powieściowy na jeden aktywny runtime produkcyjny użytkownika.
- Aktywny projekt powieściowy oznacza projekt, do którego runtime może wykonywać zapis, wznowienie lub walidację kanonu.
- Równoległe powieści mogą istnieć jako dane, ale tylko jedna może być aktywnie wykonywana w novel mode w tym samym czasie.

## 9. KONTRAKT POWIEŚCIOWY
- Każdy projekt powieściowy musi posiadać `book_bible.json`.
- `book_bible.json` jest obowiązkowym kontraktem projektu, nie dodatkiem.
- Bez ważnego `book_bible.json` nie wolno wykonać zapisu rozdziału.
- Każdy rozdział musi być zapisany jako osobny artefakt `chapter_XXX.json`.
- Nie wolno pisać rozdziału bez walidacji kanonu przed zapisem.
- Nie wolno zaakceptować rozdziału niezgodnego z kanonem.
- Nie wolno cicho naprawiać zgodności po fakcie bez jawnej decyzji i audytu.

## 10. GUIDE MODE
- Guide mode może obsługiwać równoległe projekty.
- Każdy projekt guide mode ma izolowany run, pamięć i artefakty.
- Guide mode jest logicznie oddzielony od novel mode.
- Żaden element guide mode nie może zanieczyszczać pamięci, kanonu ani stanu novel mode.
- Obowiązuje kontrola anty-duplikacji i kontroli podobieństwa między poradnikami.
- Multi-project w guide mode nie znosi wymogu audytu i izolacji.

## 11. IZOLACJA PAMIĘCI
- Zero przecieków pamięci między projektami.
- Brak transferu kontekstu bez jawnego importu.
- Każdy projekt ma własną pamięć operacyjną, długą i artefaktową.
- Wspólna pamięć między książkami jest zabroniona, chyba że zostanie jawnie wykonany import.
- Import musi być:
  - jawny,
  - ograniczony zakresem,
  - zapisany w audycie,
  - możliwy do odtworzenia.
- Izolacja pamięci jest wymogiem twardym.

## 12. WZNOWIENIA
- Wznowienie jest dozwolone wyłącznie w granicach tego samego projektu.
- Domyślne wznowienie jest dozwolone wyłącznie dla zgodnego `project_id`, `book_id`, `run_id` i audytu.
- Wznowienie nie może przekroczyć granic obcego projektu.
- Wznowienie nie może używać stanu, którego pochodzenie nie jest audytowalne.
- Wznowienie z niezgodnym project truth, canon sha albo obcym run state musi zostać zablokowane.
- Kontrolowane odgałęzienie jest dozwolone tylko jako jawna operacja fork/clone/import z własnym audytem; nie jako cichy resume.

## 13. KANON
- Kanon jest nadrzędny wobec generacji tekstu.
- Generacja ma służyć kanonowi, a nie go redefiniować.
- Źródłami kanonu są wyłącznie:
  - `book_bible.json`,
  - jawny timeline projektu,
  - jawne fakty projektu,
  - jawne decyzje projektu,
  - zatwierdzone rozdziały,
  - jawnie zapisane reguły projektu,
  - jawne, audytowalne importy.
- Kanon nie może być nadpisywany przez model w sposób cichy.
- Każda zmiana kanonu musi być:
  - jawna,
  - walidowalna,
  - audytowalna,
  - zapisana.
- Brak zgodności z kanonem oznacza blokadę zapisu.

## 14. WALIDACJA KANONU
- Każdy zapis trwały musi przejść walidację kanonu.
- Walidacja przed zapisem jest obowiązkowa.
- Walidacja po zapisie kontrolnym jest obowiązkowa wszędzie tam, gdzie architektura kroku tego wymaga.
- Pozytywny wynik generacji nie unieważnia negatywnego wyniku walidacji.
- Jeśli walidacja i generacja są sprzeczne, pierwszeństwo ma walidacja.

## 15. JAKOŚĆ I DECYZJE
- Bramka jakości działa wyłącznie w modelu:
  - ACCEPT
  - REVISE
  - REJECT
- Inne statusy nie mogą zastępować tej triady jako kontraktu procesu.
- ACCEPT oznacza zgodę na przejście do następnego dozwolonego kroku.
- REVISE oznacza konieczność poprawy bez uznania wyniku za końcowo zaakceptowany.
- REJECT oznacza blokadę przejścia lub zapisu zgodnie z regułami danego kroku.
- Jakość nie jest tylko opinią modelu.
- Jakość jest częścią kontraktu wykonawczego systemu.

## 16. AUDYT
- Każdy run musi być audytowalny.
- Każdy step musi być audytowalny.
- Każdy trwały artifact musi być audytowalny.
- Audyt musi pozwalać odtworzyć:
  - wejście,
  - decyzję,
  - źródła prawdy,
  - zależności,
  - artefakty,
  - wynik końcowy.
- Dobrze wyglądający chaos bez śladu audytu jest traktowany jako błąd systemowy.

## 17. DETERMINISTYCZNA ORKIESTRACJA
- Determinizm oznacza deterministyczną orkiestrację procesu, nie absolutnie identyczny tekst modelu w każdych warunkach.
- Muszą być deterministyczne:
  - kolejność kroków,
  - źródła prawdy użyte w kroku,
  - reguły decyzji,
  - zapis audytu,
  - kontrola przejść między krokami,
  - zasady resume i locków,
  - kontrakt artefaktów.
- Niedeterministyczny model nie zwalnia systemu z deterministycznej kontroli procesu.

## 18. LOCKI I OCHRONA STANU
- Konflikty run lock i book lock są błędami krytycznymi.
- System nie może wykonywać zapisu, jeśli aktywny lock narusza integralność projektu lub runu.
- Locki muszą być sprawdzalne, audytowalne i spójne z zasadą wznowień.
- Ominięcie locka przez obejście aplikacyjne jest zabronione.

## 19. ARCHITEKTURA / ENDPOINTY / TESTY
- Architektura, endpointy i testy muszą być zgodne.
- Endpointy są częścią kontraktu systemu.
- Testy kontraktowe są częścią kontraktu systemu.
- Żaden endpoint nie może publicznie udawać kontraktu sprzecznego z rzeczywistym runtime.
- Żaden test nie może wymuszać zachowania sprzecznego z master canonem.
- Jeśli test jest sprzeczny z master canonem, poprawia się test, a nie łamie canon.
- Jeśli kod jest sprzeczny z master canonem, poprawia się kod, a nie narrację o kodzie.

## 20. SHIMY / COMPAT / MIGRACJE
- Shim jest dozwolony tylko jako warstwa przejściowa.
- Compat jest dozwolony tylko jako warstwa przejściowa.
- Fallback jest dozwolony tylko jako warstwa przejściowa.
- Każda warstwa przejściowa musi mieć:
  - jawny status tymczasowy,
  - jawny zakres,
  - jawne ryzyko,
  - plan usunięcia.
- Nie wolno budować trwałej architektury na obejściach.

## 21. WORKFLOW
- Workflow użytkownika ma być elastyczny.
- Elastyczność dotyczy sposobu pracy, nie zasad bezpieczeństwa i spójności.
- Opcjonalne mogą być:
  - agent writes,
  - user edits,
  - discussion mode,
  - external import,
  - kolejność niektórych kroków roboczych.
- Nieopcjonalne są:
  - walidacja kanonu,
  - audyt,
  - izolacja pamięci,
  - zgodność z kontraktem jakości,
  - zgodność z kontraktem runtime.
- Workflow ma być konfigurowalny, ale nie anarchiczny.

## 22. TŁUMACZENIE I ADAPTACJA
- Tłumaczenie i adaptacja są integralną częścią systemu.
- Adaptacja ma zachować sens, intencję, siłę narracyjną i jakość literacką oryginału.
- Tekst docelowy ma brzmieć jak tekst napisany natywnie, nie jak tłumaczenie techniczne.
- Adaptacja nie może naruszać kanonu projektu bez jawnej decyzji.

## 23. CIĄGŁOŚĆ
- Każdy nowy czat jest kontynuacją projektu, jeśli istnieje już ustalony stan.
- Zakaz redefiniowania projektu od zera bez jawnej decyzji.
- Zmiana kierunku wymaga jawnej decyzji projektowej.
- Handoff nie jest nowym źródłem prawdy; jest nośnikiem ciągłości.
- System ma chronić ciągłość między czatami, runami i etapami pracy.

## 24. STABILNOŚĆ PRODUKCYJNA
- Wznowienia, audyt, odporność na przerwania i brak konfliktów plików są obowiązkowe.
- Niestabilne entrypointy, dryf runtime, konflikt kontraktów i niejawne mutacje stanu są błędami krytycznymi.
- Najpierw stabilność i zgodność kontraktu, potem rozbudowa funkcji.
- Najpierw jeden czysty runtime produkcyjny, potem rozszerzenia.

## 25. DYSCYPLINA DECYZYJNA
- Tezy o stanie systemu muszą być oznaczane jako:
  - [DOWÓD]
  - [SPECYFIKACJA]
  - [ZAŁOŻENIE]
- Nie wolno sprzedawać założeń jako faktów.
- Jeśli stan systemu jest niepewny, najpierw audyt, potem wniosek.
- Prawda o stanie systemu ma pierwszeństwo przed wygodną narracją.

## 26. ZAKRES DOKUMENTU
- Ten dokument definiuje konstytucję systemu.
- Ten dokument nie zawiera:
  - protokołu operatorskiego okien,
  - formatu odpowiedzi czatowych,
  - komend terminalowych 1:1,
  - lokalnych instrukcji uruchamiania.
- Takie reguły mają żyć w osobnych dokumentach operacyjnych.
- Nie wolno mieszać warstwy konstytucyjnej z warstwą operatorską.

## 27. REGUŁA KOŃCOWA
- Najpierw jeden stabilny, czysty, zgodny z canonem runtime AgentPRO.
- Potem pełna zgodność architektury, endpointów i testów.
- Potem rozbudowa funkcji.
- Novel mode, kanon, izolacja pamięci, audyt i jakość pozostają priorytetem absolutnym.

## 28. TEST POPRAWNOŚCI INTERPRETACJI
- Jeśli istnieją dwie interpretacje reguły, prawidłowa jest ta, która:
  - lepiej chroni kanon,
  - lepiej chroni izolację projektów,
  - lepiej chroni audyt,
  - lepiej chroni deterministyczną orkiestrację,
  - lepiej chroni czysty runtime P20.x,
  - mniej zależy od shimów i wyjątków.
