# ARCHITEKTURA_AGENTPRO
## SPECYFIKACJA TECHNICZNA SYSTEMU
## WERSJA 1.2 — APPROVED
## ZGODNA Z MASTER_CANON_AGENTPRO v2

======================================================================
0. ROLA DOKUMENTU
======================================================================

MASTER_CANON_AGENTPRO określa:

CO MUSI BYĆ PRAWDĄ.

ARCHITEKTURA_AGENTPRO określa:

JAK TECHNICZNIE AGENTPRO REALIZUJE MASTER CANON.

Hierarchia:

MASTER_CANON_AGENTPRO
→ ARCHITEKTURA_AGENTPRO
→ DECYZJE ARCHITEKTONICZNE / ADR
→ KONTRAKTY DANYCH
→ KONTRAKTY PROCESÓW
→ POLITYKI PROJEKTU
→ ROADMAPA_AGENTPRO
→ IMPLEMENTACJA P20.x
→ TESTY.

Ten dokument:
- nie zmienia Master Canonu,
- nie jest dowodem istniejącej implementacji,
- opisuje architekturę docelową,
- stanowi punkt odniesienia dla audytu istniejącego P20.x,
- może zawierać elementy przewidziane do późniejszej implementacji.

Każde twierdzenie o istniejącym kodzie wymaga osobnego dowodu.


======================================================================
1. GŁÓWNA ARCHITEKTURA
======================================================================

AgentPRO jest lokalną aplikacją-autorską.

Główne warstwy:

APLIKACJA UŻYTKOWNIKA
        ↓
OFICJALNE API
        ↓
WARSTWA APLIKACYJNA
        ↓
P20.x — ORKIESTRATOR PRODUKCYJNY
        ↓
SERWISY DOMENOWE
        ↓
REPOZYTORIA
        ↓
DANE STRUKTURALNE / GRAF / ARTEFAKTY / INDEKSY


Oddzielnie:

P20.x
        ↓
ROLA
        ↓
ROUTER MODELI
        ↓
MODEL / NARZĘDZIE
        ↓
WYNIK
        ↓
WALIDACJA
        ↓
TRWAŁY STAN.


======================================================================
2. OWNER I SILNIK PRODUKCYJNY
======================================================================

Oficjalnym ownerem API pozostaje:

app.main

app.main ma być cienkim adapterem HTTP.

Nie może posiadać alternatywnej implementacji:
- pamięci,
- Kanonu,
- jakości,
- Book Bible,
- storage,
- zespołów,
- presetów,
- workflow.

Jedynym silnikiem produkcyjnym jest:

P20.x.

P0:
- eksperymentalny,
- testowy,
- historyczny.

P0 i P20.x nie mogą być mieszane w jednym runtime produkcyjnym.


======================================================================
3. GRANICA API
======================================================================

UI korzysta z oficjalnego API.

UI nie:
- czyta bezpośrednio bazy,
- edytuje plików systemowych,
- zmienia pamięci,
- zmienia Kanonu,
- podejmuje decyzji jakościowych.

Wewnętrzne komponenty backendu nie komunikują się ze sobą poprzez
requesty HTTP do localhost.

Poprawnie:

UI
→ API
→ Serwis Domenowy
→ Repozytorium.

oraz:

P20.x
→ Serwis Domenowy
→ Repozytorium.


======================================================================
4. PODSTAWOWY STACK
======================================================================

Backend:

Python
FastAPI
P20.x.

Podstawowy trwały magazyn danych lokalnej wersji AgentPRO:

SQLite.

Preferowany tryb:

SQLite + WAL.

SQLite ma przechowywać strukturalną wiedzę projektu oraz historię
stanu wymagającą zapytań, relacji i transakcji.

P20.x nie może wykonywać bezpośrednich zapytań SQL rozsianych po kodzie.

Dostęp następuje przez repozytoria.

Architektura repozytoriów ma umożliwiać późniejszą migrację np. do:

PostgreSQL

bez przebudowy logiki domenowej.

Na pierwszym etapie NIE wprowadzamy osobnego serwera grafowego typu
Neo4j.

Graf zależności realizujemy nad SQLite poprzez jawne rekordy krawędzi.

Indeks semantyczny jest warstwą pomocniczą i może być rozwijany
niezależnie od źródła prawdy.


======================================================================
5. MODEL HYBRYDOWEGO STORAGE
======================================================================

AgentPRO używa czterech klas danych:

1. DANE STRUKTURALNE
2. GRAF ZALEŻNOŚCI
3. ARTEFAKTY
4. INDEKSY POCHODNE.


DANE STRUKTURALNE:

projekty,
serie,
tomy,
akty,
sekwencje,
bohaterowie,
relacje,
fakty,
miejsca,
trasy,
wydarzenia,
wiedza,
wątki,
badania,
źródła,
terminologia,
decyzje autora,
statusy,
wersje,
runy,
kroki,
oceny.


GRAF ZALEŻNOŚCI:

relacje pomiędzy encjami.


ARTEFAKTY:

book_bible.json,
chapter_XXX.json,
wersje tekstu,
manuskrypty,
raporty,
snapshoty,
eksporty,
Mastery,
tłumaczenia.


INDEKSY POCHODNE:

FTS,
embeddingi,
cache retrievalu,
indeksy wyszukiwawcze,
streszczenia pomocnicze.

Indeksy pochodne można odbudować.

Nie są źródłem prawdy.


======================================================================
6. PROJECT SCOPE I SERIES SCOPE
======================================================================

Każdy trwały element pamięci posiada jawny zakres:

PROJECT
lub
SERIES.

Minimalnie:

scope_type
scope_id.

PROJECT:

dane należą wyłącznie do jednej książki.

SERIES:

dane są świadomie współdzielone pomiędzy tomami jednej serii.

Nie istnieje niejawna globalna pamięć wszystkich książek.


======================================================================
7. OBECNIE 1 — DOCELOWO 7
======================================================================

Aktualna implementacja może produkcyjnie prowadzić jedną aktywną
książkę.

Nie jest to trwałe ograniczenie architektury.

Docelowo:

1 AGENTPRO
→ DO 7 AKTYWNYCH PROJEKTÓW.

Każdy run nadal należy dokładnie do jednego projektu.

Projekt posiada własne:

project_id,
book_id,
storage namespace,
Kanon,
Book Bible,
pamięć,
styl,
research,
runy,
artefakty,
locki,
audyt,
tłumaczenia.

Architektura nie może opierać się na jednym globalnym mutable:

active_project

jako trwałym rozwiązaniu docelowym.


======================================================================
8. STABILNE IDENTYFIKATORY
======================================================================

Ważne encje posiadają trwałe ID.

Przykładowe przestrzenie:

PROJ-000001
SERIES-000001
BOOK-000001
ACT-000001
SEQ-000001
CHAPTER-000001
SCENE-000001
CHAR-000001
VOICE-000001
ARC-000001
REL-000001
PLACE-000001
ROUTE-000001
ORG-000001
OBJECT-000001
FACT-000001
EVENT-000001
THREAD-000001
SETUP-000001
SOURCE-000001
RESEARCH-000001
CLAIM-000001
TERM-000001
DECISION-000001
CONFLICT-000001
EVALUATION-000001
CONTEXT-000001.

Nazwa jest właściwością.

ID jest tożsamością.

Zmiana nazwiska bohatera nie może niszczyć istniejących powiązań.


======================================================================
9. WERSJONOWANIE ENCJI
======================================================================

Encje, których historia ma znaczenie, posiadają:

version
created_at
updated_at.

Dane czasowe mogą posiadać:

valid_from
valid_to.

System nie powinien cicho nadpisywać informacji historycznej, jeśli
zmiana wpływa na interpretację wcześniejszych scen.


======================================================================
10. PROJEKT
======================================================================

Logiczny kontrakt:

ProjectRecord

Minimalne pola:

project_id
book_id
series_id | null

title
project_type

source_language
target_market

status

canon_version
book_bible_version
style_profile_version

active_run_id | null

storage_namespace

schema_version

created_at
updated_at.


======================================================================
11. SERIA
======================================================================

SeriesRecord:

series_id
name
description

series_canon_version
status

created_at
updated_at.

Seria może posiadać:
- własny Kanon,
- własną pamięć,
- wspólne postacie,
- fakty długoterminowe,
- miejsca,
- timeline,
- sekrety,
- wątki wielotomowe,
- stan wiedzy postaci,
- stan wiedzy czytelnika.


======================================================================
12. TOM / KSIĄŻKA
======================================================================

BookRecord:

book_id
project_id
series_id | null

volume_number | null
title

source_language
target_market

opening_state_ref | null
closing_state_ref | null

canon_version
style_profile_version

status
created_at
updated_at.


======================================================================
13. STRUKTURA KSIĄŻKI
======================================================================

Struktura jest danymi.

Nie może istnieć wyłącznie jako wielki tekst konspektu.

Hierarchia:

SERIA
→ TOM
→ AKT
→ SEKWENCJA
→ ROZDZIAŁ
→ SCENA.


======================================================================
14. AKT
======================================================================

ActRecord:

act_id
book_id
order

name | null
purpose

opening_state
closing_state

main_conflict
turning_point

target_tension_start
target_tension_end

status
version.


======================================================================
15. SEKWENCJA
======================================================================

SequenceRecord:

sequence_id
act_id
order

purpose
main_goal
main_conflict

entry_state
exit_state

turning_point

target_tension
target_pace

status
version.


======================================================================
16. ROZDZIAŁ — MODEL LOGICZNY
======================================================================

ChapterRecord:

chapter_id
project_id
book_id
sequence_id | null

order
title | null

purpose

entry_state
exit_state

scene_ids[]

status
current_version

canon_status
style_status
quality_status

created_at
updated_at.


======================================================================
17. SCENA
======================================================================

SceneContract:

scene_id
project_id
chapter_id

order
narrative_order

pov_character_id

participant_character_ids[]

location_id | null
route_id | null

time_start
time_end

