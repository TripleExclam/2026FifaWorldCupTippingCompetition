from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from world_cup_tipping.import_schedule import import_world_cup_schedule
from world_cup_tipping.simulation import SimulationConfig, simulate_contestant


WORKBOOK = Path(__file__).resolve().parents[1] / "world_cup_2026_v1.3.xlsx"


def test_simulates_full_tournament_from_contestant_predictions() -> None:
    fixtures, groups = import_world_cup_schedule(WORKBOOK)
    calls = []

    async def fixed_prediction(fixture: dict[str, Any], previous_results: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append((fixture["match_number"], len(previous_results), fixture["team_a"], fixture["team_b"]))
        return {
            "predicted_score_a": 2,
            "predicted_score_b": 1,
            "predicted_winner": fixture["team_a"],
            "confidence": 0.9,
        }

    simulation = asyncio.run(
        simulate_contestant(
            {"id": "fixed", "name": "Fixed", "url": "http://example.test/predict", "status": "active"},
            fixtures,
            groups,
            SimulationConfig(timeout_seconds=0.1, retries=0),
            fixed_prediction,
        )
    )

    assert len(calls) == 104
    assert calls[0][1] == 0
    assert calls[-1][1] == 103
    assert len(simulation["matches"]) == 104
    assert simulation["status"] == "completed"
    assert simulation["error_count"] == 0
    assert simulation["champion"]
    assert len(simulation["bracket"]["round_of_32"]) == 16
    assert len(simulation["bracket"]["final"]) == 1
    assert set(simulation["group_standings"]) == set("ABCDEFGHIJKL")
    assert all(len(rows) == 4 for rows in simulation["group_standings"].values())


def test_completed_results_are_used_without_calling_contestant() -> None:
    fixtures = [
        {
            "match_id": "2026-001",
            "match_number": 1,
            "stage": "group",
            "group": "A",
            "team_a": "Mexico",
            "team_b": "Canada",
            "score_a": 1,
            "score_b": 0,
            "winner": "Mexico",
            "status": "completed",
        },
        {
            "match_id": "2026-002",
            "match_number": 2,
            "stage": "group",
            "group": "A",
            "team_a": "Brazil",
            "team_b": "Japan",
            "score_a": None,
            "score_b": None,
            "winner": None,
            "status": "scheduled",
        },
    ]
    groups = {"A": ["Mexico", "Canada", "Brazil", "Japan"]}
    calls = []

    async def fixed_prediction(fixture: dict[str, Any], previous_results: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append((fixture["match_number"], [dict(result) for result in previous_results]))
        return {
            "predicted_score_a": 2,
            "predicted_score_b": 1,
            "predicted_winner": fixture["team_a"],
            "confidence": 0.9,
        }

    simulation = asyncio.run(
        simulate_contestant(
            {"id": "fixed", "name": "Fixed", "url": "http://example.test/predict", "status": "active"},
            fixtures,
            groups,
            SimulationConfig(timeout_seconds=0.1, retries=0),
            fixed_prediction,
        )
    )

    assert [call[0] for call in calls] == [2]
    assert calls[0][1] == [
        {
            "match_id": "2026-001",
            "stage": "group",
            "team_a": "Mexico",
            "team_b": "Canada",
            "score_a": 1,
            "score_b": 0,
            "winner": "Mexico",
        }
    ]
    assert simulation["matches"][0]["score_a"] == 1
    assert simulation["matches"][0]["score_b"] == 0
    assert simulation["matches"][0]["winner"] == "Mexico"
    assert simulation["matches"][0]["actual_result"] is True
    assert simulation["matches"][0]["fallback_used"] is False
    assert simulation["matches"][1]["actual_result"] is False
    assert simulation["error_count"] == 0


def test_completed_knockout_result_resolves_later_placeholder() -> None:
    fixtures = [
        {
            "match_id": "2026-073",
            "match_number": 73,
            "stage": "round_of_32",
            "team_a": "Mexico",
            "team_b": "Canada",
            "score_a": 1,
            "score_b": 1,
            "winner": "Canada",
            "status": "completed",
        },
        {
            "match_id": "2026-089",
            "match_number": 89,
            "stage": "round_of_16",
            "team_a": None,
            "team_b": "Brazil",
            "team_a_placeholder": "Winner 73",
            "team_b_placeholder": None,
            "score_a": None,
            "score_b": None,
            "winner": None,
            "status": "scheduled",
        },
    ]
    calls = []

    async def fixed_prediction(fixture: dict[str, Any], previous_results: list[dict[str, Any]]) -> dict[str, Any]:
        calls.append((dict(fixture), [dict(result) for result in previous_results]))
        return {
            "predicted_score_a": 2,
            "predicted_score_b": 0,
            "predicted_winner": fixture["team_a"],
            "confidence": 0.8,
        }

    simulation = asyncio.run(
        simulate_contestant(
            {"id": "fixed", "name": "Fixed", "url": "http://example.test/predict", "status": "active"},
            fixtures,
            {},
            SimulationConfig(timeout_seconds=0.1, retries=0),
            fixed_prediction,
        )
    )

    assert len(calls) == 1
    assert calls[0][0]["match_number"] == 89
    assert calls[0][0]["team_a"] == "Canada"
    assert calls[0][1][0]["winner"] == "Canada"
    assert simulation["matches"][0]["winner"] == "Canada"
    assert simulation["matches"][0]["loser"] == "Mexico"
    assert simulation["matches"][0]["actual_result"] is True
    assert simulation["matches"][1]["team_a"] == "Canada"


def test_simulation_uses_fallbacks_for_invalid_responses() -> None:
    fixtures, groups = import_world_cup_schedule(WORKBOOK)

    async def invalid_prediction(fixture: dict[str, Any], previous_results: list[dict[str, Any]]) -> dict[str, Any]:
        return {"predicted_score_a": -1}

    simulation = asyncio.run(
        simulate_contestant(
            {"id": "bad", "name": "Bad", "url": "http://example.test/predict", "status": "active"},
            fixtures,
            groups,
            SimulationConfig(timeout_seconds=0.1, retries=0),
            invalid_prediction,
        )
    )

    assert simulation["status"] == "completed_with_fallbacks"
    assert simulation["error_count"] == 104
    assert all(match["fallback_used"] for match in simulation["matches"])
    assert simulation["champion"]
