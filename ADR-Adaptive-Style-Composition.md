# ADR: Adaptive Style Composition Layer

**Status:** ACCEPTED
**Data akceptacji:** 2026-09-14
**Dotyczy:** AgentPRO v1.2 — rozszerzenie `StyleProfile`
**Nie wprowadza:** nowego runtime'u/engine'u. To warstwa kontraktów nad istniejącą architekturą.

---

## 1. Kontekst

Architektura v1.2 definiuje `StyleProfile` jako wersjonowany obiekt z dziedziczeniem na poziomie książki i osobnym kontekstem dla Kontrolera Stylu. Nie definiuje natomiast, **jak** styl sceny jest wyprowadzany z DNA książki w sposób adaptacyjny — tj. z uwzględnieniem typu sceny, historii skuteczności i biblioteki technik źródłowych.

Kanoniczny łańcuch pozostaje:

```
Style Library → Book Style DNA → Style Composer → Scene Style Recipe → Writer → Style Critic → Style Memory/Performance
```

Ten ADR zamyka lukę pomiędzy `Book Style DNA` a `Writer`, definiując dokładnie sześć kontraktów danych (`StyleGenome` jest kontraktem, nie samodzielną encją — patrz 2.2) i jeden proces (Composer), oraz ich granice względem `StyleProfile`.

---

## 2. Decyzja

Wprowadzamy sześć kontraktów danych. Żaden z nich nie zastępuje `StyleProfile` — wszystkie są względem niego podrzędne lub komplementarne.

### 2.1 `StyleLibraryProfile`
Statyczna, przeanalizowana reprezentacja źródła stylistycznego (autor referencyjny, korpus, technika).

```
StyleLibraryProfile {
  id
  source_ref            // abstrakcja referencji autorskiej, zgodnie z v1.2
  extracted_techniques[]  // np. free indirect discourse, short-burst dialogue
  genome: StyleGenome    // może być sparse; patrz 2.2
  applicability_tags[]   // scene_type / genre / tone, do czego się nadaje
  version
}
```
Wejście do Composera. Nigdy nie jest modyfikowany w runtime — tylko przez proces kuracji biblioteki (offline). W profilu bibliotecznym brak wartości cechy oznacza wyłącznie `UNKNOWN / NOT OBSERVED`; nie jest zastępowany zerem, wartością neutralną, średnią, DNA ani inferencją. Techniki i applicability pozostają użyteczne niezależnie od kompletności części liczbowej.

### 2.2 `StyleGenome`
Parametryczny wektor cech stylu — wspólny format dla `StyleLibraryProfile`, `BookStyleDNA` i `SceneStyleRecipe`.

```
StyleGenome {
  pacing: float           // 0–1
  dialogue_density: float
  suspense: float
  syntax_variation: float
  sentence_length_profile: {mean, variance}
  lexical_register: enum
  figurative_density: float
  pov_intimacy: float
  ... (rozszerzalne, ale zamknięty enum kluczy per wersja)
}
```
Kluczowe: `StyleGenome` to **format**, nie encja sama w sobie. Zawsze występuje jako pole wewnątrz innego kontraktu.

`StyleLibraryProfile.genome` może być częściowy (sparse), ponieważ opisuje wyłącznie udokumentowane obserwacje źródłowe. Composer rozpatruje każdą cechę osobno i uwzględnia dla niej tylko profile, które mają tę konkretną wartość. Brak sygnału bibliotecznego nie jest błędem i nie mutuje profilu; wynik jest wtedy wyprowadzany z `BookStyleDNA` oraz prawidłowo dopasowanej historii wykonania. Artefakty wykonawcze — w szczególności `SceneStyleRecipe.target_genome`, `StyleEvaluation.achieved_genome` i `StylePerformanceRecord.achieved_genome` — wymagają kompletnego genomu. Provenance recepty wskazuje wybrane profile, źródła technik oraz profile dostarczające sygnału dla każdej użytej cechy.

### 2.3 `BookStyleDNA`
Nadrzędny, stabilny zakres dopuszczalnego stylu książki. Nie jest pojedynczym punktem w przestrzeni genomu — jest **przedziałem dopuszczalnym** per cecha.