purpose
goal
obstacle
conflict
stakes
outcome
state_change

facts_required[]
facts_created[]
facts_revealed[]

must_include_facts[]
must_include_events[]
must_include_threads[]
must_include_character_states[]

threads_opened[]
threads_progressed[]
threads_closed[]

setups_created[]
payoffs_completed[]

reader_knowledge_added[]

target_tension
target_pace

status
version.


======================================================================
18. PROFIL POSTACI
======================================================================

Każda ważna postać posiada pełny CharacterProfile.

Nie jest to tylko:

imię + wiek + krótki opis.


IDENTYFIKACJA:

character_id
scope_type
scope_id

first_name
last_name
aliases[]
previous_names[]

status
version.


DANE BIOGRAFICZNE:

birth_date | null
birth_place | null

age nie powinien być utrwalany jako jedyna prawda, jeżeli można go
wyliczyć z timeline.

nationality | null
occupation
education
residence
family_status.


WYGLĄD:

height
body_type
hair
eyes
face
distinguishing_marks[]
scars[]
tattoos[]
clothing_style
movement_style
gestures[]
physical_state.


PSYCHOLOGIA:

temperament
strengths[]
weaknesses[]
fears[]
needs[]
desires[]
obsessions[]
prejudices[]
values[]
moral_boundaries[]
false_beliefs[]
defense_mechanisms[].


REAKCJE:

response_to_stress
response_to_threat
response_to_loss
response_to_love
response_to_conflict
response_to_failure
response_to_success.


HISTORIA:

childhood
family_history
education_history
relationship_history
career_history
traumas[]
successes[]
failures[]
secrets[]
formative_events[].


CELE:

external_goal
internal_goal
motivation
real_need
obstacles[]
self_misunderstanding.


POWIĄZANIA:

relationship_refs[]
organization_refs[]
place_refs[].


WIEDZA:

knowledge_refs[]
belief_refs[]
secret_refs[].


STAN:

current_location
physical_condition
emotional_state
professional_state
relationship_state_refs[]
current_goals[].


KANON:

frozen_traits[]
author_locked_traits[]
canon_status.


======================================================================
19. PROFIL GŁOSU POSTACI
======================================================================

Każda ważna postać może posiadać VoiceProfile.

voice_profile_id
character_id

version
valid_from
valid_to.


SŁOWNICTWO:

vocabulary_complexity
formality
specialist_vocabulary[]
slang_level
regionalisms[]
foreignisms[]
archaic_tendency
preferred_word_types[].


RYTM:

speech_pace
sentence_length_tendency
sentence_variation
pause_tendency
fragment_tendency.


SKŁADNIA:

simple_sentence_tendency
complex_sentence_tendency
ellipses
unfinished_sentences
rhetorical_questions
repetition_tendency.


EMOCJE:

emotional_directness
emotion_suppression
irony
sarcasm
aggression
withdrawal
silence_tendency.


HUMOR:

humor_type
humor_frequency.


PRZEKLEŃSTWA:

profanity_level
profanity_types[]
profanity_triggers[].


NAWYKI:

verbal_habits[]
fillers[]
avoidance_patterns[]
topic_change_patterns[].


ZAKAZY:

forbidden_words[]
forbidden_constructions[]
out_of_character_patterns[].


ZALEŻNOŚĆ OD ROZMÓWCY:

speech_to_partner
speech_to_child
speech_to_superior
speech_to_enemy
speech_to_stranger
speech_in_public.


ZALEŻNOŚĆ OD STANU:

speech_under_stress
speech_when_afraid
speech_when_angry
speech_when_grieving
speech_when_lying
speech_during_interrogation.


Kontroler Stylu nie powinien pilnować jednej powtarzalnej
„charakterystycznej frazy”.

Ma pilnować profilu zachowania językowego.


======================================================================
20. ŁUK POSTACI
======================================================================

CharacterArc:

arc_id
character_id

scope_type
scope_id

opening_state

external_want
internal_need
false_belief
core_conflict

turning_point_refs[]

crisis_ref
transformation

closing_state

status
version.

Punkty przemiany powinny odwoływać się do konkretnych wydarzeń lub
scen.


======================================================================
21. RELACJE
======================================================================

RelationshipRecord:

relationship_id

character_a_id
character_b_id

relationship_type
state

valid_from
valid_to

source_event_id | null
source_scene_id | null

canon_status
version.

System może odtworzyć stan relacji w dowolnej scenie.

Przykład:

Tom 1:
NIEZNAJOMI.

Tom 2:
SOJUSZNICY.

Tom 3:
PARTNERZY.

Tom 5:
WROGOWIE.


======================================================================
22. FAKT
======================================================================

FactRecord:

fact_id

scope_type
scope_id

subject_id

predicate

object_type
object_id | null
object_value | null

reality_status
verification_status

confidence

valid_from
valid_to

established_event_id | null
established_scene_id | null

source_refs[]

frozen
author_locked

canon_version
version

created_at
updated_at.


======================================================================
23. STATUS RZECZYWISTOŚCI
======================================================================

reality_status:

PROJECT_CANON

REAL_VERIFIED

REAL_UNVERIFIED

FICTIONAL

FICTIONAL_OVERLAY_ON_REAL_WORLD.


======================================================================
24. STATUS WERYFIKACJI
======================================================================

verification_status:

CONFIRMED

UNCERTAIN

DISPUTED

REVIEW_REQUIRED

INTENTIONALLY_FICTIONAL.

Status i confidence są różnymi pojęciami.


======================================================================
25. ZAMROŻONY FAKT
======================================================================

frozen = true

oznacza:

fakt nie może zostać cicho zmieniony przez model.

NIE oznacza:

fakt musi znajdować się w każdym promptcie.

Context Builder kwalifikuje zamrożony fakt jako obowiązkowy w danym
zadaniu, jeżeli:

- bezpośrednio dotyczy sceny,
- może zostać przez scenę naruszony,
- jest zależnością wymaganej encji,
- został jawnie oznaczony jako must_include,
- polityka danej roli wymaga jego obecności.

Zamrożony fakt niezwiązany z zadaniem pozostaje chroniony w pamięci,
ale nie musi zużywać kontekstu modelu.


======================================================================
26. BLOKADA AUTORA
======================================================================

author_locked = true

oznacza:

NIE ZMIENIAJ BEZ ZGODY UŻYTKOWNIKA.

Blokada może dotyczyć m.in.:

faktu,
postaci,
cechy postaci,
relacji,
miejsca,
trasy,
sceny,
rozdziału,
elementu stylu,
wątku,
decyzji fabularnej.

Jest to blokada domenowa.

Nie należy jej mylić z lockiem technicznym runu.


======================================================================
27. DECYZJA AUTORA
======================================================================

AuthorDecision:

decision_id

scope_type
scope_id

decision_type

subject_type
subject_id

decision

reason | null

impact_refs[]

status

created_at
created_by.

Przykład:

DECISION-000417

subject:
PLACE-000084 / drzewo przy zakręcie.

decision:
pozostawić świadomie jako element fikcyjny.

Po zatwierdzeniu Weryfikator Faktów nie powinien wielokrotnie zgłaszać
tego samego elementu jako błędu.


======================================================================
28. REJESTR SPRZECZNOŚCI
======================================================================

ConflictRecord:

conflict_id

scope_type
scope_id

entity_type
entity_id

statement_a
source_refs_a[]

statement_b
source_refs_b[]

status

resolution | null
resolved_by | null
resolved_at | null.


Status:

OPEN
RESEARCH_REQUIRED
AUTHOR_DECISION_REQUIRED
RESOLVED.

Sprzeczność istotna dla bieżącej sceny nie jest tylko faktem o niskim
score.

Context Builder ma przekazać ją jako:

OSTRZEŻENIE O SPRZECZNOŚCI.

Model nie może dostać jednej wersji jako pewnej, jeśli projekt nie
rozstrzygnął konfliktu.


======================================================================
29. MIEJSCE
======================================================================

PlaceRecord:

place_id

scope_type
scope_id

name
aliases[]

place_type

country
region | null
city | null
address | null

latitude | null
longitude | null

reality_status
verification_status

description

fiction_overlay[]

source_refs[]

canon_status
version.


======================================================================
30. RELACJA PRZESTRZENNA
======================================================================

SpatialRelation:

spatial_relation_id

source_place_id
target_place_id

distance | null
direction | null
travel_mode | null
expected_duration | null

verified
source_refs[]

version.


======================================================================
31. TRASA
======================================================================

RouteRecord:

route_id
project_id

start_place_id
end_place_id

waypoints[]

distance
travel_mode
expected_duration

verified
source_refs[]

fiction_overlay[]

version.


Przykład:

START
→ 50 m
→ SKRĘT W PRAWO
→ punkt B.


======================================================================
32. WYDARZENIE I TIMELINE
======================================================================

EventRecord:

event_id

scope_type
scope_id

event_type

time_start
time_end

narrative_order

location_id | null
participant_ids[]

description

cause_refs[]
effect_refs[]

source_scene_id | null

canon_status
version.

Chronologia świata i kolejność narracyjna są różnymi pojęciami.


======================================================================
33. PRZYCZYNOWOŚĆ
======================================================================

AgentPRO przechowuje jawne zależności:

EVENT-A
→ CAUSES
→ EVENT-B.

EVENT-B
→ MOTIVATES
→ DECISION-C.

DECISION-C
→ CAUSES
→ EVENT-D.

Pozwala to wykrywać:

deus ex machina,
wydarzenie bez przyczyny,
nieuzasadnioną decyzję,
brak konsekwencji,
sprzeczny ciąg zdarzeń.


======================================================================
34. GRAF ZALEŻNOŚCI
======================================================================

Pierwsza implementacja:

SQLite
+
tabela edges.

EdgeRecord:

edge_id

scope_type
scope_id

source_type
source_id

relation_type

target_type
target_id

valid_from
valid_to

confidence

source_ref | null
version.


Przykładowe relation_type:

