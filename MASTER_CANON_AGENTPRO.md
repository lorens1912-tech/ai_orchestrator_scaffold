# MASTER_CANON_AGENTPRO
## WERSJA 2 — APPROVED / KONSTYTUCJA SYSTEMU
## STATUS: ZAMKNIĘTY / NAJWYŻSZE ŹRÓDŁO PRAWDY

======================================================================
0. ROLA DOKUMENTU
======================================================================

MASTER_CANON_AGENTPRO definiuje to, CO MUSI BYĆ PRAWDĄ o AgentPRO.

Nie jest instrukcją implementacyjną i nie opisuje chwilowego stanu kodu.

W razie konfliktu pierwszeństwo ma kolejno:

MASTER_CANON_AGENTPRO
→ CANON SERII / CANON KSIĄŻKI / BOOK BIBLE / PROJECT TRUTH
→ ZATWIERDZONE DECYZJE UŻYTKOWNIKA I ZAMKNIĘTE KONTRAKTY
→ ZATWIERDZONY STAN / HANDOFF
→ BIEŻĄCY KROK
→ IMPLEMENTACJA
→ TESTY
→ WYNIK MODELU.

Niższa warstwa nie może cicho redefiniować wyższej.

======================================================================
1. TOŻSAMOŚĆ AGENTPRO
======================================================================

AgentPRO jest prywatną, profesjonalną aplikacją-autorską do tworzenia książek na rynek Amazon USA.

AgentPRO nie jest generatorem treści.

Agent jest wewnętrznym engine’em aplikacji, nie całym produktem.

Celem AgentPRO jest wysoka wartość literacka, rynkowa i operacyjna tekstu oraz pełna kontrola procesu autorskiego.

======================================================================
2. ZASADA APP-FIRST
======================================================================

AgentPRO jest aplikacją.

Backend musi działać poprawnie bez UI.

UI jest wyłącznie warstwą operatorską.

UI nie może być właścicielem:
- pamięci,
- Kanonu,
- decyzji jakościowych,
- orkiestracji,
- model routingu,
- storage,
- logiki domenowej.

UI nie może wpływać na pamięć, Kanon ani deterministykę procesu.

======================================================================
3. OFICJALNA GRANICA API
======================================================================

UI komunikuje się z backendem wyłącznie przez oficjalne API.

Wewnętrzny backend nie powinien komunikować się ze sobą przez HTTP do localhost, jeżeli może użyć serwisów/repozytoriów bezpośrednio.

Oficjalnym ownerem API pozostaje app.main.

app.main ma być cienką warstwą transportową, a nie alternatywnym silnikiem domenowym.

======================================================================
4. SILNIK PRODUKCYJNY
======================================================================

P20.x jest jedynym silnikiem produkcyjnym AgentPRO.

P0 jest wyłącznie eksperymentalny, testowy lub historyczny.

P0 i P20.x nie mogą być mieszane w jednym produkcyjnym runtime.

Compat, shim, hotfix i fallback mogą istnieć przejściowo, ale nie mogą stać się docelowym fundamentem.

======================================================================
5. DETERMINISTYCZNA ORKIESTRACJA
======================================================================

Deterministyczność AgentPRO oznacza deterministyczny proces, nie gwarancję identycznego tekstu LLM.

Deterministyczne muszą być co najmniej:
- kolejność kroków,
- wybór polityki,
- źródła prawdy,
- przejścia między stanami,
- zasady resume,
- zasady locków,
- zapis audytu,
- kontrakt artefaktów.

======================================================================
6. PROJEKT JAKO GRANICA IZOLACJI
======================================================================

Każdy run należy dokładnie do jednego projektu.

Każdy projekt ma własną tożsamość, pamięć, Kanon, Book Bible, styl, research, artefakty, runy, locki, audyt i tłumaczenia.

Zero przecieków pamięci pomiędzy niezależnymi projektami.

Transfer danych pomiędzy projektami jest możliwy tylko przez jawny, audytowalny import.

======================================================================
7. NOVEL MODE MA PRIORYTET
======================================================================

Novel Mode jest priorytetowym trybem AgentPRO.

Dla powieści obowiązuje jedna aktywnie wykonywana książka na danym torze produkcyjnym użytkownika.

System może przechowywać wiele projektów, lecz Novel Mode nie może cicho mieszać ich kontekstu ani stanu.

