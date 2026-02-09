from pathlib import Path
import re

path = Path("app/main.py")
txt = path.read_text(encoding="utf-8", errors="ignore")

# 1) Sanitizacja artefaktów po patchowaniu
txt = txt.replace("`r`n", "")
txt = txt.replace("\ufeff", "")

# 2) Usuń ręczne ustawianie Content-Length (to najczęściej wywołuje mismatch)
patterns = [
    r'(?im)^\s*response\.headers\[\s*[\'"]content-length[\'"]\s*\]\s*=.*$',
    r'(?im)^\s*response\.headers\[\s*[\'"]Content-Length[\'"]\s*\]\s*=.*$',
    r'(?im)^\s*headers\[\s*[\'"]content-length[\'"]\s*\]\s*=.*$',
    r'(?im)^\s*headers\[\s*[\'"]Content-Length[\'"]\s*\]\s*=.*$',
]
for p in patterns:
    txt = re.sub(p, "", txt)

# 3) Dodaj guard tylko raz
guard = r'''
class _P20_6ContentLengthGuard:
    """
    ASGI middleware: wymusza zgodność Content-Length z realnym body.
    Działa jako bezpieczny guard na błędy typu:
    h11.LocalProtocolError: Too much data for declared Content-Length
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        start_msg = None
        body_parts = []

        async def send_wrapper(message):
            nonlocal start_msg, body_parts

            mtype = message.get("type")
            if mtype == "http.response.start":
                # wstrzymaj start, aż zbierzemy finalne body
                start_msg = message
                return

            if mtype == "http.response.body":
                body_parts.append(message.get("body", b""))
                if message.get("more_body", False):
                    return

                if start_msg is None:
                    # fallback: gdyby ktoś wysłał body bez start
                    await send(message)
                    return

                body = b"".join(body_parts)
                headers = [
                    (k, v) for (k, v) in start_msg.get("headers", [])
                    if k.lower() != b"content-length"
                ]
                headers.append((b"content-length", str(len(body)).encode("ascii")))
                start_msg["headers"] = headers

                await send(start_msg)
                await send({
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False
                })
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)
'''

if "_P20_6ContentLengthGuard" not in txt:
    txt = txt.rstrip() + "\n\n" + guard + "\n"

if "app = _P20_6ContentLengthGuard(app)" not in txt:
    txt = txt.rstrip() + "\n\n# P20.6 hotfix: guard Content-Length mismatch\napp = _P20_6ContentLengthGuard(app)\n"

# kosmetyka pustych linii
txt = re.sub(r"\n{3,}", "\n\n", txt)

path.write_text(txt, encoding="utf-8", newline="\n")
print("PATCH_OK:", path)
