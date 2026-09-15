import unittest
from app.quality_rules import evaluate_quality


GOOD_TEXT = """
TEST_CHARACTER_A wszedł do TEST_PLACE_A kilka minut przed rozpoczęciem próby. Pomieszczenie było jasne, uporządkowane
i przygotowane do spokojnej pracy. Na środku stał stół, a obok niego dwa krzesła. Każdy przedmiot miał oznaczone
miejsce, dlatego łatwo było zauważyć nawet drobną zmianę układu.

Na stole leżał formularz oznaczony FACT_TEST_001. Dokument należał do TEST_PROJECT_A i opisywał dane robocze
TEST_BOOK_A. TEST_CHARACTER_A przeczytał wszystkie pola, porównał numery wersji i zapisał wynik kontroli w pustej
rubryce. Nie znalazł braków, sprzeczności ani śladów przypadkowej korekty. Następnie odłożył formularz dokładnie tam,
gdzie znajdował się na początku.

Po chwili do pomieszczenia wszedł TEST_CHARACTER_B. Druga osoba powtórzyła kontrolę według tej samej kolejności,
sprawdziła podpisy i porównała wynik z danymi wejściowymi. Oboje omówili różnice między obserwacją a wnioskiem,
po czym zgodnie potwierdzili, że rekord pozostał niezmieniony. Na końcu zamknęli sesję, uporządkowali materiały
i zapisali krótką informację o zakończeniu próby. Raport zawierał godzinę, identyfikator miejsca oraz jednoznaczny
wynik, dzięki czemu kolejna osoba mogła odtworzyć cały przebieg bez dodatkowych wyjaśnień.
""".strip()


class TestQualityGateV2(unittest.TestCase):
    def test_reject_meta_ai(self):
        txt = "Jako model językowy nie mogę tego zrobić, ale mogę opisać ogólnie."
        r = evaluate_quality(txt, min_words=50)
        self.assertEqual(r["decision"], "REJECT")
        ids = [x["id"] for x in r["must_fix"]]
        self.assertIn("META_AI", ids)

    def test_revise_lists_in_prose(self):
        txt = "- Punkt pierwszy\n- Punkt drugi\n- Punkt trzeci\n"
        r = evaluate_quality(txt, min_words=10, forbid_lists=True)
        self.assertEqual(r["decision"], "REVISE")
        ids = [x["id"] for x in r["must_fix"]]
        self.assertIn("LISTS_IN_PROSE", ids)

    def test_accept_good_prose(self):
        r = evaluate_quality(GOOD_TEXT, min_words=120, forbid_lists=True)
        self.assertEqual(r["decision"], "ACCEPT")


if __name__ == "__main__":
    unittest.main()