Guide Mode pozostaje logicznie odseparowany i nie może destabilizować Novel Mode.

======================================================================
8. DŁUGA FORMA
======================================================================

AgentPRO musi być projektowany dla powieści 100k–200k+ słów oraz serii/sag wielotomowych.

Architektura pamięci i kontroli nie może zakładać, że cały rękopis mieści się w jednym promptcie.

Analiza długiej formy jest hierarchiczna.

======================================================================
9. HIERARCHIA NARRACYJNA
======================================================================

Dla powieści obowiązuje model:

SERIES
→ VOLUME / BOOK
→ ACT
→ SEQUENCE
→ CHAPTER
→ SCENE.

Struktura narracyjna jest danymi systemu, nie tylko tekstem konspektu.

======================================================================
10. SERIES CANON I VOLUME CANON
======================================================================

Seria może posiadać własny Series Canon oraz Series Memory.

Tom/książka posiada własny Book/Volume Canon i Book Bible.

Book Canon nie może cicho naruszać Series Canon.

Zmiana wspólnej prawdy serii wymaga kontrolowanej decyzji i analizy wpływu.

======================================================================
11. BOOK BIBLE
======================================================================

Każdy projekt powieściowy musi posiadać book_bible.json.

Book Bible jest obowiązkowym kontraktem projektu.

Nie wolno zapisać produkcyjnego rozdziału bez ważnego Book Bible i walidacji Kanonu.

Book Bible nie jest jedyną pamięcią książki.

======================================================================
12. ARTEFAKT ROZDZIAŁU
======================================================================

Każdy rozdział produkcyjny jest zapisywany jako wersjonowany artefakt chapter_XXX.json.

Rozdział musi mieć identyfikowalne pochodzenie, wersję, parent version, źródła kontekstu, model, rolę, status jakości i hash.

Nie wolno cicho nadpisywać zaakceptowanej wersji.

======================================================================
13. STABILNE TOŻSAMOŚCI ENCJI
======================================================================

Kluczowe encje mają trwałe ID niezależne od nazw.

Zmiana nazwy postaci, miejsca, organizacji lub innej encji nie może niszczyć jej relacji i historii.

======================================================================
14. LLM NIE JEST PAMIĘCIĄ
======================================================================

LLM nie jest pamięcią projektu.

Pamięć znajduje się w trwałym, audytowalnym stanie AgentPRO.

Model otrzymuje wyłącznie kontekst przygotowany dla konkretnego zadania.

======================================================================
15. PAMIĘĆ HIERARCHICZNA
======================================================================

AgentPRO utrzymuje pamięć na poziomie odpowiednim do skali:
- scena,
- rozdział,
- sekwencja,
- akt,
- książka,
- seria.

Po zamknięciu tomu system może tworzyć trwały snapshot stanu potrzebny następnym tomom.

======================================================================
16. KANON NAD GENERACJĄ
======================================================================

Kanon ma pierwszeństwo przed generacją.

Model nie może stać się źródłem prawdy tylko dlatego, że coś wygenerował.

Nowa informacja staje się trwałą prawdą dopiero po przejściu właściwego procesu akceptacji i zapisu.

======================================================================
17. WALIDACJA PRZED ZAPISEM
======================================================================

Nie wolno pisać i utrwalać produkcyjnego rozdziału bez walidacji obowiązującego Kanonu.

Jeżeli tekst narusza Kanon, nie może zostać zaakceptowany jako poprawny artefakt końcowy.

======================================================================
18. FROZEN FACT
======================================================================

frozen oznacza ochronę integralności kanonicznej.

frozen=true oznacza: nie wolno automatycznie zmienić tego elementu bez formalnego procesu zmiany Kanonu.

frozen nie oznacza: wklejaj ten fakt do każdego promptu.

======================================================================
19. AUTHOR_LOCKED
======================================================================

author_locked oznacza blokadę nałożoną przez użytkownika.

Element author_locked nie może zostać zmieniony bez zgody użytkownika.

frozen i author_locked są niezależnymi osiami ochrony.

======================================================================
20. ZMIANA KANONU
======================================================================

Zmiana chronionego Kanonu wymaga co najmniej:

CHANGE PROPOSAL
→ IMPACT ANALYSIS
→ USER APPROVAL
→ VERSIONED CHANGE
→ INVALIDATION / REBUILD danych pochodnych
→ AUDIT.

