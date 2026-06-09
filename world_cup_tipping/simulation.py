from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

import httpx

from .models import isoformat_z, result_key, utc_now
from .scoring import validate_prediction


PredictionClient = Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[dict[str, Any]]]

RANK_PLACEHOLDER_RE = re.compile(r"^([123])([A-L])$")
MATCH_PLACEHOLDER_RE = re.compile(r"^(Winner|Loser) ([0-9]+)$")


@dataclass(frozen=True)
class SimulationConfig:
    timeout_seconds: float = 5.0
    retries: int = 1
    consecutive_error_limit: int = 3


async def simulate_contestant(
    contestant: dict[str, Any],
    fixtures: list[dict[str, Any]],
    groups: dict[str, list[str]],
    config: SimulationConfig | None = None,
    prediction_client: PredictionClient | None = None,
) -> dict[str, Any]:
    config = config or SimulationConfig()
    sorted_fixtures = sorted(fixtures, key=lambda item: item["match_number"])
    standings = _initial_standings(groups)
    group_rankings: dict[str, list[dict[str, Any]]] = {}
    outcomes: dict[int, dict[str, Any]] = {}
    previous_results: list[dict[str, Any]] = []
    simulated_matches: list[dict[str, Any]] = []
    used_third_groups: set[str] = set()
    consecutive_errors = 0

    if prediction_client is None:
        prediction_client = _http_prediction_client(contestant, config)

    for fixture in sorted_fixtures:
        simulation_fixture = _resolved_fixture(fixture, group_rankings, outcomes, used_third_groups)
        if consecutive_errors >= config.consecutive_error_limit:
            raw_response = None
            error = f"Simulation fallback after {consecutive_errors} consecutive invalid or failed predictions"
        else:
            raw_response, error = await _call_prediction(prediction_client, simulation_fixture, previous_results, config)
        valid = False
        prediction: dict[str, Any] | None = None
        if raw_response is not None:
            valid, prediction, validation_error = validate_prediction(simulation_fixture, raw_response)
            error = validation_error or error

        match = _simulated_match(simulation_fixture, prediction, valid, error, raw_response)
        consecutive_errors = 0 if match["valid"] else consecutive_errors + 1
        simulated_matches.append(match)
        previous_results.append(_result_payload(match))
        outcomes[match["match_number"]] = {
            "winner": match["winner"],
            "loser": match["loser"],
        }

        if match["stage"] == "group":
            _apply_group_result(standings, match)
            if fixture["match_number"] == 72:
                group_rankings = _rank_groups(standings)

    error_count = sum(1 for match in simulated_matches if not match["valid"])
    final_match = next((match for match in simulated_matches if match["match_number"] == 104), None)
    third_place_match = next((match for match in simulated_matches if match["match_number"] == 103), None)
    status = "completed_with_fallbacks" if error_count else "completed"
    return {
        "id": str(uuid4()),
        "contestant_id": contestant["id"],
        "contestant_name": contestant.get("name", contestant["id"]),
        "simulated_at": isoformat_z(utc_now()),
        "status": status,
        "error_count": error_count,
        "champion": final_match["winner"] if final_match else None,
        "runner_up": final_match["loser"] if final_match else None,
        "third_place": third_place_match["winner"] if third_place_match else None,
        "fourth_place": third_place_match["loser"] if third_place_match else None,
        "group_standings": group_rankings,
        "matches": simulated_matches,
        "bracket": _bracket_by_stage(simulated_matches),
    }