KNOWS
BELIEVES
RELATED_TO
LOCATED_AT
PRESENT_IN
REVEALS
REQUIRES
USES
OPENS
PROGRESSES
CLOSES
SETS_UP
PAYS_OFF
CAUSES
MOTIVATES
CONTRADICTS
DEPENDS_ON
PART_OF.


======================================================================
35. ANALIZA WPŁYWU
======================================================================

Zmiana ważnej encji uruchamia Impact Analysis.

Przykład:

zmiana FACT-00127.

System wyszukuje:

- sceny używające faktu,
- rozdziały zawierające sceny,
- postacie posiadające tę wiedzę,
- fałszywe przekonania związane z faktem,
- wydarzenia zależne,
- relacje zależne,
- wątki zależne,
- setups/payoffs,
- streszczenia,
- oceny QUALITY,
- indeksy,
- późniejsze tomy,
- tłumaczenia,
- Mastery zależne.

Wynik zmiany nie może być „naprawiony” przez zgadywanie modelu.


======================================================================
36. WIEDZA POSTACI
======================================================================

KnowledgeEvent:

knowledge_event_id
character_id
fact_id

knowledge_type

learned_at_scene_id
learned_at_event_id | null

learned_from_character_id | null
source_ref | null

valid_from
valid_to

confidence.


knowledge_type:

KNOWS

BELIEVES

SUSPECTS

DENIES

MISREMEMBERS.


======================================================================
37. FAŁSZYWE PRZEKONANIA
======================================================================

Prawda projektu i wiedza postaci są oddzielne.

Przykład:

FACT:
John żyje.

KnowledgeEvent:
Lena BELIEVES John nie żyje.

Pisarz piszący scenę z perspektywy Leny musi dostać:

jej stan wiedzy,

a nie automatycznie obiektywną prawdę świata jako wiedzę postaci.


======================================================================
38. WIEDZA CZYTELNIKA
======================================================================

ReaderKnowledgeEvent:

reader_knowledge_id
fact_id

revealed_at_scene_id

knowledge_level

valid_from
valid_to.


Możliwe stany:

REVEALED

HINTED

MISDIRECTED

HIDDEN.

Wiedza czytelnika może przechodzić między tomami serii.


======================================================================
39. WĄTEK
======================================================================

ThreadRecord:

thread_id

scope_type
scope_id

name
description

importance

opened_scene_id
closed_scene_id | null

status

payoff_required

target_payoff | null
actual_payoff_ref | null

deliberately_left_reason | null

version.


status:

OPEN

DEVELOPING

PAID_OFF

DELIBERATELY_LEFT.


DELIBERATELY_LEFT wymaga świadomego uzasadnienia lub decyzji autora.


======================================================================
40. ZAPOWIEDŹ / SPŁATA
======================================================================

SetupPayoffRecord:

setup_id

scope_type
scope_id

created_scene_id

description
importance

expected_payoff

target_range

actual_payoff_scene_id | null

status
version.


System powinien wykrywać ważne zapowiedzi pozbawione spłaty.


======================================================================
41. PRZEJŚCIE WĄTKÓW MIĘDZY TOMAMI
======================================================================

Po zamknięciu tomu aktywne wątki przechodzą kontrolę:

WĄTEK TOMU
→ ZAMKNIĘTY

albo:

WĄTEK TOMU
→ PRZENIESIONY DO SERIES SCOPE.

Następny tom może pobierać takie wątki z Pamięci Serii.


======================================================================
42. SNAPSHOT KOŃCOWY TOMU
======================================================================

Po zatwierdzeniu tomu system tworzy VolumeClosingSnapshot.

Snapshot obejmuje co najmniej:

- żyjące/martwe postacie,
- miejsce pobytu ważnych postaci,
- stan fizyczny,
- stan emocjonalny,
- relacje,
- wiedzę postaci,
- wiedzę czytelnika,
- sekrety,
- otwarte wątki,
- zapowiedzi wymagające spłaty,
- stan organizacji,
- ważne przedmioty,
- trwałe fakty,
- trwałe konsekwencje.

Następny tom nie zaczyna od pustej pamięci.


======================================================================
43. PAMIĘĆ HIERARCHICZNA
======================================================================

Poziomy:

SERIES MEMORY
→ BOOK MEMORY
→ CHAPTER MEMORY
→ SCENE MEMORY.

Pamięć Serii nie jest kopią całej serii.

Przechowuje trwałą wiedzę potrzebną między tomami.

Pamięć Tomu przechowuje lokalny stan bieżącej książki.

Pamięć Rozdziału i Sceny wspiera lokalny kontekst.


======================================================================
44. BOOK BIBLE
======================================================================

Każdy projekt powieściowy posiada:

book_bible.json.

Book Bible jest obowiązkowym wewnętrznym artefaktem kontraktowym.

Nie ma być gigantyczną dynamiczną bazą całej pamięci.

Przechowuje przede wszystkim:

- tożsamość książki,
- podstawowe założenia,
- fundamentalne reguły,
- główne postacie,
- główne konflikty,
- fundamentalne fakty,
- nadrzędną strukturę,
- związki z serią,
- odniesienie do Profilu Stylu,
- wersję kontraktu.

Dynamiczna wiedza trafia do strukturalnej pamięci.


======================================================================
44A. STRUCTURED MEMORY EXTRACTION INTEGRITY
======================================================================

Ekstrakcja pamięci ze sceny jest osobnym procesem integralnościowym.

Obowiązkowy przepływ:

SCENE SOURCE
→ STRUCTURED EXTRACTION CANDIDATE
→ VERIFICATION AGAINST SOURCE SCENE
→ ACCEPT / REVISE / REJECT
→ COMMIT TO STRUCTURED MEMORY.

Ekstraktor i weryfikator ekstrakcji są logicznie odrębnymi rolami
lub odrębnymi wywołaniami. Ten sam wynik LLM nie może sam siebie
zatwierdzić jako pamięć kanoniczną.

Weryfikacja ekstrakcji sprawdza osobno:

PRECISION:

- kandydat nie zawiera faktów,
- zdarzeń,
- stanu postaci,
- zmian relacji,
- wiedzy,
- zapowiedzi,
- spłat,
- ani innych danych, które nie wynikają ze źródłowej sceny.

COMPLETENESS:

- kandydat nie pomija istotnych faktów,
- zdarzeń,
- zmian stanu,
- zmian wiedzy,
- zmian relacji,
- otwarcia lub zamknięcia wątku,
- setupu,
- payoffu,
- ani innych istotnych konsekwencji źródłowej sceny.

Status ekstrakcji pamięci jest niezależny od statusu jakości tekstu:

memory_extraction_status
!=
scene_quality_status.

ACCEPT tekstu nie oznacza automatycznie ACCEPT ekstrakcji pamięci.

REJECT ekstrakcji pamięci nie oznacza automatycznie REJECT tekstu
sceny.

Ekstrakcja pamięci posiada własną pętlę:

ACCEPT
REVISE
REJECT.

Pętla ma limit prób. Po przekroczeniu limitu system nie zgaduje
brakujących danych, tylko eskaluje decyzję do użytkownika.

Mechanizm obejmuje co najmniej:

- FactRecord,
- KnowledgeEvent,
- CharacterState,
- Relationship changes,
- EventRecord,
- Thread,
- Setup,
- Payoff.

Każdy kandydat pamięci posiada provenance wskazujące dokładną scenę
lub artifact source, z którego pochodzi.

Dane wygenerowane przez ekstraktor LLM nie mogą automatycznie wejść
do kanonicznej Structured Memory.

Zestaw powiązanych encji wynikających z jednej sceny jest zatwierdzany
logicznie jako całość. Nie wolno zatwierdzić tylko wygodnej części
kandydata, jeżeli pozostała część jest wymagana do zachowania
spójności sceny.

Commit danych pamięci w obrębie project.db jest atomowy dla
zaakceptowanego zestawu encji sceny:

ACCEPTED MEMORY ENTITY SET
→ ATOMIC PROJECT.DB COMMIT.

Nie wolno udawać jednej transakcji ACID obejmującej jednocześnie:

- project.db,
- series.db,
- artefakty plikowe.

Propagacja zaakceptowanej pamięci poza project.db podlega późniejszemu
commit/recovery/outbox protocol zgodnemu z ADR-0001.


======================================================================
45. PROFIL STYLU KSIĄŻKI
======================================================================

StyleProfile:

style_profile_id
project_id

version

valid_from_chapter | null
valid_from_scene | null

status

narrator
pov_policy
narrative_tense
narrative_distance

rhythm
pace

sentence_length_target
sentence_length_variance

description_density
metaphor_density
emotionality
introspection
exposition_density
dialogue_density
humor_level
violence_level

forbidden_patterns[]

functional_profile_refs[]

author_reference_refs[].


======================================================================
46. STYLE FUNKCJONALNE
======================================================================

Możliwe FunctionalStyleProfile:

NARRACJA

DIALOGI

BOHATEROWIE

PRZYRODA

MIASTA

MIEJSCA

WNĘTRZA

AKCJA

NAPIĘCIE

EMOCJE

INTROSPEKCJA

RETROSPEKCJA

ŚLEDZTWO

EKSPOZYCJA TECHNICZNA.


Każdy profil funkcjonalny dziedziczy nadrzędny styl książki.


======================================================================
47. REFERENCJE AUTORSKIE
======================================================================

Użytkownik może podać autora lub utwór jako referencję warsztatową.

System nie zapisuje wyłącznie:

"PISZ JAK X".

Referencja jest przekładana na abstrakcyjne cechy, np.:

rhythm
sentence_length
imagery
detail_density
metaphor_density
dialogue_density
narrative_distance
exposition
pace
introspection.

Pisarz otrzymuje cechy stylu.

Nie instrukcję kopiowania konkretnego autora.


======================================================================
48. WERSJONOWANIE STYLU
======================================================================

Jeżeli styl zmienia się od Rozdziału 30:

Rozdziały 1–29:
StyleProfile v1.

Rozdział 30+:
StyleProfile v2.

