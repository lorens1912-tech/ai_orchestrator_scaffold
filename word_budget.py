# word_budget.py
from __future__ import annotations
from typing import List, Optional, Tuple


class WordBudget:
    """
    Dynamiczny budżet słów na rozdziały.
    - Domyślnie rozdziela równo, ale automatycznie "nadrabia" w kolejnych rozdziałach.
    - Opcjonalnie wspiera wagi (np. cięższe rozdziały dostają większy target).
    - Każdy rozdział ma widełki +/- tol (domyślnie 5%).
    - Ostatni rozdział: HARD CLOSE (dokładnie dobijamy do total_words).
    """

    def __init__(
        self,
        total_words: int,
        chapters_total: int,
        tol: float = 0.05,
        weights: Optional[List[float]] = None,
    ) -> None:
        if total_words <= 0:
            raise ValueError("total_words musi być > 0")
        if chapters_total <= 0:
            raise ValueError("chapters_total musi być > 0")
        if not (0.0 <= tol <= 0.5):
            raise ValueError("tol musi być w zakresie 0..0.5")

        self.total_words = int(total_words)
        self.chapters_total = int(chapters_total)
        self.tol = float(tol)

        if weights is None:
            self.weights = [1.0] * self.chapters_total
        else:
            if len(weights) != self.chapters_total:
                raise ValueError("weights musi mieć długość równą chapters_total")
            if any(w <= 0 for w in weights):
                raise ValueError("wszystkie wagi muszą być > 0")
            self.weights = [float(w) for w in weights]

        self.words_written_total = 0
        self.chapter_words = [0] * self.chapters_total

    def remaining_words(self) -> int:
        return max(0, self.total_words - self.words_written_total)

    def _target_for(self, chapter_idx: int) -> int:
        """Wylicza target dla rozdziału (1..N) na podstawie pozostałego budżetu i wag."""
        if not (1 <= chapter_idx <= self.chapters_total):
            raise ValueError("chapter_idx poza zakresem 1..chapters_total")

        remaining = self.remaining_words()
        # ostatni rozdział domyka total
        if chapter_idx == self.chapters_total:
            return remaining

        i = chapter_idx - 1
        w_i = self.weights[i]
        w_sum = sum(self.weights[i:])  # wagi pozostałych rozdziałów (włącznie z tym)

        # proporcjonalny przydział z pozostałego budżetu (to robi "nadganianie" automatycznie)
        target = int(round(remaining * (w_i / w_sum)))

        # sanity: nie wyjdź poza remaining
        if target < 0:
            target = 0
        if target > remaining:
            target = remaining

        return target

    def range(self, chapter_idx: int) -> Tuple[int, int, int]:
        """
        Zwraca (target, low, high).
        low/high to widełki +/- tol, ale ostatni rozdział ma (remaining, remaining, remaining).
        """
        target = self._target_for(chapter_idx)
        if chapter_idx == self.chapters_total:
            return target, target, target

        low = int(round(target * (1.0 - self.tol)))
        high = int(round(target * (1.0 + self.tol)))

        remaining = self.remaining_words()
        if low < 0:
            low = 0
        if high < low:
            high = low
        if low > remaining:
            low = remaining
        if high > remaining:
            high = remaining
        if high < low:
            high = low

        return target, low, high

    def commit(self, chapter_idx: int, actual_words: int) -> None:
        """Zapisuje ile faktycznie wyszło i aktualizuje sumę."""
        if not (1 <= chapter_idx <= self.chapters_total):
            raise ValueError("chapter_idx poza zakresem 1..chapters_total")
        if actual_words < 0:
            raise ValueError("actual_words musi być >= 0")

        i = chapter_idx - 1
        prev = self.chapter_words[i]
        self.chapter_words[i] = int(actual_words)
        self.words_written_total += int(actual_words) - prev