def _http_prediction_client(contestant: dict[str, Any], config: SimulationConfig) -> PredictionClient:
    async def client(fixture: dict[str, Any], previous_results: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "match_id": fixture["match_id"],
            "stage": fixture["stage"],
            "team_a": fixture["team_a"],
            "team_b": fixture["team_b"],
            "previous_results": previous_results,
        }
        timeout = httpx.Timeout(config.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            response = await http_client.post(contestant_url(contestant), json=payload)
            response.raise_for_status()
            return response.json()

    return client


def contestant_url(contestant: dict[str, Any]) -> str:
    return str(contestant["url"])


async def _call_prediction(
    prediction_client: PredictionClient,
    fixture: dict[str, Any],
    previous_results: list[dict[str, Any]],
    config: SimulationConfig,
) -> tuple[dict[str, Any] | None, str | None]:
    error: str | None = None
    for attempt in range(config.retries + 1):
        try:
            return await prediction_client(fixture, previous_results), None
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            if attempt < config.retries:
                await asyncio.sleep(0.2)
    return None, error


def _initial_standings(groups: dict[str, list[str]]) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        group: {
            team: {
                "team": team,
                "group": group,
                "played": 0,
                "wins": 0,
                "draws": 0,
                "losses": 0,
                "goals_for": 0,
                "goals_against": 0,
                "goal_difference": 0,
                "points": 0,
            }
            for team in teams
        }
        for group, teams in groups.items()
    }


def _apply_group_result(standings: dict[str, dict[str, dict[str, Any]]], match: dict[str, Any]) -> None:
    group = match.get("group")
    if not group or group not in standings:
        return
    team_a = standings[group].get(match["team_a"])
    team_b = standings[group].get(match["team_b"])
    if team_a is None or team_b is None:
        return

    score_a = match["score_a"]
    score_b = match["score_b"]
    team_a["played"] += 1
    team_b["played"] += 1
    team_a["goals_for"] += score_a
    team_a["goals_against"] += score_b
    team_b["goals_for"] += score_b
    team_b["goals_against"] += score_a

    if score_a > score_b:
        team_a["wins"] += 1
        team_a["points"] += 3
        team_b["losses"] += 1
    elif score_b > score_a:
        team_b["wins"] += 1
        team_b["points"] += 3
        team_a["losses"] += 1
    else:
        team_a["draws"] += 1
        team_b["draws"] += 1
        team_a["points"] += 1
        team_b["points"] += 1

    team_a["goal_difference"] = team_a["goals_for"] - team_a["goals_against"]
    team_b["goal_difference"] = team_b["goals_for"] - team_b["goals_against"]