System zachowuje tę informację.

Globalna redakcja może później harmonizować wcześniejszy tekst do
nowszego profilu.


======================================================================
49. BADANIA
======================================================================

ResearchRecord:

research_id
project_id

question
purpose

requested_by

related_entity_refs[]

status

created_at
completed_at | null.


======================================================================
50. ŹRÓDŁO
======================================================================

ResearchSource:

source_id
project_id

source_type

title
author | null
publisher | null

url_or_reference | null

publication_date | null
accessed_at | null

reliability

content_hash | null

notes.


source_type może obejmować:

WEB
PDF
BOOK
MAP
REPORT
DOCUMENT
USER_NOTE
OTHER.


======================================================================
51. TWIERDZENIE BADAWCZE
======================================================================

ResearchClaim:

claim_id
research_id

claim

source_refs[]

confidence

verification_status

related_fact_id | null

created_at.


Badacz powinien produkować konkretne twierdzenia, nie tylko wielką
niestrukturalną notatkę.


======================================================================
52. BADACZ
======================================================================

Badacz:

- wyszukuje informacje,
- zbiera źródła,
- porównuje źródła,
- identyfikuje konflikty,
- tworzy ResearchClaim.

Badacz nie:
- zatwierdza własnego wyniku jako Kanonu,
- nie zmienia sam Book Bible,
- nie zmienia zamrożonych faktów.


======================================================================
53. WERYFIKATOR FAKTÓW
======================================================================

Weryfikator:

- sprawdza źródła,
- porównuje claims,
- ocenia sprzeczności,
- nadaje verification_status,
- ocenia confidence,
- może skierować temat do dalszych badań,
- może skierować temat do decyzji autora.

Research nie staje się automatycznie Kanonem.


======================================================================
54. IMPORT BADAŃ UŻYTKOWNIKA
======================================================================

Użytkownik może wprowadzić:

PDF,
notatkę,
mapę,
dokument,
raport,
książkę źródłową,
inne materiały.

Proces:

IMPORT
→ SOURCE RECORD
→ EKSTRAKCJA CLAIMS
→ WERYFIKACJA
→ ewentualna decyzja.

Nie:

WKLEJ DO PROMPTU
→ MOŻE MODEL ZAPAMIĘTA.


======================================================================
55. REJESTR TERMINÓW
======================================================================

TermRecord:

term_id
project_id

term
category

technical_definition
reader_definition

first_use_scene_id | null
first_use_chapter_id | null

presentation_policy

source_refs[]

status
version.


presentation_policy:

INLINE

FOOTNOTE

ENDNOTE

GLOSSARY

NONE.


======================================================================
56. TERMINOLOGIA A SKŁAD
======================================================================

AgentPRO zapisuje:

CO MA SIĘ STAĆ.

Nie zapisuje na poziomie autorskim:

"umieść to fizycznie na stronie 127".

Przykład:

TERM-0017
→ FOOTNOTE.

Warstwa eksportu może później utworzyć właściwy przypis w konkretnym
formacie wydawniczym.

Ta sama semantyczna decyzja może zostać inaczej wyrenderowana w
różnych wydaniach.


======================================================================
57. BUDOWNICZY PAKIETU KONTEKSTU
======================================================================

LLM nie jest pamięcią książki.

Przed każdym istotnym wywołaniem modelu AgentPRO tworzy:

ContextPackage.

Pisarz, Krytyk ani inna rola nie wybierają swobodnie całej pamięci.

Context Builder wykonuje kontrolowany retrieval.


======================================================================
58. CONTEXT PACKAGE
======================================================================

ContextPackage:

context_package_id

project_id
book_id
series_id | null

run_id
step_id

role
mode

context_policy_id
context_policy_version

effective_model
tokenizer_id
tokenizer_version | null

context_window
reserved_output_tokens
reserved_system_tokens
available_context_tokens

canon_version
book_bible_version
style_version

memory_snapshot_id | null
graph_version | null

included_items[]

selection_summary

total_tokens

context_hash

created_at.


======================================================================
59. CONTEXT ITEM
======================================================================

ContextItem:

entity_type
entity_id

layer

mandatory

reason

score | null

score_breakdown | null

representation_type

token_count

source_version

content_hash.


representation_type może oznaczać np.:

FULL
STRUCTURED
COMPRESSED
SUMMARY
WARNING.


======================================================================
60. PROFILE KONTEKSTU DLA RÓL
======================================================================

Nie istnieje jeden identyczny Context Package dla wszystkich ról.

Każda rola posiada własny ContextProfile.


PISARZ potrzebuje przede wszystkim:

- kontraktu sceny,
- Kanonu dotyczącego sceny,
- postaci,
- ich wiedzy i przekonań,
- miejsca i czasu,
- aktywnych wątków,
- potrzebnych setups/payoffs,
- stylu,
- głosów postaci,
- kontekstu narracyjnego.


KRYTYK potrzebuje m.in.:

- tekstu ocenianego,
- celu sceny/rozdziału,
- kontraktu sceny,
- Reader Promise,
- wymaganej struktury,
- kryteriów jakości,
- wybranych zależności fabularnych.

Nie musi otrzymywać identycznego promptu i informacji jak Pisarz.


STRAŻNIK CIĄGŁOŚCI potrzebuje więcej:

- timeline,
- historii stanów,
- wiedzy postaci,
- relacji,
- miejsc,
- tras,
- zależności.


KONTROLER KANONU potrzebuje:

- obowiązującego Kanonu,
- Book Bible,
- zamrożonych faktów relewantnych,
- decyzji autora,
- zmian proponowanych przez tekst.


BADACZ potrzebuje:

- pytania badawczego,
- zakresu projektu,
- istniejących claims,
- źródeł,
- sprzeczności.

Nie potrzebuje pełnego Profilu Stylu książki.


WERYFIKATOR FAKTÓW potrzebuje:

- claims,
- źródeł,
- dowodów,
- real-world status,
- istniejących konfliktów,
- decyzji autora.


KONTROLER STYLU potrzebuje:

- tekstu,
- StyleProfile,
- właściwego FunctionalStyleProfile,
- VoiceProfile postaci,
- zakazów stylistycznych.


TŁUMACZ potrzebuje:

- Source Master fragment,
- sąsiedni kontekst semantyczny,
- Translation Bible,
- profil stylu,
- VoiceProfile postaci,
- Kanon potrzebny do uniknięcia zmiany sensu.


KONTROLER TŁUMACZENIA posiada własny profil zależny od rodzaju
kontroli.


======================================================================
61. CONTEXT POLICY
======================================================================

Wartości budżetowe i wagi retrievalu nie są wpisane na stałe do
Master Canonu.

Są wersjonowaną polityką:

ContextPolicy.

ContextPolicy może różnić się dla:

NOVEL_WRITE

NOVEL_CONTINUITY

NOVEL_CRITIC

FACTCHECK

RESEARCH

GUIDE_WRITE

TRANSLATION

TRANSLATION_QA

itd.


======================================================================
62. OBLICZANIE BUDŻETU KONTEKSTU
======================================================================

Logiczna formuła:

AVAILABLE =
CONTEXT_WINDOW
-
RESERVED_OUTPUT
-
SYSTEM_AND_ROLE_INSTRUCTIONS
-
TECHNICAL_OVERHEAD.

Context Builder nie może przyjmować:

"wrzuć wszystko, aż się skończy miejsce".

Przed retrievalem zna maksymalny budżet.


======================================================================
63. TWARDY PRIORYTET KONTEKSTU
======================================================================

Dla przykładowego NOVEL_WRITE kolejność logiczna jest następująca:

1. instrukcje systemowe i kontrakt roli,
2. cel zadania,
3. SceneContract,
4. obowiązkowy Kanon dotyczący sceny,
5. relewantne zamrożone / zablokowane fakty,
6. jawne must_include,
7. ostrzeżenia o sprzecznościach,
8. główne postacie sceny,
9. ich wiedza i przekonania,
10. miejsce / czas / trasa,
11. bezpośrednie zależności fabularne,
12. aktywne wątki,
13. wymagane setups/payoffs,
14. kontekst narracyjnie poprzedzający,
15. StyleProfile,
16. VoiceProfile,
17. relewantne wydarzenia,
18. dodatkowa pamięć,
19. treść semantycznie podobna.

Niższa warstwa nie może wyrzucić obowiązkowej wyższej warstwy.


======================================================================
64. MUST_INCLUDE
======================================================================

SceneContract może jawnie wymagać:

must_include_facts[]
must_include_events[]
must_include_threads[]
must_include_character_states[].

Element taki nie podlega zwykłemu rankingowi.

Przykład:

SCENE-0680
musi uwzględnić:
OBJECT/FACT związany z przedmiotem wprowadzonym w SCENE-0411.

To chroni długoterminowe payoffy przed przypadkowym wypadnięciem z
retrievalu.


======================================================================
65. GŁÓWNE I DRUGORZĘDNE ENCJE SCENY
======================================================================

Nie obowiązuje twarda reguła typu:

maksymalnie 6 postaci.

Postacie są klasyfikowane dla konkretnej sceny.

Przykład:

PRIMARY
SECONDARY
BACKGROUND.

PRIMARY:
pełny potrzebny profil.

SECONDARY:
profil ograniczony do relewantnych informacji.

BACKGROUND:
minimalna reprezentacja.

To samo dotyczy miejsc i innych encji.


======================================================================
66. KONTEKST NARRACYJNIE POPRZEDZAJĄCY
======================================================================

Context Builder nie zakłada automatycznie:

"pełny tekst poprzedniej numerem sceny".

Wyznacza:

NARRATIVELY_RELEVANT_PREVIOUS_CONTEXT.

Może to być:

- scena bezpośrednio poprzednia,
- ostatnia scena tego samego POV,
- ostatnia scena dotycząca tego wątku,
- scena przyczynowo prowadząca do bieżącej,
- połączenie krótkich reprezentacji kilku scen.

Pełny tekst poprzedniej sceny jest używany, gdy rzeczywiście jest
potrzebny.


