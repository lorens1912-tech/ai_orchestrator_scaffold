import pytest

from app.pro_writer_lane import WriterLaneRoute, normalize_writer_mode, resolve_writer_lane


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("write", "WRITE"),
        (" EDIT ", "EDIT"),
        ("critic", "CRITIC"),
        ("critique", "CRITIC"),
        ("review", "CRITIC"),
    ],
)
def test_025_normalize_writer_mode_ok(raw, expected):
    assert normalize_writer_mode(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", None, "FACTCHECK", "QUALITY"])
def test_025_normalize_writer_mode_reject(raw):
    with pytest.raises(ValueError):
        normalize_writer_mode(raw)


@pytest.mark.parametrize(
    "mode,stage",
    [
        ("WRITE", "draft"),
        ("EDIT", "edit"),
        ("CRITIC", "critic"),
    ],
)
def test_025_resolve_writer_lane_contract(mode, stage):
    route = resolve_writer_lane(mode, preset="ORCH_STANDARD")
    assert isinstance(route, WriterLaneRoute)
    assert route.mode == mode
    assert route.lane == "PRO_WRITER"
    assert route.stage == stage
    assert route.preset == "ORCH_STANDARD"


def test_025_resolve_writer_lane_alias_critic():
    route = resolve_writer_lane("critique")
    assert route.mode == "CRITIC"
    assert route.stage == "critic"