======================================================================
21. LOCK TECHNICZNY
======================================================================

PROJECT / BOOK / RUN LOCK jest mechanizmem technicznym i nie jest tym samym co frozen ani author_locked.

Konflikt locków blokujący integralność zapisu jest błędem krytycznym.

======================================================================
22. WIEDZA POSTACI
======================================================================

AgentPRO musi wiedzieć nie tylko kim jest postać, ale również:
- co wie,
- czego nie wie,
- w co wierzy,
- co podejrzewa,
- kiedy uzyskała informację,
- kiedy informacja przestała być prawdziwa.

======================================================================
23. WIEDZA CZYTELNIKA
======================================================================

System śledzi stan wiedzy czytelnika niezależnie od stanu wiedzy postaci.

Reveal, twist i payoff nie mogą być kontrolowane wyłącznie przez pamięć modelu.

======================================================================
24. CHARAKTER I STAN POSTACI
======================================================================

Ważna postać ma trwały profil oraz wersjonowany stan.

Profil obejmuje tożsamość, biografię, wygląd, psychologię, reakcje, historię, cele, relacje, wiedzę i bieżący stan.

======================================================================
25. GŁOS POSTACI
======================================================================

Ważna postać może posiadać osobny VoiceProfile.

Głos postaci jest profilem zachowania językowego, nie pojedynczą powtarzaną frazą.

Profil może zależeć od rozmówcy, sytuacji i stanu emocjonalnego.

======================================================================
26. RELACJE
======================================================================

Relacje postaci są wersjonowanym stanem w czasie.

System ma móc ustalić, jaka relacja obowiązywała w określonej scenie/tomie.

======================================================================
27. THREADS / SETUP / PAYOFF
======================================================================

AgentPRO śledzi aktywne wątki, otwarcia, progresję, zamknięcia, setupy i payoffy.

Wielotomowe wątki mogą należeć do Series Scope.

======================================================================
28. CAUSALITY
======================================================================

System musi pozwalać kontrolować zależności przyczynowo-skutkowe.

Zmiana faktu, wydarzenia lub decyzji fabularnej może wymagać analizy wpływu na późniejsze sceny i artefakty.

======================================================================
29. SCENE CONTRACT
======================================================================

Scena jest kontrolowanym kontraktem narracyjnym, zawierającym cel, uczestników, POV, miejsce, czas, konflikt, stawkę, outcome, zmianę stanu oraz istotne wymagania kanoniczne.

======================================================================
30. MIEJSCE I TRASA
======================================================================

Miejsce jest encją.

Trasa jest encją.

Fizyczna wiarygodność ruchu, odległości, czasu i logistyki może podlegać walidacji.

======================================================================
31. REALNE I FIKCYJNE
======================================================================

System rozróżnia fakty realne, niezweryfikowane, projektowo-kanoniczne, fikcyjne oraz fikcyjne nakładki na rzeczywisty świat.

Fikcja świadoma nie może być automatycznie traktowana jako błąd researchu.

======================================================================
32. RESEARCH JAKO PIERWSZORZĘDNA WARSTWA
======================================================================

Research jest trwałym, audytowalnym procesem.

Minimalny przepływ:

SOURCE
→ CLAIM
→ VERIFY
→ DECISION
→ opcjonalnie CANON.

Research nie staje się automatycznie Kanonem.

======================================================================
33. PROVENANCE
======================================================================

Każdy istotny fakt, claim, decyzja, ocena i artefakt powinien mieć możliwe do odtworzenia pochodzenie.

System musi wiedzieć skąd dana informacja pochodzi i jaka wersja źródła ją ustanowiła.

======================================================================
34. TERMINOLOGIA
======================================================================

AgentPRO może prowadzić semantyczny rejestr terminów, definicji oraz polityki prezentacji: inline, footnote, endnote, glossary, none.

Decyzja semantyczna jest oddzielona od fizycznego składu strony.

======================================================================
35. STYLE PROFILE
======================================================================

Styl książki jest wersjonowanym profilem projektu.

System może posiadać style funkcjonalne dla różnych zadań i warstw tekstu.

Styl nie jest utożsamiany z jedną nazwą autora ani prostym promptem.

