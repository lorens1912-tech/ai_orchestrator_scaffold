from __future__ import annotations

import importlib
from typing import Any, Dict, List

from fastapi import FastAPI


app = FastAPI(title="AI Orchestrator Scaffold", version="1.0")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/debug/routes")
def debug_routes():
    items: List[Dict[str, Any]] = []
    for r in app.router.routes:
        methods = getattr(r, "methods", None)
        path = getattr(r, "path", None)
        name = getattr(r, "name", None)
        if path:
            items.append({"path": path, "methods": sorted(list(methods)) if methods else [], "name": name})
    return {"ok": True, "items": items}


def _include_router_safe(module_name: str, router_attr: str = "router") -> None:
    try:
        mod = importlib.import_module(module_name)
        router = getattr(mod, router_attr, None)
        if router is None:
            print(f"[router] {module_name}.{router_attr} not found")
            return
        app.include_router(router)
        print(f"[router] included: {module_name}.{router_attr}")
    except Exception as e:
        print(f"[router] NOT included: {module_name}.{router_attr} -> {e!r}")


# --- BOOKS TOOLS (UI contract: artifacts + runs) ---
_include_router_safe("books_router_bundle", "router")