======================================================================
67. RETRIEVAL STRUKTURALNY NAJPIERW
======================================================================

Kolejność:

1. structured query,
2. graf,
3. timeline,
4. wiedza postaci,
5. relacje,
6. wątki,
7. miejsca/trasy,
8. dopiero później retrieval semantyczny.

Embedding nie określa prawdy.


======================================================================
68. RANKING HYBRYDOWY
======================================================================

Dla elementów nieobowiązkowych można stosować ranking:

score =
w_graph * graph_proximity
+
w_task * task_relevance
+
w_recency * narrative_recency
+
w_confidence * confidence
+
w_semantic * semantic_similarity
+
w_importance * story_importance.


Wagi są częścią:

ContextPolicy.

Nie są globalną stałą całego AgentPRO.


======================================================================
69. GRAPH PROXIMITY
======================================================================

graph_proximity wykorzystuje zależności pomiędzy:

sceną,
postaciami,
faktami,
wątkami,
wydarzeniami,
miejscami,
relacjami.

Pierwsza implementacja może wykonywać:

1–2 hop

nad tabelą edges.

Szczegóły SQL mogą zostać zapisane w ADR / kontrakcie implementacyjnym.


======================================================================
70. RECENCY
======================================================================

Recency nie może być liczona na podstawie ID encji.

SCENE-00100 nie musi być matematycznie wcześniejsza tylko dlatego,
że ma mniejszy identyfikator.

Recency korzysta z:

narrative_order,
timeline,
distance_in_narrative_sequence

lub innego jawnego pola porządku.


======================================================================
71. STARY FAKT NIE STAJE SIĘ NIEWAŻNY
======================================================================

Recency nie może sprowadzać starego faktu automatycznie do zera.

Fakt ustanowiony 300 000 słów wcześniej nadal może mieć wysoki score,
jeżeli:

- jest bezpośrednio grafowo związany z bieżącą sceną,
- jest potrzebny do payoffu,
- dotyczy obecnej postaci,
- jest must_include,
- ma wysokie story_importance.


======================================================================
72. SPRZECZNOŚCI W CONTEXT BUILDERZE
======================================================================

DISPUTED nie jest zwykłym "słabszym faktem".

Jeżeli konflikt jest relewantny:

Context Package zawiera:

WARNING:
PROJECT DATA CONTAINS UNRESOLVED CONFLICT.

Model otrzymuje:
- obie wersje,
- status,
- zakaz traktowania jednej jako potwierdzonej,
- ewentualne AuthorDecision, jeśli konflikt już rozstrzygnięto.


======================================================================
73. OVERFLOW
======================================================================

Gdy obowiązkowy kontekst przekracza budżet, AgentPRO nie obcina go
cicho.

Kolejność reakcji:

1. usuń nieobowiązkowe dane,
2. zamień pełne reprezentacje na strukturalne,
3. zastosuj lossless semantic compression tam, gdzie możliwe,
4. zmniejsz mniej istotne profile encji,
5. podziel zadanie,
6. wykonaj wcześniejszy etap analityczny,
7. użyj większego dostępnego modelu, jeśli polityka na to pozwala,
8. dopiero w sytuacji nierozwiązywalnej:
   ESKALACJA DO UŻYTKOWNIKA.

Użytkownik nie powinien być niepotrzebnie angażowany w normalne
zarządzanie kontekstem.


======================================================================
74. CONTEXT TRACE
======================================================================

Każdy Context Package posiada audyt wyboru.

Dla elementów włączonych:

entity_id
entity_type
layer
reason
score | mandatory
representation
tokens.

Nie należy bez potrzeby zapisywać milionów pełnych rekordów wszystkich
kandydatów odrzuconych przez ranking.

Dla odrzuconych zapisujemy:

- liczbę kandydatów,
- liczbę wybranych,
- próg końcowy,
- top-N odrzuconych,
- przyczynę odrzucenia,
- statystyki rankingu.

Może istnieć tryb diagnostyczny zapisujący pełny trace.


======================================================================
75. CONTEXT PACKAGE I RETRY
======================================================================

Techniczny retry tego samego kroku nie buduje automatycznie nowego
Context Package.

Używa:

context_package_id

z pierwotnej operacji.

Daje to odtwarzalność:

ten sam input,
ten sam Kanon,
ten sam Context Package,
ta sama wersja polityki.

Świadome uruchomienie nowej próby generacji może utworzyć nowy
Context Package i musi zostać odnotowane jako nowa operacja.


======================================================================
76. CONTEXT HASH
======================================================================

Context Package posiada deterministyczny:

context_hash.

Hash obejmuje co najmniej:

- identyfikatory elementów,
- ich wersje,
- reprezentacje przekazane modelowi,
- wersję ContextPolicy,
- Kanon,
- Book Bible,
- Styl.

Pozwala stwierdzić, czy dwa wywołania rzeczywiście dostały ten sam
kontekst.


======================================================================
77. DANE POCHODNE
======================================================================

Dane takie jak:

streszczenia,
embeddingi,
FTS,
cache,
ranking retrievalu,
agregaty napięcia

są danymi pochodnymi.

Muszą wskazywać swoje źródło i jego wersję/hash.


======================================================================
78. UNIEWAŻNIANIE DANYCH
======================================================================

Jeżeli źródło zmienia się w sposób wpływający na pochodne dane,
otrzymują one status:

STALE / NIEAKTUALNE.

Przykład:

zmieniono Chapter 12.

Analiza może unieważnić:

- jego summary,
- summary Aktu,
- wybrane Facts,
- Context cache,
- ocenę QUALITY,
- embedding,
- późniejszy rozdział zależny,
- tłumaczenie,
- Master lokalizowany.

System nie może cicho używać starej pochodnej jako aktualnej.


======================================================================
79. RĘCZNA ZMIANA AUTORA
======================================================================

Human Edit jest pełnoprawną operacją.

Proces:

EDYCJA UŻYTKOWNIKA
→ NOWA WERSJA
→ IMPACT ANALYSIS
→ INVALIDATION
→ CANON CHECK
→ CONTINUITY CHECK, JEŚLI POTRZEBNY
→ MEMORY UPDATE
→ DALSZE WYMAGANE KONTROLE.

Zmiana użytkownika nie omija integralności systemu.


======================================================================
80. READER PROMISE
======================================================================

Project Profile posiada ReaderPromise.

Może obejmować:

genre
target_reader
core_experience

expected_tension
expected_realism
expected_emotionality

core_value

market_expectation

promises[]
forbidden_failures[].

Kontrola książki sprawdza, czy obietnica została spełniona.


======================================================================
81. NAPIĘCIE
======================================================================

Scena może posiadać:

target_tension
actual_tension.

Rozdział, sekwencja i akt posiadają agregaty.

AgentPRO może porównywać:

KRZYWA PLANOWANA
vs
KRZYWA RZECZYWISTA.

Pozwala wykrywać:

- martwe odcinki,
- zbyt wczesny szczyt,
- monotonię,
- niewystarczający finał.


======================================================================
82. TEMPO
======================================================================

Analogicznie:

target_pace
actual_pace.

Tempo może oznaczać szybkość:
- zmian fabularnych,
- konfliktów,
- informacji,
- ekspozycji,
- ruchu sceny,
- emocji.

Nie jest równoważne wyłącznie liczbie scen akcji.


======================================================================
83. KONTROLA ZBĘDNOŚCI
======================================================================

System powinien wykrywać:

- scenę bez funkcji,
- scenę bez state_change,
- drugi raz tę samą funkcję narracyjną,
- powtarzaną ekspozycję,
- dialog bez konsekwencji,
- akapit bez wartości,
- sztuczne wydłużanie.

Usunięcie tekstu nie jest celem samym w sobie.

Celem jest gęstość wartości.


======================================================================
84. KONTROLA POWTÓRZEŃ
======================================================================

AgentPRO może analizować:

frazy,
metafory,
schematy scen,
argumenty,
przykłady,
strukturę rozdziałów,
powtarzalne zachowania językowe.

Porównanie z innymi własnymi projektami użytkownika wymaga specjalnie
dozwolonej operacji analizującej wiele projektów.

Nie może powodować włączenia treści Projektu A do normalnego Context
Package Pisarza Projektu B.


======================================================================
85. DOMYŚLNY WORKFLOW ROZDZIAŁU
======================================================================

Dla Novel Mode domyślny proces:

LOAD PROJECT
→ VALIDATE PROJECT TRUTH
→ VALIDATE BOOK BIBLE
→ BUILD CONTEXT PACKAGE
→ CANON PRE-CHECK
→ PLAN
→ WRITE
→ CRITIC
→ REWRITE
→ EDIT
→ CONTINUITY
→ CANON POST-CHECK
→ STYLE CHECK
→ QUALITY
→ AKCEPTUJ / POPRAW / ODRZUĆ.


Preset może sterować sekwencją.

Nie może usuwać obowiązkowych zabezpieczeń trwałej akceptacji.


======================================================================
86. KRYTYK
======================================================================

Krytyk ocenia m.in.:

purpose,
conflict,
stakes,
tension,
pacing,
character behavior,
dialogue,
emotional credibility,
exposition,
predictability,
redundancy,
causality,
scene consequence.

Krytyk nie modyfikuje Kanonu.


======================================================================
87. NIEZALEŻNOŚĆ OCENY
======================================================================

Pisarz i Krytyk są oddzielnymi wywołaniami logicznymi.

Po poważnym Rewrite wynik podlega nowej niezależnej kontroli.

Proces wykonawczy nie jest jedynym sędzią swojej własnej poprawki.


======================================================================
88. BRAMKA JAKOŚCI
======================================================================

Wewnętrzne decyzje pozostają:

ACCEPT
REVISE
REJECT.

UI wyświetla:

AKCEPTUJ
POPRAW
ODRZUĆ.

Execution status jest oddzielny.

Przykład:

execution_status = SUCCESS
quality_decision = REJECT

jest poprawnym rezultatem.