======================================================================
36. REFERENCJE AUTORSKIE
======================================================================

Jeżeli styl korzysta z inspiracji znanymi autorami, AgentPRO przechowuje i stosuje abstrakcyjne cechy warsztatowe, a nie polecenie kopiowania rozpoznawalnego głosu konkretnego żyjącego autora.

======================================================================
37. NATURALNOŚĆ TEKSTU
======================================================================

Celem jest naturalna, literacka proza wysokiej jakości.

AgentPRO nie jest systemem do obchodzenia detektorów AI.

Ocena naturalności służy jakości literackiej, nie ukrywaniu pochodzenia tekstu.

======================================================================
38. MODE / ROLE / PRESET
======================================================================

MODE, ROLE i PRESET są trzema różnymi pojęciami.

MODE = rodzaj operacji.
ROLE = odpowiedzialność wykonawcza.
PRESET = orkiestracja kroków.

Nie wolno ich scalać w jeden parametr o zmiennym znaczeniu.

======================================================================
39. ROLA NIE JEST MODELEM
======================================================================

Rola jest funkcją systemową.

Model jest wymiennym wykonawcą wybranym przez Model Router.

AgentPRO nie może być trwale zależny od jednego providera/modelu.

======================================================================
40. MODEL ROUTER
======================================================================

System rozróżnia requested_model i effective_model.

Audyt zapisuje model rzeczywiście użyty.

System nie może raportować użytkownikowi modelu innego niż faktycznie użyty.

======================================================================
41. PARAMETRY MODELU
======================================================================

Dla wywołań mających znaczenie dla audytu system zapisuje efektywne parametry istotne dla wyniku, w zakresie wspieranym przez providera/model.

Może to obejmować temperature, top_p, seed, reasoning effort i inne parametry polityki.

======================================================================
42. EMBEDDING / SEMANTIC RETRIEVAL PROVENANCE
======================================================================

Jeżeli system używa embeddingów lub innego retrievalu semantycznego, identyfikacja modelu/wersji indeksu musi być audytowalna i uwzględniana w polityce unieważniania danych pochodnych.

======================================================================
43. CONTEXT BUILDER
======================================================================

Przed istotnym wywołaniem modelu AgentPRO buduje kontrolowany ContextPackage.

Model nie pobiera samowolnie całej pamięci projektu.

Context Builder zna budżet kontekstu przed retrievalem.

======================================================================
44. CONTEXT PROFILE PER ROLE
======================================================================

Każda rola posiada własny profil kontekstu.

Pisarz, Krytyk, Strażnik Ciągłości i Badacz nie muszą otrzymywać tych samych danych.

======================================================================
45. MUST_INCLUDE
======================================================================

Elementy oznaczone must_include muszą wejść do odpowiedniego ContextPackage.

Krytyczny Kanon nie może być cicho obcięty z powodu overflow.

======================================================================
46. RELEVANCJA ZAMROŻONYCH FAKTÓW
======================================================================

frozen/author_locked fact trafia do kontekstu, jeżeli jest relewantny dla zadania albo wymuszony polityką.

Ochrona faktu w pamięci nie oznacza automatycznej obecności w każdym promptcie.

======================================================================
47. RECENCY
======================================================================

Recency ma znaczenie narracyjne, nie numeryczne.

Nie wolno wyznaczać ważności na podstawie samego ID encji.

Stary fakt może pozostać krytycznie ważny, jeżeli jest związany grafowo, fabularnie lub przez payoff.

======================================================================
48. SPRZECZNOŚCI W KONTEKŚCIE
======================================================================

Nierozstrzygnięta sprzeczność nie może zostać ukryta przez ranking.

Jeżeli jest relewantna, ContextPackage zawiera ostrzeżenie i reprezentuje konflikt zgodnie z polityką.

======================================================================
49. CONTEXT OVERFLOW
======================================================================

Gdy kontekst krytyczny przekracza budżet, system nie może cicho usuwać obowiązkowej prawdy.

Może kolejno redukować dane nieobowiązkowe, stosować reprezentacje strukturalne/kompresję bez utraty sensu, dzielić zadanie, wykonywać analizę pomocniczą lub użyć większego modelu zgodnie z polityką.

Nierozwiązywalny konflikt budżetu jest eskalowany.

======================================================================
50. CONTEXT TRACE
======================================================================

ContextPackage jest audytowalny.

