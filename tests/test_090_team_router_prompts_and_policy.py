import unittest

from app.team_router import resolve_team_context

class Test090TeamRouter(unittest.TestCase):
    def test_resolve_writer_write_loads_policy_and_prompts(self):
        ctx = resolve_team_context("WRITER", "WRITE")
        self.assertEqual(ctx.team_id, "WRITER")
        self.assertEqual(ctx.mode, "WRITE")
        self.assertTrue(ctx.policy.model)
        self.assertTrue(isinstance(ctx.policy.max_tokens, int))
        # system prompt exists
        self.assertTrue(isinstance(ctx.prompts.get("system"), str))
        # mode prompt exists for WRITE
        self.assertIn("TRYB WRITE", ctx.prompts.get("mode", ""))
        self.assertTrue(ctx.prompt_id.endswith("/WRITE.txt") or ctx.prompt_id.endswith("/system.txt"))

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            resolve_team_context("QA", "WRITE")  # QA allowed only QUALITY

if __name__ == "__main__":
    unittest.main()