def _rank_groups(standings: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    ranked_groups = {}
    for group, teams in standings.items():
        ranked = sorted(teams.values(), key=_team_sort_key)
        ranked_groups[group] = [dict(row, rank=index + 1) for index, row in enumerate(ranked)]
    return ranked_groups


def _team_sort_key(team: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        -int(team["points"]),
        -int(team["goal_difference"]),
        -int(team["goals_for"]),
        str(team["team"]).lower(),
    )


def _resolved_fixture(
    fixture: dict[str, Any],
    group_rankings: dict[str, list[dict[str, Any]]],
    outcomes: dict[int, dict[str, Any]],
    used_third_groups: set[str],
) -> dict[str, Any]:
    resolved = dict(fixture)
    resolved["team_a"] = _resolve_team(fixture.get("team_a") or fixture.get("team_a_placeholder"), group_rankings, outcomes, used_third_groups)
    resolved["team_b"] = _resolve_team(fixture.get("team_b") or fixture.get("team_b_placeholder"), group_rankings, outcomes, used_third_groups)
    return resolved


def _resolve_team(
    placeholder: Any,
    group_rankings: dict[str, list[dict[str, Any]]],
    outcomes: dict[int, dict[str, Any]],
    used_third_groups: set[str],
) -> str | None:
    if placeholder is None:
        return None
    value = str(placeholder)
    rank_match = RANK_PLACEHOLDER_RE.match(value)
    if rank_match:
        rank = int(rank_match.group(1))
        group = rank_match.group(2)
        return _ranked_team(group_rankings, group, rank)

    match_placeholder = MATCH_PLACEHOLDER_RE.match(value)
    if match_placeholder:
        side = match_placeholder.group(1).lower()
        match_number = int(match_placeholder.group(2))
        return outcomes.get(match_number, {}).get(side)

    if value.startswith("3rd Group "):
        return _resolve_third_place_slot(value, group_rankings, used_third_groups)

    return value


def _ranked_team(group_rankings: dict[str, list[dict[str, Any]]], group: str, rank: int) -> str | None:
    rows = group_rankings.get(group, [])
    if len(rows) < rank:
        return None
    return rows[rank - 1]["team"]


def _resolve_third_place_slot(
    placeholder: str,
    group_rankings: dict[str, list[dict[str, Any]]],
    used_third_groups: set[str],
) -> str | None:
    eligible_groups = _eligible_third_groups(placeholder)
    for row in _best_third_place_rows(group_rankings):
        group = row["group"]
        if group in used_third_groups:
            continue
        if group in eligible_groups:
            used_third_groups.add(group)
            return row["team"]
    for row in _best_third_place_rows(group_rankings):
        group = row["group"]
        if group not in used_third_groups:
            used_third_groups.add(group)
            return row["team"]
    return None


def _eligible_third_groups(placeholder: str) -> set[str]:
    _, _, suffix = placeholder.partition("3rd Group ")
    return {group.strip() for group in suffix.split("/") if group.strip()}


def _best_third_place_rows(group_rankings: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    rows = [ranking[2] for ranking in group_rankings.values() if len(ranking) >= 3]
    return sorted(rows, key=_team_sort_key)[:8]


def _simulated_match(
    fixture: dict[str, Any],
    prediction: dict[str, Any] | None,
    valid: bool,
    error: str | None,
    raw_response: dict[str, Any] | None,
) -> dict[str, Any]:
    fallback_used = not valid or prediction is None
    if fallback_used:
        prediction = _fallback_prediction(fixture)

    score_a = prediction["predicted_score_a"]
    score_b = prediction["predicted_score_b"]
    winner = _predicted_match_winner(fixture, prediction)
    loser = _loser(fixture, winner)
    return {
        "match_id": fixture["match_id"],
        "match_number": fixture["match_number"],
        "stage": fixture["stage"],
        "group": fixture.get("group"),
        "team_a": fixture.get("team_a"),
        "team_b": fixture.get("team_b"),
        "score_a": score_a,
        "score_b": score_b,
        "winner": winner,
        "loser": loser,
        "predicted_winner": prediction.get("predicted_winner"),
        "confidence": prediction.get("confidence"),
        "valid": valid,
        "fallback_used": fallback_used,
        "error": error,
        "raw_response": raw_response,
    }


def _fallback_prediction(fixture: dict[str, Any]) -> dict[str, Any]:
    winner = None
    if fixture["stage"] != "group":
        winner = fixture.get("team_a")
    return {
        "predicted_score_a": 0,
        "predicted_score_b": 0,
        "predicted_winner": winner,
        "confidence": 0.0,
    }


def _predicted_match_winner(fixture: dict[str, Any], prediction: dict[str, Any]) -> str | None:
    if fixture["stage"] != "group":
        return prediction.get("predicted_winner") or fixture.get("team_a")

    outcome = result_key(prediction["predicted_score_a"], prediction["predicted_score_b"])
    if outcome == "team_a":
        return fixture.get("team_a")
    if outcome == "team_b":
        return fixture.get("team_b")
    return None


def _loser(fixture: dict[str, Any], winner: str | None) -> str | None:
    if winner is None:
        return None
    if winner == fixture.get("team_a"):
        return fixture.get("team_b")
    if winner == fixture.get("team_b"):
        return fixture.get("team_a")
    return None


def _result_payload(match: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_id": match["match_id"],
        "stage": match["stage"],
        "team_a": match["team_a"],
        "team_b": match["team_b"],
        "score_a": match["score_a"],
        "score_b": match["score_b"],
        "winner": match["winner"],
    }


def _bracket_by_stage(matches: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    stages = ["round_of_32", "round_of_16", "quarterfinal", "semifinal", "third_place", "final"]
    return {
        stage: [match for match in matches if match["stage"] == stage]
        for stage in stages
    }
