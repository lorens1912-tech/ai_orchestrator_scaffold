"""Phase 4 regressions found by the independent practical gate."""
from __future__ import annotations

from app.p20_core.translation_execution import MAX_UNIT_BYTES, _segments


def test_chapter_separator_never_becomes_whitespace_only_unit():
    first = "neutral " * 1000
    content = first + "\n\n" + "second."
    boundary = len(first.encode("utf-8")) + 2
    chapters = [
        {"chapter_id": "CH-1", "start_byte": 0, "end_byte": boundary},
        {"chapter_id": "CH-2", "start_byte": boundary,
         "end_byte": len(content.encode("utf-8"))},
    ]
    units = list(_segments(content, chapters))
    assert b"".join(item[3].encode("utf-8") for item in units) == content.encode("utf-8")
    assert all(item[3].strip() for item in units)
    assert all(item[2] - item[1] <= MAX_UNIT_BYTES for item in units)
    assert units[0][2] < boundary
    assert units[1][2] == boundary