======================================================================
89. EVALUATION RECORD
======================================================================

EvaluationRecord:

evaluation_id

project_id
artifact_id

artifact_hash

criteria_version
prompt_version

context_package_id
context_hash

requested_model
effective_model
provider
model_version | null

decision

reasons[]
must_fix[]

created_at.


======================================================================
90. CACHE WERDYKTU JAKOŚCI
======================================================================

Evaluation Cache Key obejmuje co najmniej:

artifact_hash
criteria_version
prompt_version
effective_model identity
context_hash.

Jeżeli dokładnie ta sama ocena została już prawidłowo ukończona,
techniczny retry odczytuje jej wynik.

Nie wywołuje LLM-as-judge drugi raz bez powodu.


======================================================================
91. PONÓW TECHNICZNIE VS OCEŃ PONOWNIE
======================================================================

PONÓW TECHNICZNIE:

to kontynuacja tej samej operacji.

Używa:
- tego samego wejścia,
- tego samego ContextPackage,
- istniejącego Evaluation, jeśli już powstało.


OCEŃ PONOWNIE:

to nowa świadoma operacja.

Tworzy:
- nowe evaluation_id,
- nowy wpis audytu,
- opcjonalnie nowy ContextPackage, jeśli zmienił się stan.


======================================================================
92. AUTONOMICZNA PĘTLA POPRAWY
======================================================================

REVISE
→ DIAGNOZA
→ ROUTING DO WŁAŚCIWEJ ROLI
→ NOWA WERSJA
→ NOWA OCENA.

Problem:

język
→ Redaktor.

styl
→ Kontroler Stylu / Pisarz.

fakt
→ Badacz / Weryfikator.

ciągłość
→ Strażnik Ciągłości.

konstrukcja
→ Planista / Pisarz.

System nie wykonuje całego pipeline od początku, jeśli problem wymaga
jednej lokalnej naprawy.


======================================================================
93. LIMIT PĘTLI
======================================================================

Retry/revision limit jest polityką projektu.

Nie jest stałą Master Canonu.

Po osiągnięciu limitu:

ESCALATE_TO_USER.

AgentPRO nie może obniżyć jakości tylko dlatego, że wykorzystał limit.


======================================================================
94. JAKOŚĆ SCENY, ROZDZIAŁU I KSIĄŻKI
======================================================================

Istnieją co najmniej trzy poziomy:

SCENE QA

CHAPTER QA

BOOK QA.

ACCEPT każdego rozdziału nie oznacza automatycznie:

BOOK ACCEPT.


======================================================================
95. KONTROLA CAŁEJ KSIĄŻKI
======================================================================

Po zakończeniu wszystkich rozdziałów:

MANUSKRYPT
→ STRUKTURA
→ CAUSALITY
→ PACING
→ TENSION
→ CHARACTER ARCS
→ CONTINUITY
→ CANON
→ KNOWLEDGE STATES
→ READER KNOWLEDGE
→ THREADS
→ SETUPS / PAYOFFS
→ STYLE
→ CHARACTER VOICES
→ REDUNDANCY
→ REPETITION
→ RESEARCH / FACTS
→ TERMINOLOGY
→ LANGUAGE
→ FINAL QUALITY.


======================================================================
96. ANALIZA MANUSKRYPTU 200K+
======================================================================

Nie zakładamy, że pełne 200 000 słów musi wejść do jednego promptu.

Analiza jest hierarchiczna:

SCENA
→ ROZDZIAŁ
→ SEKWENCJA
→ AKT
→ KSIĄŻKA.

Jeżeli analiza wysokiego poziomu wykrywa problem, system wraca do
źródłowych scen/rozdziałów potrzebnych do potwierdzenia.


======================================================================
97. ARTEFAKT ROZDZIAŁU
======================================================================

Każdy rozdział posiada wewnętrzny:

chapter_XXX.json.

Minimalny kontrakt artefaktu:

schema_version

chapter_id
project_id
book_id

version
parent_version | null

status

text_ref lub text

canon_version
book_bible_version
style_version

context_package_id
context_hash

created_by_role

requested_model
effective_model

quality_decision

artifact_hash

created_at.

Artefakt jest wersjonowany.

Nie jest cicho nadpisywany.


======================================================================
98. LINEAGE ROZDZIAŁU
======================================================================

Historia może wyglądać:

PLAN
→ WRITE v1
→ CRITIC
→ REWRITE v2
→ EDIT v3
→ CONTINUITY
→ QUALITY
→ ACCEPT.

Każda wersja tekstu zna:

parent_version
reason
source_evaluation
model
role
Kanon
Styl.


======================================================================
99. WERSJE MANUSKRYPTU
======================================================================

ManuscriptVersion:

manuscript_id
project_id
version

chapter_version_refs[]

parent_manuscript_id | null

status

artifact_hash

created_at.

Przykład:

M1
M2
M3.

Możliwy jest rollback do poprzedniej pełnej wersji.


======================================================================
100. TRWAŁA AKCEPTACJA ROZDZIAŁU
======================================================================

Akceptacja rozdziału jest logiczną transakcją.

Obejmuje co najmniej:

1. zapis wersji rozdziału,
2. aktualizację Facts,
3. aktualizację Character Knowledge,
4. aktualizację Reader Knowledge,
5. aktualizację Timeline,
6. aktualizację Character State,
7. aktualizację Relationships,
8. aktualizację Threads,
9. aktualizację Setup/Payoff,
10. aktualizację pochodnych summaries,
11. zapis audytu,
12. aktualizację run_state.

Operacja ma zakończyć się:

CAŁOŚĆ ZATWIERDZONA

albo:

POWRÓT DO POPRZEDNIEGO SPÓJNEGO STANU.


======================================================================
101. ATOMOWOŚĆ
======================================================================

Nie wolno pozostawić stanu:

tekst zapisany
ale pamięć nie,

albo:

pamięć zmieniona
ale rozdział nie,

albo:

run_state wskazuje artefakt,
który nie istnieje.

Dla operacji obejmujących bazę i pliki potrzebny jest kontrolowany
protokół zapisu oraz recovery.


======================================================================
102. IDEMPOTENCY KEY
======================================================================

Krytyczne operacje posiadają idempotency_key.

Logicznie może obejmować:

project_id
run_id
step_id
operation_type
input_artifact_hash.

Techniczny retry nie tworzy drugiego:

rozdziału,
Fact,
Memory Event,
Audit Event,
Evaluation.


======================================================================
103. RUN
======================================================================

RunRecord:

run_id

project_id
book_id

preset_id

status

project_truth_hash
canon_version

started_at
finished_at | null

resume_parent | null.


Run zawsze należy do dokładnie jednego projektu.


======================================================================
104. STEP
======================================================================

StepRecord:

step_id
run_id

mode
role

status

input_refs[]
output_refs[]

context_package_id | null

requested_model
effective_model

policy_refs[]

decision | null

started_at
finished_at | null.


======================================================================
105. MODE / ROLE / PRESET
======================================================================

MODE:

CO ROBIMY.

ROLE:

KTO TO WYKONUJE.

PRESET:

JAKA JEST SEKWENCJA.

MODEL:

JAKI MODEL ZOSTAŁ WYBRANY DO WYKONANIA ROLI.

Te pojęcia pozostają rozdzielone.


======================================================================
106. ROLE
======================================================================

Docelowe role mogą obejmować:

PLANISTA
PISARZ
KRYTYK
REDAKTOR
STRAŻNIK CIĄGŁOŚCI
KONTROLER KANONU
BADACZ
WERYFIKATOR FAKTÓW
KONTROLER STYLU
KONTROLER JAKOŚCI
TŁUMACZ
KONTROLER TŁUMACZENIA
KONTROLER NATURALNOŚCI TŁUMACZENIA.

Rola nie oznacza koniecznie osobnego modelu.


======================================================================
107. ROUTER MODELI
======================================================================

ModelRouter wybiera model na podstawie wymagań zadania.

TaskModelProfile może określać:

required_quality
reasoning_requirement
context_requirement
tool_requirement
language_requirement

latency_preference
cost_policy

fallback_policy.


Nie kodujemy:

PISARZ = konkretny model na zawsze.

Kodujemy:

PISARZ
→ wymagania
→ ROUTER
→ aktualnie właściwy model.


======================================================================
108. REQUESTED MODEL I EFFECTIVE MODEL
======================================================================

System rozróżnia:

requested_model
effective_model.

Audyt zapisuje model faktycznie użyty.

Nie wolno raportować modelu, który nie wykonał wywołania.

Fallback modelu nie może być cichy, jeśli polityka wymaga jawności.


======================================================================
109. AUDYT
======================================================================

Audyt musi umożliwić odtworzenie:

PROJECT
→ RUN
→ STEP
→ INPUTS
→ CONTEXT PACKAGE
→ ROLE
→ REQUESTED MODEL
→ EFFECTIVE MODEL
→ OUTPUT
→ EVALUATION
→ MEMORY DELTA
→ ARTIFACTS
→ DECISION.

Audyt nie polega wyłącznie na luźnym pliku log.txt.


======================================================================
110. PROJECT TRUTH I RESUME
======================================================================

Resume sprawdza co najmniej:

project_id
book_id
run_id
project_truth
canon_version
run_state.

Nie wolno:

- przejąć runu innej książki,
- przejąć memory innego projektu,
- podstawić ostatniego globalnego runu,
- wznowić niezgodnego projektu.


======================================================================
111. FORK / KLON
======================================================================

Fork nie jest Resume.

Fork tworzy:

nowy project_id.

Zachowuje jawne provenance:

source_project_id
source_version.

Nowy projekt posiada własne:

pamięć,
runy,
locki,
audyt,
artefakty.


======================================================================
112. LOCKI
======================================================================

Locki techniczne są zakresowe.

Co najmniej:

PROJECT LOCK
BOOK LOCK
RUN LOCK.

Docelowe 7 projektów nie może być blokowane jednym niepotrzebnym
globalnym lockiem.

