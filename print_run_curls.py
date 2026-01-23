import json

OPENAPI = r"C:\AI\ai_orchestrator_scaffold\openapi.json"

def pick(name: str, typ: str):
    if name == "model":
        return "gpt-4.1-mini"
    if name in ("prompt_text", "prompt", "text"):
        return "TEST"
    if name == "book":
        return "test"
    if name == "max_output_tokens":
        return 1200
    if name in ("target_words", "delta"):
        return 3000
    if name == "chunk_words":
        return 1200
    if typ == "boolean":
        return False
    if typ == "integer":
        return 1
    if typ == "number":
        return 0.0
    return "TEST"

def build_body_and_params(d: dict, path: str):
    post = d["paths"][path].get("post", {})
    rb = post.get("requestBody", {}).get("content", {}).get("application/json", {})
    sch = rb.get("schema", {})
    params = post.get("parameters", []) or []

    ref = sch.get("$ref")
    if not ref:
        return {}, params

    name = ref.split("/")[-1]
    comp = d["components"]["schemas"][name]
    required = comp.get("required", []) or []
    props = comp.get("properties", {}) or {}

    obj = {}
    for k in required:
        s = props.get(k, {})
        if "default" in s:
            obj[k] = s["default"]
        else:
            obj[k] = pick(k, s.get("type", "string"))
    return obj, params

def main():
    d = json.load(open(OPENAPI, encoding="utf-8"))

    for path in ("/books/run", "/books/agent/run"):
        obj, params = build_body_and_params(d, path)
        j = json.dumps(obj, ensure_ascii=False)

        has_book_param = any(
            (p.get("name") == "book" and p.get("in") in ("path", "query"))
            for p in (params or [])
        )

        print("\n=== " + path + " ===")
        if path == "/books/run" and has_book_param:
            print("curl.exe -i -X POST \"http://127.0.0.1:8000/books/run/test\" -H \"Content-Type: application/json\" -d '" + j + "'")
            print("curl.exe -i -X POST \"http://127.0.0.1:8000/books/run?book=test\" -H \"Content-Type: application/json\" -d '" + j + "'")
        else:
            print("curl.exe -i -X POST \"http://127.0.0.1:8000" + path + "\" -H \"Content-Type: application/json\" -d '" + j + "'")

if __name__ == "__main__":
    main()
