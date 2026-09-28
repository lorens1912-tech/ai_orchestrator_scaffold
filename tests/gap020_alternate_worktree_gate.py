"""Run each GAP-020 launcher from a second valid checkout and foreign cwd."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from tests.gap020_phase4_practical_gate import (
    DIAGNOSTIC, PRODUCTION, Server, check_import_boundary, check_routes,
    expect_http, require,
)


def main() -> int:
    require(len(sys.argv) == 2, "pass the alternate worktree root")
    root = Path(sys.argv[1]).resolve(strict=True)
    require((root / ".git").exists() and (root / "app" / "main.py").is_file(),
            "alternate path is not a valid worktree")
    with tempfile.TemporaryDirectory(prefix="gap020-alt-") as directory:
        temp = Path(directory).resolve()
        storage = temp / "storage"
        storage.mkdir()
        foreign = temp / "foreign"
        foreign.mkdir()
        env = os.environ.copy()
        env.update({
            "AGENTPRO_STORAGE_ROOT": str(storage),
            "AGENT_TEST_MODE": "1",
            "PYTEST_FASTPATH": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONVERBOSE": "1",
            "PYTHONPATH": "",
        })
        env.pop("OPENAI_API_KEY", None)
        results = {}
        for name in (*PRODUCTION, *DIAGNOSTIC):
            with Server(name, foreign, env, temp, root=root) as server:
                check_routes(server.port)
                check_import_boundary(server.log_file, root=root)
                request = {
                    "project_id": "PROJ-gap020-alt-" + str(len(results)),
                    "book_id": "BOOK-gap020-alt-" + str(len(results)),
                    "run_id": "run-gap020-alt-" + str(len(results)),
                    "step_id": "step-gap020-alt-" + str(len(results)),
                    "modes": ["QUALITY"],
                    "payload": {"text": "Neutral synthetic sentence. " * 14},
                }
                response = expect_http(server.port, "POST", "/agent/step", 200, request)
                require(response["evaluation_record"]["project_id"] == request["project_id"],
                        f"{name} did not use project-local P20 evaluation")
                results[name] = "PASS"
        print(json.dumps({"status": "PASS", "alternate_root": str(root),
                          "launchers": results}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
