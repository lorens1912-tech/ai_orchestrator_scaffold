import unittest

class Test081AgentStepSingleOwner(unittest.TestCase):
    def test_only_one_agent_step_route(self):
        from app.main import app
        hits = []
        for r in app.routes:
            p = getattr(r, "path", "")
            if p == "/agent/step":
                ep = getattr(r, "endpoint", None)
                mod = getattr(ep, "__module__", "?") if ep else "?"
                hits.append(mod)
        self.assertEqual(len(hits), 1, hits)

if __name__ == "__main__":
    unittest.main()
