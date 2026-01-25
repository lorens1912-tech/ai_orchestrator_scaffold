import json
from pathlib import Path

def test_every_mode_has_team():
    modes = json.loads(Path("app/modes.json").read_text("utf-8"))
    map_ = json.loads(Path("config/mode_team_map.json").read_text("utf-8"))
    mode_ids = [m["id"] for m in modes["modes"]]

    for m in mode_ids:
        assert m in map_, f"MODE {m} has no TEAM mapping"