System zapisuje dlaczego element został włączony, jaka reprezentacja została użyta i jaki był koszt kontekstu.

Pełny trace odrzuconych kandydatów może być diagnostyczny, nie musi być zawsze trwałym masowym artefaktem.

======================================================================
51. CONTEXT HASH
======================================================================

context_hash musi zależeć od treści i wersji elementów kontekstu, ich reprezentacji, polityki, Kanonu, Book Bible, stylu oraz innych elementów mających wpływ na użyty kontekst.

Hashowanie musi być stabilne dla tej samej kanonicznej reprezentacji danych.

======================================================================
52. RETRY TECHNICZNY
======================================================================

Techniczny retry tego samego kroku nie jest nową próbą twórczą.

Powinien używać tego samego zapisanego ContextPackage, jeżeli retry dotyczy tej samej logicznej operacji.

======================================================================
53. REEVALUATION
======================================================================

PONÓW TECHNICZNIE i OCEŃ PONOWNIE to różne operacje.

Reevaluation tworzy nową ocenę i nowe evaluation_id.

======================================================================
54. QUALITY GATE
======================================================================

Bramka jakości ma trzy decyzje:

ACCEPT
REVISE
REJECT.

Te decyzje nie są tym samym co status technicznego wykonania kroku.

======================================================================
55. QUALITY ≠ EXECUTION STATUS
======================================================================

Krok może wykonać się technicznie poprawnie i jednocześnie otrzymać REVISE albo REJECT.

System przechowuje te dwa wymiary oddzielnie.

======================================================================
56. PĘTLA POPRAWY
======================================================================

REVISE prowadzi do ukierunkowanej poprawy opartej na konkretnych wykrytych problemach.

System nie powinien bez potrzeby przepisywać całego tekstu, jeżeli wymagane są lokalne poprawki.

======================================================================
57. EVALUATION RECORD
======================================================================

Ocena jakości jest wersjonowanym rekordem z kryteriami, kontekstem, modelem, parametrami, decyzją i uzasadnieniem.

======================================================================
58. EVALUATION CACHE
======================================================================

System może cache’ować ukończoną ocenę na podstawie kompletnego klucza zależnego od artefaktu, kryteriów, promptu, modelu i kontekstu.

Cache nie może maskować świadomej operacji OCEŃ PONOWNIE.

======================================================================
59. QA SCENY
======================================================================

Scena może być oceniana pod kątem celu, konfliktu, stawki, zmiany stanu, spójności, stylu, wiedzy, miejsca, czasu i skutku narracyjnego.

======================================================================
60. QA ROZDZIAŁU
======================================================================

Rozdział przechodzi kontrolę struktury, spójności, stylu, głosów, wiedzy, Kanonu i jakości jako całość.

======================================================================
61. QA KSIĄŻKI
======================================================================

Po ukończeniu rozdziałów system wykonuje analizę całej książki hierarchicznie, obejmując m.in. strukturę, causality, pacing, tension, arcs, continuity, knowledge states, reader knowledge, threads, setups/payoffs, styl, głosy, redundancję, research i final quality.

======================================================================
62. CANDIDATE MASTER
======================================================================

Wersja spełniająca wewnętrzne kryteria AgentPRO może zostać Candidate Master.

Candidate Master nie jest jeszcze Source Masterem.

======================================================================
63. ZEWNĘTRZNI KRYTYCY
======================================================================

Zewnętrzny niezależny krytyk/evaluator nie jest częścią AgentPRO.

Może oceniać eksportowaną książkę z zewnątrz, ale nie uzyskuje automatycznie prawa do zmiany Kanonu lub pamięci AgentPRO.

======================================================================
64. SOURCE MASTER
======================================================================

Source Master powstaje tylko po jawnej finalnej akceptacji użytkownika.

To użytkownik podejmuje decyzję, że dana wersja źródłowa jest Masterem do tłumaczeń i publikacji.

======================================================================
65. TŁUMACZENIE TYLKO JAWNE
======================================================================

Tłumaczenie nie startuje automatycznie.

Rozpoczyna się dopiero na polecenie użytkownika i po istnieniu właściwego Source Mastera.

======================================================================
66. NIEZALEŻNE GAŁĘZIE JĘZYKOWE
======================================================================

