"""Run from PowerShell: python -m app.operator_cli --help."""
from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from app.operator_dpapi import default_secret_path, read_secret
from app.p20_core.local_operator import initialize_operator, rotate_operator, revoke_operator, OperatorError
from app.p20_core.project_repository import ensure_system_repository


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Operator requests cannot follow redirects")


def request_json(base_url: str, path: str, token: str, body: dict | None = None):
    url = urllib.parse.urlsplit(base_url)
    # Numeric loopback only in the credential-bearing client; no DNS/proxy leaks.
    if (url.scheme != "http" or not ipaddress.ip_address(url.hostname or "").is_loopback
            or url.username or url.password or url.query or url.fragment or url.path not in ("", "/")):
        raise ValueError("Use an HTTP numeric loopback address without credentials or a path")
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(base_url.rstrip("/") + path, data=data,
                                    headers={"Authorization": "Bearer " + token,
                                             "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    with opener.open(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Local AgentPRO operator; never prints a credential")
    parser.add_argument("command", choices=["init", "rotate", "revoke", "review", "decide", "commit"])
    parser.add_argument("--secret-path", type=Path, default=default_secret_path())
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--project")
    parser.add_argument("--proposal")
    args = parser.parse_args()
    try:
        if args.command in {"init", "rotate", "revoke"}:
            function = {"init": initialize_operator, "rotate": rotate_operator, "revoke": revoke_operator}[args.command]
            result = function(ensure_system_repository(), args.secret_path)
            print(json.dumps({"operation": args.command, "operator": result.to_dict() if result else None}))
            return 0
        if not args.project or not args.proposal:
            parser.error("--project and --proposal are required")
        path = "/operator/projects/" + urllib.parse.quote(args.project, safe="") + "/proposals/" + urllib.parse.quote(args.proposal, safe="")
        token = read_secret(args.secret_path)
        review = request_json(args.url, path + ("/review" if args.command == "decide" else ""), token,
                              {} if args.command == "decide" else None)
        print(json.dumps(review, ensure_ascii=False, indent=2))
        if args.command == "commit":
            print(json.dumps(request_json(args.url, path + "/commit", token,
                {"proposal_hash": review["proposal"]["proposal_hash"]}), indent=2))
        if args.command == "decide" and review["challenge"] is not None:
            decision = input("Wpisz APPROVE lub REJECT dla pokazanej propozycji (inne: anuluj): ").strip()
            if decision not in {"APPROVE", "REJECT"}:
                print("Anulowano; brak decyzji.")
                return 0
            proposal = review["proposal"]
            body = {k: proposal[k] for k in ("proposal_hash", "scope_type", "scope_id")}
            body.update(challenge_id=review["challenge"]["challenge_id"], decision=decision)
            print(json.dumps(request_json(args.url, path + "/decision", token, body), indent=2))
        return 0
    except (OperatorError, OSError, ValueError, RuntimeError, urllib.error.HTTPError) as exc:
        # Do not render request objects, token values, HTTP response payloads or tracebacks.
        print("Operacja niedostępna: " + (exc.code if isinstance(exc, OperatorError) else type(exc).__name__))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
