from pathlib import Path

import pytest

from world_cup_tipping.import_schedule import import_world_cup_schedule


WORKBOOK = Path(__file__).resolve().parents[1] / "world_cup_2026_v1.3.xlsx"

if not WORKBOOK.exists():
    pytest.skip("Local source workbook is not published with the repo.", allow_module_level=True)


def test_imports_full_schedule() -> None:
    fixtures, groups = import_world_cup_schedule(WORKBOOK)
    assert len(fixtures) == 104
    assert len([fixture for fixture in fixtures if fixture["stage"] == "group"]) == 72
    assert fixtures[0]["match_id"] == "2026-001"
    assert fixtures[-1]["match_id"] == "2026-104"


def test_imports_groups_a_to_l() -> None:
    _, groups = import_world_cup_schedule(WORKBOOK)
    assert set(groups) == set("ABCDEFGHIJKL")
    assert all(len(teams) == 4 for teams in groups.values())
    assert groups["A"] == ["Mexico", "Korea Republic", "Czech Republic", "South Africa"]


def test_imports_utc_kickoff_times() -> None:
    fixtures, _ = import_world_cup_schedule(WORKBOOK)
    assert fixtures[0]["kickoff_at"] == "2026-06-11T19:00:00Z"
    assert fixtures[72]["kickoff_at"].endswith("Z")