Konflikt locka blokuje niebezpieczny trwały zapis.


======================================================================
113. BEZPIECZNE ZATRZYMANIE
======================================================================

Użytkownik może wybrać:

ZATRZYMAJ PO BEZPIECZNYM KROKU.

System ustawia:

STOP_REQUESTED.

Po zakończeniu aktualnego atomowego kroku:

PAUSED.

Resume może później kontynuować zgodnie z tym samym projektem i
audytem.


======================================================================
114. ODPORNOŚĆ NA AWARIE
======================================================================

System musi odzyskiwać stan po:

- zamknięciu aplikacji,
- crashu,
- timeout,
- błędzie modelu,
- utracie sieci,
- przerwaniu systemu,
- częściowym błędzie kroku.

Recovery używa ostatniego potwierdzonego bezpiecznego stanu.

Nie rekonstruuje prawdy z przypadkowego ostatniego pliku.


======================================================================
115. MASTER ŹRÓDŁOWY
======================================================================

Po Book QA powstaje:

CANDIDATE_MASTER.

Tylko użytkownik może zatwierdzić:

CANDIDATE_MASTER
→ SOURCE_MASTER.


SourceMaster:

source_master_id
project_id

manuscript_id
manuscript_version

artifact_hash

approved_by_user
approved_at

status.


Automatyczny workflow nie może wykonać finalnej akceptacji za
użytkownika.


======================================================================
116. ZMIANA MASTER ŹRÓDŁOWEGO
======================================================================

Zmiana po akceptacji tworzy nową wersję Source Master.

Stary Source Master pozostaje w historii.

Wszystkie wersje zależne wskazują source_master_id/hash.

Jeżeli ich źródło staje się stare:

status:

STALE_AGAINST_SOURCE.


======================================================================
117. ZEWNĘTRZNY KRYTYK
======================================================================

Zewnętrzny krytyk nie jest komponentem AgentPRO.

AgentPRO:

- nie zna jego nazwy,
- nie wymaga go,
- nie posiada dedykowanej zależności,
- nie posiada obowiązkowego hooka,
- nie posiada obowiązkowego endpointu.

AgentPRO potrafi wyeksportować manuskrypt.

Użytkownik może później zaimportować dowolne uwagi jako własny materiał
wejściowy.


======================================================================
118. START TŁUMACZENIA
======================================================================

Tłumaczenie wymaga:

SOURCE_MASTER
+
jawnej decyzji użytkownika.

Nie uruchamia się automatycznie.


======================================================================
119. NIEZALEŻNE LOCALE
======================================================================

Poprawnie:

SOURCE_MASTER
→ en-US.

SOURCE_MASTER
→ en-GB.

Nie:

SOURCE_MASTER
→ en-US
→ en-GB.

Każde locale ma niezależną historię.


======================================================================
120. BIBLIA TŁUMACZENIA
======================================================================

TranslationBible:

translation_bible_id

source_master_id
locale

version

approved_names[]
approved_terms[]
organizations[]
titles[]
technologies[]
units[]
idiom_rules[]
character_voice_rules[]
localization_decisions[]
do_not_translate[]
notes[].


======================================================================
121. MAPPING TŁUMACZENIA
======================================================================

Tłumaczenie nie musi zachowywać relacji:

1 zdanie źródłowe = 1 zdanie docelowe.

Mapowanie powinno używać stabilniejszych jednostek:

paragraph_id
scene_id
semantic_segment_id.

Tłumacz może naturalnie przebudować zdania bez utraty kontroli
pokrycia źródła.


======================================================================
122. WORKFLOW TŁUMACZENIA
======================================================================

SOURCE MASTER
→ TRANSLATION
→ FIDELITY CHECK
→ COMPLETENESS CHECK
→ CANON CHECK
→ TERMINOLOGY CHECK
→ LOCALE CHECK
→ LANGUAGE EDIT
→ STYLE CHECK
→ NATIVE NATURALNESS CHECK
→ TRANSLATION QUALITY
→ KANDYDAT LOCALE.


Decyzje:

AKCEPTUJ
POPRAW
ODRZUĆ.


======================================================================
123. NIEZALEŻNA NATURALNOŚĆ TŁUMACZENIA
======================================================================

Kontroler naturalności może otrzymać wyłącznie tekst docelowy.

Nie musi widzieć źródła.

Ocenia:

- naturalność,
- idiomatyczność,
- dialog,
- rytm,
- składnię,
- translationese,
- jakość literacką.

Dla en-US ocenia tekst jako współczesną naturalną prozę amerykańską.

Dla en-GB jako naturalną prozę brytyjską.


======================================================================
124. MASTER WERSJI JĘZYKOWEJ
======================================================================

Po tłumaczeniowym QA powstaje:

CANDIDATE en-US

lub:

CANDIDATE en-GB.

Finalny:

MASTER en-US

MASTER en-GB

powstaje dopiero po jawnej akceptacji użytkownika.


======================================================================
125. UI — JĘZYK
======================================================================

UI obsługuje:

POLSKI
ENGLISH.

Warstwa prezentacji korzysta z kluczy językowych.

Zmiana UI locale nie zmienia:

- projektu,
- pamięci,
- Kanonu,
- P20.x,
- identyfikatorów,
- treści książki.


======================================================================
126. UI — GŁÓWNE OBSZARY
======================================================================

Docelowe obszary aplikacji:

PULPIT

PROJEKTY

SERIE

KSIĄŻKI

STRUKTURA

BOHATEROWIE

RELACJE

MIEJSCA

MAPA / TRASY

OŚ CZASU

WĄTKI

KANON

PAMIĘĆ

BADANIA

ŹRÓDŁA

TERMINY I OBJAŚNIENIA

STYL

ROZDZIAŁY

SCENY

KRYTYKA

REDAKCJA

KONTROLA JAKOŚCI

HISTORIA

TŁUMACZENIA

PRZYGOTOWANIE WYDAWNICZE

EKSPORT.


======================================================================
127. UI — BOHATER
======================================================================

Ekran bohatera pokazuje m.in.:

DANE

WYGLĄD

PSYCHOLOGIA

HISTORIA

CELE

LĘKI

SEKRETY

RELACJE

WIEDZA

PRZEKONANIA

ŁUK POSTACI

STAN BIEŻĄCY

PROFIL GŁOSU

SCENY

HISTORIA ZMIAN

ZAMROŻONE ELEMENTY

BLOKADY AUTORA.


======================================================================
128. UI — PROFIL GŁOSU
======================================================================

Użytkownik powinien móc ustawić bez pisania promptów:

Słownictwo:
potoczne.

Zdania:
krótkie.

Humor:
suchy.

Ironia:
wysoka.

Przekleństwa:
rzadkie.

Do szefa:
formalnie.

Do partnerki:
bardziej otwarcie.

Pod presją:
krótsze zdania, mniej wyjaśnień.

Zakaz:
długie monologi.


======================================================================
129. UI — STYL KSIĄŻKI
======================================================================

Ekran Styl:

STYL GŁÓWNY

NARRACJA

DIALOGI

BOHATEROWIE

PRZYRODA

MIASTA

MIEJSCA

WNĘTRZA

AKCJA

NAPIĘCIE

EMOCJE

INTROSPEKCJA

EKSPOZYCJA TECHNICZNA

REFERENCJE AUTORSKIE

GŁOSY POSTACI

REGUŁY ZABRONIONE

HISTORIA WERSJI.


======================================================================
130. UI — BADANIA
======================================================================

Proces operatorski:

NOWE PYTANIE BADAWCZE
→ BADANIE
→ ŹRÓDŁA
→ CLAIMS
→ WERYFIKACJA
→ SPRZECZNOŚCI
→ DECYZJA
→ ewentualne użycie w projekcie.


======================================================================
131. UI — MIEJSCA
======================================================================

Ekran miejsca może pokazywać:

- dane miejsca,
- status realne/fikcyjne,
- źródła,
- mapę,
- współrzędne,
- połączenia,
- trasy,
- odległości,
- sceny,
- fikcyjne nakładki,
- decyzje autora.


======================================================================
132. STANY PROJEKTU
======================================================================

Docelowa maszyna stanu może używać m.in.:

CREATING

PREPARATION

READY_TO_WRITE

WRITING

BOOK_QA

CANDIDATE_MASTER

USER_REVIEW

SOURCE_MASTER_LOCKED

TRANSLATION_REQUESTED

TRANSLATION

LOCALIZATION_QA

PUBLICATION_PREPARATION

PUBLICATION_READY

CLOSED.

Dokładne przejścia stanu wymagają osobnego kontraktu procesu.


======================================================================
133. PRZYGOTOWANIE WYDAWNICZE
======================================================================

Po zaakceptowanym Masterze system może przygotować:

- finalny manuskrypt eksportowy,
- przypisy,
- przypisy końcowe,
- słowniczek,
- opis książki,
- słowa kluczowe,
- metadane,
- dane wydawnicze,
- brief okładki,
- materiały publikacyjne.

Może osiągnąć:

GOTOWY DO PUBLIKACJI.

Publikacja sama wymaga decyzji użytkownika.


======================================================================
134. BACKUP PROJEKTU
======================================================================

Backup projektu powinien obejmować:

- dane strukturalne,
- Kanon,
- Book Bible,
- pamięć,
- graf,
- bohaterów,
- miejsca,
- research,
- styl,
- rozdziały,
- manuskrypty,
- audyt krytyczny,
- decyzje autora,
- Mastery,
- tłumaczenia.


======================================================================
135. BACKUP SERII
======================================================================

Backup serii obejmuje:

- Series Canon,
- Series Memory,
- wszystkie tomy,
- powiązania między tomami,
- snapshoty,
- wspólne postacie,
- wspólne miejsca,
- wątki wielotomowe,
- wiedzę postaci,
- wiedzę czytelnika,
- historię serii.


======================================================================
136. RESTORE
======================================================================

Restore musi odtworzyć:

project_id
series_id