en-US i en-GB są niezależnymi bezpośrednimi gałęziami od Source Mastera.

Jedna nie powinna być domyślnie tłumaczeniem drugiej.

======================================================================
67. TRANSLATION BIBLE
======================================================================

Każda gałąź tłumaczeniowa może posiadać Translation Bible zawierającą decyzje terminologiczne, nazewnicze, stylistyczne i lokalizacyjne.

======================================================================
68. NATURALNA TRANSLACJA LITERACKA
======================================================================

Tekst docelowy ma brzmieć jak naturalna literatura w języku docelowym, przy zachowaniu Kanonu, intencji, tonu i funkcji narracyjnej Source Mastera.

======================================================================
69. HUMAN EDIT
======================================================================

Ręczna zmiana użytkownika jest pierwszorzędną wersją artefaktu.

Po ręcznej zmianie system ponownie waliduje wymagane zależności: Kanon, pamięć, wiedzę, styl, jakość i inne dotknięte warstwy.

======================================================================
70. PROVENANCE WERSJI
======================================================================

Każda trwała wersja zna swoje pochodzenie: parent, powód zmiany, rolę, model, człowieka, ocenę, Kanon i kontekst tam, gdzie dotyczy.

======================================================================
71. RUN / STEP / ARTIFACT
======================================================================

Podstawowy ślad wykonawczy pozostaje:

RUN
→ STEPS
→ ARTIFACTS.

Każdy z tych poziomów musi być audytowalny.

======================================================================
72. PEŁNY AUDYT
======================================================================

Audyt umożliwia odtworzenie:
- kto/co uruchomiło operację,
- project_id/book_id/run_id/step_id,
- źródeł prawdy,
- wersji Kanonu,
- Book Bible,
- kontekstu,
- modelu i efektywnych parametrów,
- wejścia,
- decyzji,
- wyniku,
- artefaktów,
- zależności i zmian trwałego stanu.

======================================================================
73. RESUME
======================================================================

Resume jest dozwolone tylko w kompatybilnym kontekście tego samego projektu/książki/runu/audytu i zgodnej prawdy projektowej.

Cross-project resume jest zabronione.

======================================================================
74. FORK
======================================================================

Fork/clone jest nową jawnie identyfikowaną linią stanu.

Fork nie może udawać kontynuacji tego samego runu.

======================================================================
75. IDEMPOTENCE
======================================================================

Operacje mogące być ponawiane po przerwaniu muszą być projektowane tak, aby nie tworzyć cichych duplikatów ani podwójnych zmian stanu.

======================================================================
76. ATOMICZNOŚĆ I DURABILITY
======================================================================

Trwały zapis nie może pozostawiać systemu w pozornie zaakceptowanym, ale częściowo zapisanym stanie bez mechanizmu wykrycia i recovery.

======================================================================
77. STORAGE JEST WEWNĘTRZNY
======================================================================

Ścieżki i fizyczny storage są detalem infrastruktury.

Logika domenowa nie może być na stałe związana z rozsianymi bezpośrednimi ścieżkami plikowymi lub SQL.

======================================================================
78. REPOSITORY LAYER
======================================================================

Dostęp do trwałego stanu domenowego jest abstrahowany przez repozytoria związane z właściwym Project/Series Context.

Repository Layer ma umożliwić zmianę fizycznej technologii bez przebudowy reguł domenowych.

======================================================================
79. STORAGE PER PROJECT
======================================================================

Zgodnie z ADR-0001 niezależna książka docelowo posiada własny project.db.

Dane wielu niezależnych książek nie mogą polegać na jednym wspólnym domain DB jako jedynym mechanizmie izolacji.

======================================================================
80. SERIES STORAGE
======================================================================

Seria może posiadać własny series.db dla jawnie współdzielonego Series Canon/Memory.

Dostęp do Series Scope wymaga potwierdzonego członkostwa projektu w serii.

======================================================================
81. SYSTEM STORAGE
======================================================================

AgentPRO może posiadać mały agentpro_system.db dla rejestru aplikacji, konfiguracji i technicznego schedulera.

Nie jest to magazyn literackiej pamięci wszystkich książek.

======================================================================
82. CROSS-STORE CONSISTENCY
======================================================================

project.db + series.db + artefakty plikowe nie są magicznie jedną transakcją SQLite.