```
BookStyleDNA {
  book_id
  version
  genome_bounds: { [feature]: {min, max, target} }
  locked_identity_markers[]   // cechy, których Composer NIE MOŻE naruszyć
  derived_from: StyleProfile.version   // powiązanie z v1.2
}
```
**Granica:** `BookStyleDNA` jest wyprowadzane z `StyleProfile` (v1.2) w momencie inicjalizacji książki i wersjonowane równolegle. `StyleProfile` pozostaje źródłem prawdy o tożsamości autorskiej/formalnej; `BookStyleDNA` to jego projekcja na przestrzeń genomu, konsumowana przez Composer.

### 2.4 `SceneStyleRecipe`
Wynik pracy Composera dla konkretnej sceny — punkt (nie przedział) w genomie, wybrany w granicach `BookStyleDNA`.

```
SceneStyleRecipe {
  scene_id
  book_style_dna_version
  target_genome: StyleGenome        // punkt, mieści się w genome_bounds
  selected_techniques[]             // z StyleLibraryProfile
  rationale                         // krótkie uzasadnienie wyboru (audytowalność)
  scene_index_key: SceneIndexKey    // patrz 2.6
}
```

### 2.5 `StyleEvaluation`
Pomiar efektu po napisaniu sceny, produkowany przez Style Critic. Ocenia **wyłącznie zgodność stylu** — nie jest werdyktem o całym artefakcie. Ten drugi werdykt należy do Quality Gate (osobny, niezależny mechanizm — `KONTROLER JAKOŚCI` w v1.2), który operuje na własnym kontrakcie `QualityEvaluation` (poza zakresem tego ADR) i zwraca `quality_decision: ACCEPT | REVISE | REJECT`.

```
StyleEvaluation {
  style_evaluation_id
  scene_id
  recipe_id

  artifact_id
  artifact_hash          // wersja konkretnego wygenerowanego tekstu

  achieved_genome: StyleGenome      // faktyczny styl wygenerowanego tekstu
  deviation_from_target: float
  dna_compliance: bool              // czy mieści się w genome_bounds

  style_score

  style_status:
      COMPLIANT
      NEEDS_REVISION
      NONCOMPLIANT

  reasons[]
  must_fix[]
}
```

`Style Critic → StyleEvaluation.style_status` i `Quality Gate → quality_decision` to dwa niezależne werdykty na tym samym artefakcie — nie wolno ich zlewać w jedno pole.

### 2.6 `StylePerformanceRecord`
Historia skuteczności recipe/technik, indeksowana wielowymiarowo.

```
StylePerformanceRecord {
  recipe_id
  scene_index_key: SceneIndexKey
  quality_score
  dna_version

  style_evaluation_id
  quality_evaluation_id
  accepted_artifact_id
  accepted_artifact_hash   // musi zgadzać się z artifact_hash obu ocen — patrz 3.2

  status: ACCEPTED   // patrz reguła w 3.2
}

SceneIndexKey {
  scene_type
  narrative_function
  pov
  target_tension
  target_pace
  book_style_dna_version
}
```

---

## 3. Reguły niezmienne (invariants)

### 3.1 Nadrzędność `BookStyleDNA`
Composer **nigdy** nie generuje `target_genome` poza `genome_bounds`. `locked_identity_markers` są twardym ograniczeniem — naruszenie blokuje recipe przed przejściem do Writera (fail fast, nie po fakcie w Critic).

### 3.2 Zapis do pamięci tylko po ACCEPT obu bramek na tej samej wersji artefaktu
`StylePerformanceRecord` może zostać utworzony wyłącznie wtedy, gdy spełnione są łącznie trzy warunki:

1. `StyleEvaluation` dla dokładnie tej wersji artefaktu ma `style_status = COMPLIANT`.
2. Quality Gate dla dokładnie tej samej wersji artefaktu ma `quality_decision = ACCEPT`.
3. `artifact_hash` obu ocen odpowiada `accepted_artifact_hash`.

`REVISE` ani `REJECT` na którymkolwiek etapie nie aktualizują Style Memory/Performance. Warunek 3 nie jest formalnością — zabezpiecza przed sytuacją, w której `v3` przechodzi Style Critic jako `COMPLIANT`, ale Quality Gate zwraca `REVISE`, powstaje `v4`, i system błędnie zapisałby ocenę stylu z `v3` jako skuteczność `v4`. Bez wymogu zgodności hashy między obiema ocenami a zaakceptowanym artefaktem Style Memory uczyłaby się na niedopasowanych parach (ocena jednej wersji, artefakt innej).