Kanon
Book Bible
dane strukturalne
pamięć
graf
artefakty
wersje
audyt
run state
Mastery.

Indeksy pochodne mogą zostać odbudowane.


======================================================================
137. MIGRACJE
======================================================================

Trwałe schematy są wersjonowane.

Zmiana wymaga:

BACKUP
→ MIGRATION
→ VALIDATION
→ TEST
→ SCHEMA VERSION UPDATE.

Nie wolno wykonywać destrukcyjnej cichej migracji podczas zwykłego
odczytu projektu.


======================================================================
138. TEST STORAGE
======================================================================

Testy nie używają realnego storage użytkownika.

Obowiązuje oddzielna przestrzeń testowa dla:

- bazy,
- books,
- runs,
- pamięci,
- artefaktów,
- indeksów.


======================================================================
139. TESTY IZOLACJI
======================================================================

Docelowe testy muszą potwierdzić m.in.:

Projekt A nie widzi Facts B.

Projekt A nie widzi CharacterProfile B.

Projekt A nie widzi Research B.

Projekt A nie widzi StyleProfile B.

Projekt A nie widzi tłumaczeń B.

Projekt A nie wznowi Run B.

Projekt A nie zapisze artefaktu do B.

Context Builder A nie pobierze elementu z B.


======================================================================
140. TESTY SERII
======================================================================

Testy serii muszą potwierdzić:

Tom 2 może korzystać z dozwolonego Series Canon.

Tom 2 otrzymuje snapshot Tomu 1.

Stan relacji przechodzi poprawnie.

Wiedza postaci przechodzi poprawnie.

Wiedza czytelnika przechodzi poprawnie.

Wątki wielotomowe przechodzą poprawnie.

Niezależna książka C nie widzi danych serii.


======================================================================
141. TESTY CONTEXT BUILDERA
======================================================================

Minimalne klasy testów:

- obowiązkowy fakt zawsze wchodzi,
- nieistotny frozen fact nie musi wejść,
- must_include zawsze wchodzi,
- sprzeczność dostaje WARNING,
- projekt A nie pobiera danych B,
- Series Scope działa tylko dla członka serii,
- starszy ważny fakt może pokonać nowszy nieistotny,
- overflow nie ucina krytycznego Kanonu,
- techniczny retry używa tego samego ContextPackage,
- zmiana ContextPolicy zmienia context_hash,
- zmiana wersji faktu zmienia context_hash,
- context trace wyjaśnia wybór.


======================================================================
142. TESTY QUALITY CACHE
======================================================================

Należy potwierdzić:

ten sam artifact_hash
+
te same criteria
+
ten sam prompt
+
ten sam model
+
ten sam context_hash

→ techniczny retry nie generuje nowej oceny.

Świadome:

OCEŃ PONOWNIE

→ tworzy nowe evaluation_id.


======================================================================
143. HARMONOGRAM 7 PROJEKTÓW
======================================================================

Pełna implementacja równoległości może nastąpić później.

Architektura już teraz wymaga zakresowych danych.

Docelowy Scheduler odpowiada za:

- kolejkę,
- priorytety,
- limity równoległych wywołań,
- limity providerów,
- zatrzymanie,
- wznowienie,
- fairness pomiędzy projektami.

Scheduler nie jest właścicielem:
- Kanonu,
- pamięci,
- jakości.

Każde zadanie schedulera nadal posiada project_id.


======================================================================
144. KONTROLA DRYFU PAMIĘCI
======================================================================

AgentPRO musi umożliwiać kontrolę:

STRUCTURED MEMORY
vs
APPROVED CHAPTERS
vs
CANON
vs
TIMELINE
vs
GRAPH.

Rozbieżność:

nie jest automatycznie naprawiana przez zgadywanie.

System ustala źródło prawdy i wykonuje kontrolowaną naprawę.


======================================================================
145. DEFINITION OF DONE DLA KOMPONENTU ARCHITEKTURY
======================================================================

Element architektury jest produkcyjnie wdrożony dopiero, gdy:

- istnieje w P20.x,
- realizuje właściwy kontrakt,
- jest project-aware,
- respektuje Canon,
- respektuje izolację,
- posiada wymagany audyt,
- posiada idempotencję tam, gdzie wymagana,
- posiada testy,
- przechodzi regresję,
- nie opiera się na przypadkowej ścieżce legacy.

Samo utworzenie tabeli, pliku, endpointu lub klasy nie oznacza DONE.


======================================================================
146. ZASADA: ARCHITEKTURA NIE JEST ROADMAPĄ
======================================================================

To, że element znajduje się w tym dokumencie, nie oznacza:

"budujemy go natychmiast".

ROADMAPA_AGENTPRO ustali:

TERAZ
→ NASTĘPNIE
→ PÓŹNIEJ
→ DOCELOWO.

Dzięki temu nie próbujemy budować jednocześnie całego systemu.

Ale element budowany TERAZ nie może zablokować elementów wymaganych
DOCELOWO.


======================================================================
147. ZASADA KOŃCOWA
======================================================================

LLM NIE PAMIĘTA SAGI.

AGENTPRO PAMIĘTA SAGĘ.

LLM NIE JEST ŹRÓDŁEM PRAWDY.

KANON I STRUKTURALNY STAN PROJEKTU SĄ ŹRÓDŁEM PRAWDY.

BOOK BIBLE JEST OBOWIĄZKOWYM KONTRAKTEM, ALE NIE JEST JEDYNĄ BAZĄ
PAMIĘCI.

BAZA PRZECHOWUJE STRUKTURALNĄ WIEDZĘ.

GRAF PRZECHOWUJE ZALEŻNOŚCI.

ARTEFAKTY PRZECHOWUJĄ TEKST I HISTORIĘ TEKSTU.

INDEKS SEMANTYCZNY POMAGA ODNALEŹĆ MATERIAŁ, ALE NIE DECYDUJE O
PRAWDZIE.

CONTEXT BUILDER DECYDUJE, JAKĄ CZĘŚĆ PAMIĘCI DOSTAJE MODEL W DANYM
KROKU.

CONTEXT BUILDER NIE MOŻE CICHYM OBCIĘCIEM KRYTYCZNYCH DANYCH
PRZENOSIĆ PROBLEMU PAMIĘCI NA LLM.

ZAMROŻONY FAKT OZNACZA "NIE NARUSZAJ", A NIE "WKLEJAJ DO KAŻDEGO
PROMPTU".

PISARZ OTRZYMUJE INFORMACJE WŁAŚCIWE DLA PISANIA.

KRYTYK OTRZYMUJE INFORMACJE WŁAŚCIWE DLA KRYTYKI.

STRAŻNIK CIĄGŁOŚCI OTRZYMUJE INFORMACJE WŁAŚCIWE DLA CIĄGŁOŚCI.

BADACZ OTRZYMUJE INFORMACJE WŁAŚCIWE DLA BADAŃ.

KAŻDA ROLA MA WŁASNY PROFIL KONTEKSTU.

POSTAĆ POSIADA PEŁNY PROFIL PSYCHOLOGICZNY, BIOGRAFICZNY,
FABULARNY I STANOWY.

WAŻNA POSTAĆ POSIADA ODDZIELNY, WERSJONOWANY PROFIL GŁOSU.

AGENTPRO MUSI WIEDZIEĆ NIE TYLKO KIM JEST POSTAĆ, ALE RÓWNIEŻ:

JAK MÓWI,
DO KOGO MÓWI,
JAK MÓWI POD PRESJĄ,
CO WIE,
W CO WIERZY,
CZEGO NIE WIE,
JAK SIĘ ZMIENIA.

MIEJSCE JEST ENCJĄ.

TRASA JEST ENCJĄ.

FAKT RZECZYWISTY I FIKCYJNA NAKŁADKA SĄ ROZRÓŻNIANE.

DECYZJA AUTORA JEST TRWAŁYM ELEMENTEM SYSTEMU.

SPRZECZNOŚĆ NIE JEST UKRYWANA.

WPŁYW ZMIANY JEST ANALIZOWANY.

DANE POCHODNE SĄ UNIEWAŻNIANE, JEŻELI ICH ŹRÓDŁO SIĘ ZMIENIŁO.

TECHNICZNY RETRY NIE JEST NOWĄ PRÓBĄ TWÓRCZĄ ANI NOWĄ OCENĄ.

QUALITY JEST ODDZIELONE OD EXECUTION STATUS.

SOURCE MASTER POWSTAJE TYLKO PO AKCEPTACJI UŻYTKOWNIKA.

TŁUMACZENIE STARTUJE TYLKO NA POLECENIE UŻYTKOWNIKA.

en-US I en-GB SĄ NIEZALEŻNYMI GAŁĘZIAMI OD SOURCE MASTER.

ZEWNĘTRZNY KRYTYK NIE JEST CZĘŚCIĄ AGENTPRO.

UI JEST WARSTWĄ OPERATORSKĄ.

BACKEND MUSI DZIAŁAĆ BEZ UI.

P20.x JEST JEDYNYM SILNIKIEM PRODUKCYJNYM.

ARCHITEKTURA MA DZIAŁAĆ POPRAWNIE DLA JEDNEJ KSIĄŻKI TERAZ I
POZWOLIĆ PÓŹNIEJ BEZ PRZEBUDOWY FUNDAMENTÓW NA:

- POWIEŚCI 100K–200K+,
- SAGI 700K+,
- WIELE TOMÓW,
- PEŁNĄ PAMIĘĆ POSTACI I ŚWIATA,
- REAL-WORLD RESEARCH,
- ZAAWANSOWANĄ KONTROLĘ CIĄGŁOŚCI,
- STYLE FUNKCJONALNE,
- INDYWIDUALNE GŁOSY POSTACI,
- NIEZALEŻNE TŁUMACZENIA,
- DO 7 RÓWNOLEGŁYCH PROJEKTÓW.

ARCHITEKTURA MA SŁUŻYĆ JAKO KONKRETNY KONTRAKT TECHNICZNY DO AUDYTU
I DALSZEJ BUDOWY P20.x.
