import unittest
from app.quality_rules import evaluate_quality


GOOD_TEXT = """
Deszcz dzwonił o parapet jak cierpliwy telegrafista. Adam stał przy oknie, liczył oddechy miasta i próbował udawać,
że neon po drugiej stronie ulicy nie mruga dokładnie w rytmie jego tętna. W kieszeni miał telefon. Milczał.
To milczenie było podejrzane.

Na blacie kuchennym leżał wydruk — kilka cyfr, kilka dat, jeden podpis. Wystarczyło, żeby komuś odebrać spokojny sen.
Adam przesunął palcem po papierze, jakby mógł wygładzić fakty. Nie mógł. Fakty miały krawędzie.

Drzwi skrzypnęły. Ktoś wszedł bez pukania. Adam nie odwrócił się od razu. Najpierw usłyszał zapach mokrego płaszcza,
potem krok, pewny i lekki, jakby właściciel nóg znał już układ mieszkania.

— Masz to? — padło pytanie.
— Mam — odpowiedział Adam. — I mam też problem.
Cisza między nimi nie była pusta. Była pełna decyzji.

Gdy w końcu się odwrócił, zobaczył twarz, której nie powinno tu być. A jednak była. I to oznaczało, że gra właśnie
zmieniła zasady, tylko nikt nie raczył go o tym poinformować.
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