### 3.3 Indeksacja per kontekst sceny, nie globalnie
Composer, dobierając recipe na podstawie historii, filtruje `StylePerformanceRecord` po pełnym `SceneIndexKey`, nie po samej średniej jakości. Recipe skuteczne dla pościgu (wysoki `target_pace`, `target_tension`) nie jest kandydatem dla sceny żałoby — różny `narrative_function`. Fallback przy braku danych dla danego klucza: agregacja po `scene_type + narrative_function` (bez POV/tension/pace), z oznaczeniem `low_confidence`.

### 3.4 Wersjonowanie DNA jako granica ważności danych
`StylePerformanceRecord` przypisany do `book_style_dna_version` traci wagę (nie jest usuwany) po zmianie wersji DNA. Composer może go użyć tylko jako sygnał niższej wagi, nigdy jako podstawowe źródło przy nowej wersji.

---

## 4. Przepływ (kanoniczny)

```
BookStyleDNA + SceneContract + StyleLibraryProfile[] + StylePerformanceRecord[]
        ↓
    Style Composer  ── (respektuje genome_bounds + locked_identity_markers)
        ↓
    SceneStyleRecipe
        ↓
    Writer
        ↓
    Style Critic
        ↓
    StyleEvaluation
        │
        ├─ NEEDS_REVISION / NONCOMPLIANT
        │        ↓
        │      REVISE (bez zapisu do Memory)
        │        ↓
        │   Composer / Writer
        │
        └─ COMPLIANT
                 ↓
            Quality Gate
                 │
           ┌─────┼──────┐
           ↓     ↓      ↓
        ACCEPT REVISE REJECT
           ↓
    StylePerformanceRecord (status: ACCEPTED, wymaga zgodności artifact_hash — patrz 3.2)
```

---

## 5. Granice względem architektury v1.2

| Element v1.2 | Rola w tej warstwie |
|---|---|
| `StyleProfile` | Źródło prawdy tożsamości stylistycznej; nadrzędne wobec `BookStyleDNA` |
| Dziedziczenie stylu książki | Mechanizm inicjalizujący `BookStyleDNA` z `StyleProfile` |
| Abstrakcja referencji autorskich | Wejście do `StyleLibraryProfile.source_ref` |
| Kontekst Kontrolera Stylu | Konsument `StyleEvaluation` i `dna_compliance` |
| Kontroler Jakości (Quality Gate) | Niezależny werdykt `quality_decision` nad tym samym artefaktem; kontrakt `QualityEvaluation` poza zakresem tego ADR |

Żaden z sześciu nowych kontraktów nie duplikuje odpowiedzialności `StyleProfile`. Composer nie ma uprawnień do modyfikacji `StyleProfile` ani `BookStyleDNA` — tylko do odczytu i generowania `SceneStyleRecipe` w ich granicach.

---

## 6. Konsekwencje

**Zyskujemy:** audytowalność decyzji stylistycznych (`rationale`), odporność na drift stylu między scenami, uczenie się kontekstowe zamiast globalnej optymalizacji jakości.

**Koszt:** dodatkowa złożoność schematu danych (6 nowych obiektów), konieczność utrzymania spójności wersji DNA ↔ Performance Records, narzut na fallback przy rzadkich `SceneIndexKey`.

**Ryzyko do monitorowania:** przy małej liczbie zaakceptowanych scen na dany `SceneIndexKey`, Composer będzie działał głównie na `low_confidence` fallbacku — do rozważenia próg minimalnej liczby rekordów przed użyciem Performance History jako sygnału decyzyjnego (nie rozstrzygam tego w tym ADR, to decyzja implementacyjna).

---

## 7. Następny krok

Ten ADR jest gotowy do zamrożenia jako kontrakt warstwy. Kolejny dokument: specyfikacja algorytmu selekcji Composera (jak dokładnie `target_genome` jest wybierany z przecięcia `genome_bounds` × historia × dostępne techniki) — to już wymaga decyzji projektowej (np. weighted scoring vs constraint satisfaction), nie tylko kontraktu danych.