Architektura musi posiadać jawny protokół commit/recovery/idempotency dla operacji przekraczających granice storage.

======================================================================
83. MIGRACJE
======================================================================

Zmiany schematu i storage muszą być wersjonowane i migracyjne.

Nie wolno niszczyć istniejących projektów w celu uproszczenia implementacji.

======================================================================
84. SAFE STOP / CRASH RECOVERY
======================================================================

System musi bezpiecznie zatrzymywać i wznawiać pracę po przerwaniu, zachowując audyt i integralność stanu.

======================================================================
85. DERIVED INDEXES
======================================================================

FTS, embeddingi, cache retrievalu, pomocnicze summaries i inne indeksy pochodne nie są źródłem prawdy.

Muszą być możliwe do odbudowania lub unieważnienia.

======================================================================
86. GRAPH
======================================================================

Graf przechowuje zależności pomiędzy encjami i wspiera continuity, retrieval oraz impact analysis.

Pierwsza implementacja nie wymaga osobnego serwera grafowego.

======================================================================
87. IMPACT ANALYSIS
======================================================================

Przed zmianą ważnego elementu chronionego system może wyliczać wpływ na fakty, relacje, sceny, wątki, wiedzę, timeline i artefakty zależne.

======================================================================
88. PROJECT SCOPE
======================================================================

PROJECT scope jest domyślną granicą danych książki.

Repozytorium projektu powinno być związane z jednym ProjectStorageContext, zamiast przyjmować arbitralny project_id przy każdej operacji.

======================================================================
89. SERIES SCOPE
======================================================================

SERIES scope jest jawnie współdzielonym wyjątkiem od pełnej izolacji książek.

Dostęp do danych serii nie może być globalny.

======================================================================
90. SKALOWANIE DO 7 PROJEKTÓW
======================================================================

Architektura docelowo obsługuje do 7 aktywnych projektów książkowych bez przebudowy fundamentów.

Każdy run nadal należy do jednego projektu.

Równoległość nie znosi izolacji.

======================================================================
91. SCHEDULER
======================================================================

Przyszły Scheduler może zarządzać kolejką, priorytetami, limitami providerów, stop/resume i fairness.

Scheduler nie jest właścicielem Kanonu, pamięci ani jakości.

======================================================================
92. TESTY SĄ DOWODEM, NIE PRAWDĄ
======================================================================

Testy są dowodem zachowania określonego kontraktu, ale nie mogą redefiniować Master Canonu.

Przechodzący test nie dowodzi DONE, jeżeli produkcyjna ścieżka używa innego kodu lub omija wymaganie.

======================================================================
93. TEST ISOLATION
======================================================================

Testy nie mogą modyfikować realnego storage użytkownika.

Każdy test wymagający storage pracuje na izolowanym środowisku testowym.

======================================================================
94. EVIDENCE DISCIPLINE
======================================================================

Twierdzenia o aktualnym stanie implementacji oznacza się jako:
[DOWÓD]
[SPECYFIKACJA]
[ZAŁOŻENIE].

Założenie nie może być prezentowane jako stan faktyczny.

======================================================================
95. USER APPROVAL
======================================================================

Użytkownik zachowuje finalne prawo do decyzji twórczych i zatwierdzenia Source Mastera.

System ma automatyzować pracę, ale nie odbiera użytkownikowi kontroli nad zamkniętymi decyzjami.

======================================================================
96. BRAK CICHYCH MUTACJI
======================================================================

Żadna rola, UI ani model nie może cicho zmienić chronionej prawdy projektu.

Każda trwała mutacja musi przejść przez właściwy punkt egzekwowania reguł domenowych.

======================================================================
97. DOMAIN MUTATION GUARD
======================================================================

Ochrona frozen i author_locked jest egzekwowana centralnie przez Domain Mutation Guard lub równoważny pojedynczy mechanizm domenowy.

Nie wolno pozostawić tej reguły do dobrowolnego przestrzegania przez każdą rolę osobno.

======================================================================
98. PRIORYTET INTEGRALNOŚCI
======================================================================

W razie konfliktu priorytety są następujące:

integralność projektu
→ tożsamość projektu
→ Kanon
→ pamięć
→ izolacja
→ recoverability
→ audyt
→ poprawna orkiestracja
→ jakość książki
→ funkcjonalność
→ UI
→ szybkość
→ koszt.

