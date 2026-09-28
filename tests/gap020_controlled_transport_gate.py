"""Process-level production WRITE transport probe against a local controlled HTTP provider."""
from __future__ import annotations

import json
import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.gap020_phase4_practical_gate import Server, ROOT, check_import_boundary, expect_http, require


class Provider(BaseHTTPRequestHandler):
    calls: list[dict] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        self.calls.append({"path": self.path, "request": request})
        payload = {
            "id": "resp-gap020-controlled", "object": "response", "created_at": 0,
            "status": "completed", "model": "gpt-gap020-effective",
            "output": [{"id": "msg-gap020", "type": "message", "status": "completed",
                        "role": "assistant", "content": [{"type": "output_text",
                        "text": "A neutral courier delivered a sealed key at dusk.",
                        "annotations": []}]}],
            "usage": {"input_tokens": 10, "output_tokens": 12, "total_tokens": 22},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, _format: str, *_args: object) -> None:
        pass


def main() -> int:
    from app.p20_core.book_bible_test_helper import ensure_test_book_bible
    from app.p20_core.project_repository import ProjectRepository, StorageResolver

    Provider.calls = []
    with tempfile.TemporaryDirectory(prefix="gap020-transport-") as directory:
        temp = Path(directory).resolve()
        storage = temp / "storage"
        storage.mkdir()
        foreign = temp / "foreign"
        foreign.mkdir()
        os.environ["AGENTPRO_STORAGE_ROOT"] = str(storage)
        book = "write-gap020-controlled"
        project = "PROJ-gap020-controlled"
        ensure_test_book_bible(book)
        provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        try:
            env = os.environ.copy()
            env.update({
                "AGENTPRO_STORAGE_ROOT": str(storage),
                "AGENT_TEST_MODE": "1",
                "PYTEST_FASTPATH": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONVERBOSE": "1",
                "PYTHONPATH": "",
                "OPENAI_API_KEY": "sk-gap020-controlled-local-only",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{provider.server_port}/v1",
                "OPENAI_API_MODE": "responses",
                "MODEL_ALLOWLIST": "gpt-gap020-effective",
                "MODEL_DEFAULT": "gpt-gap020-effective",
                "MODEL_POLICY_MODE": "PERMISSIVE",
            })
            with Server("run_server.ps1", foreign, env, temp) as server:
                request = {
                    "project_id": project, "book_id": book,
                    "run_id": "run-gap020-controlled", "step_id": "step-gap020-controlled",
                    "modes": ["WRITE"],
                    "payload": {"input": "Neutral synthetic scene: a sealed key arrives at dusk.",
                                "model": "gpt-gap020-requested"},
                }
                result = expect_http(server.port, "POST", "/agent/step", 200, request)
                require(result["artifact_paths"], "controlled WRITE has no artifact")
                check_import_boundary(server.log_file)
            require(Provider.calls and Provider.calls[0]["path"] == "/v1/responses",
                    f"controlled production writer transport missing; calls={len(Provider.calls)}")
            require(Provider.calls[0]["request"]["model"] == "gpt-gap020-effective",
                    "effective model was not sent to controlled transport")
            repository = ProjectRepository(StorageResolver(storage).resolve_project(
                project, book_id="BOOK-" + book))
            traces = [trace for trace in repository.list_model_invocations()
                      if trace["role"] == "WRITER"]
            require(len(traces) == 1, "controlled transport did not persist one writer trace")
            trace = traces[0]
            require(trace["requested"]["model"] == "gpt-gap020-requested" and
                    trace["resolved"]["decision"]["effective_model"] == "gpt-gap020-effective"
                    and trace["attempts"][0]["provider_reported"]["response_id"] ==
                    "resp-gap020-controlled" and trace["validation"] == "VALID",
                    "controlled transport provenance mismatch")
        finally:
            provider.shutdown()
            provider.server_close()
            thread.join(timeout=10)
    print(json.dumps({"status": "PASS", "transport": "local HTTP Responses",
                      "calls": len(Provider.calls), "writer_trace": "VALID", "root": str(ROOT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
