from __future__ import annotations

from typing import Any


def call_text_direct(*args: Any, **kwargs: Any) -> str:
    """
    Minimal compat shim.

    This project previously imported `app.openai_direct.call_text_direct`
    from routes/modules that are now mostly exercised only for API surface
    and roundtrip tests. The shim restores importability without claiming
    provider functionality.

    If a runtime path actually needs direct LLM execution through this
    legacy entrypoint, fail explicitly instead of returning fake content.
    """
    raise RuntimeError(
        "app.openai_direct.call_text_direct compat shim was invoked, "
        "but no direct OpenAI transport is configured in this runtime."
    )
