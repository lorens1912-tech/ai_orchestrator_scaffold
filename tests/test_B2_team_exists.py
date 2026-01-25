import json
from pathlib import Path

from app.team_layer import policy_for_team

def _team_ids_from_teams_json(obj):
    # teams.json może być {"teams":[{"id":...},...]} albo {"TEAM_ID": {...}}
    if isinstance(obj, dict) and "teams" in obj and isinstance(obj["teams"], list):
        return {t.get("id") for t in obj["teams"] if isinstance(t, dict) and t.get("id")}
    if isinstance(obj, dict):
        return set(obj.keys())
    return set()

def test_every_mapped_team_exists_and_has_resolvable_policy():
    map_ = json.loads(Path("config/mode_team_map.json").read_text("utf-8"))
    teams = json.loads(Path("config/teams.json").read_text("utf-8"))

    team_ids = _team_ids_from_teams_json(teams)
    used_teams = set(map_.values())

    missing_team = sorted([t for t in used_teams if t not in team_ids])
    assert not missing_team, f"Missing TEAM definitions in config/teams.json: {missing_team}"

    # Realny kontrakt: team_layer.policy_for_team(team_id) musi zwrócić policy z MODELEM.
    missing_policy = []
    bad_policy = []

    for t in sorted(used_teams):
        try:
            pol = policy_for_team(t) or {}
        except Exception as e:
            missing_policy.append((t, str(e)))
            continue

        model = pol.get("model") or pol.get("MODEL") or pol.get("default_model")
        if not isinstance(model, str) or not model.strip():
            bad_policy.append((t, pol))

    assert not missing_policy, f"TEAM policy resolution failed: {missing_policy}"
    assert not bad_policy, f"TEAM policy has no model: {bad_policy}"