======================================================================
99. JAKOŚĆ LITERACKA I RYNKOWA
======================================================================

Celem końcowym nie jest jedynie technicznie poprawny pipeline.

AgentPRO ma wspierać tworzenie książek o wysokiej wartości literackiej i rynkowej.

======================================================================
100. ARCHITEKTURA NIE JEST KANONEM
======================================================================

Master Canon określa WHAT.

ARCHITEKTURA_AGENTPRO określa HOW.

Architektura nie może zmieniać obowiązujących zasad Master Canonu.

======================================================================
101. ROADMAPA NIE JEST KANONEM
======================================================================

ROADMAPA_AGENTPRO określa NOW / NEXT / LATER / TARGET.

Roadmapa może zmieniać kolejność implementacji bez zmiany tożsamości i twardych reguł systemu.

======================================================================
102. AKTUALNY STAN NIE JEST DOCELOWYM OGRANICZENIEM
======================================================================

To, że obecny kod realizuje tylko część architektury, nie oznacza, że część brakująca przestaje być wymaganiem docelowym.

Jednocześnie element przyszły nie jest automatycznie aktualnym blockerem, jeżeli Roadmapa świadomie umieszcza go później.

======================================================================
103. GUIDE MODE
======================================================================

Guide Mode może istnieć jako odrębny tor produkcyjny.

Jego rozwój jest odłożony do czasu dojrzałości Novel Mode.

Guide Mode nie może mieszać pamięci, Kanonu ani stanu z Novel Mode.

======================================================================
104. ZEWNĘTRZNY IMPORT
======================================================================

Dane zewnętrzne, pliki użytkownika i research są importowane jako jawne źródła z provenance.

Import nie staje się automatycznie kanoniczną prawdą.

======================================================================
105. AUDYTOWALNA DECYZJA AUTORA
======================================================================

Decyzje użytkownika mające znaczenie dla fabuły, Kanonu, stylu, researchu i publikacji mogą być utrwalane jako wersjonowane AuthorDecision.

======================================================================
106. SPRZECZNOŚĆ NIE JEST UKRYWANA
======================================================================

Jeżeli system wykryje sprzeczne źródła lub fakty, rejestruje konflikt i jego status.

Nie wolno losowo wybrać jednej wersji i cicho usunąć drugiej.

======================================================================
107. DEFINICJA PRODUKCYJNEGO DONE
======================================================================

Element jest produkcyjnie DONE dopiero, gdy:
- istnieje w aktywnym P20.x,
- realizuje właściwy kontrakt,
- jest project-aware,
- respektuje Kanon i izolację,
- posiada wymagany audyt,
- posiada idempotencję/recovery tam, gdzie wymagane,
- posiada odpowiednie testy,
- nie zależy od przypadkowej ścieżki legacy.

Sama obecność pliku, klasy, endpointu lub testu nie oznacza DONE.

======================================================================
108. REGUŁA KOŃCOWA
======================================================================

AGENTPRO JEST APLIKACJĄ-AUTOREM, NIE GENERATOREM TREŚCI.

P20.x JEST JEDYNYM SILNIKIEM PRODUKCYJNYM.

LLM NIE JEST PAMIĘCIĄ.

KANON I STRUKTURALNY STAN SĄ ŹRÓDŁEM PRAWDY.

BOOK BIBLE JEST OBOWIĄZKOWY.

NIE WOLNO PISAĆ I ZAPISYWAĆ PRODUKCYJNEGO ROZDZIAŁU BEZ WALIDACJI KANONU.

KAŻDY ROZDZIAŁ MA WERSJONOWANY chapter_XXX.json.

QUALITY = ACCEPT / REVISE / REJECT.

TECHNICZNY RETRY ≠ REEVALUATION.

SOURCE MASTER POWSTAJE TYLKO PO AKCEPTACJI UŻYTKOWNIKA.

TŁUMACZENIE JEST JAWNĄ OPERACJĄ OD SOURCE MASTER.

UI JEST TYLKO WARSTWĄ OPERATORSKĄ.

ZERO PRZECIEKÓW PAMIĘCI MIĘDZY NIEZALEŻNYMI PROJEKTAMI.

PEŁNY RUN / STEP / ARTIFACT / CANON / MEMORY / MODEL AUDIT JEST CZĘŚCIĄ KONTRAKTU.
