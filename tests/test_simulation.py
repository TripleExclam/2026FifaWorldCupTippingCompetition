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
