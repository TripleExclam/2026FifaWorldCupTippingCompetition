#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_DATA_DIR = Path("/var/lib/world-cup-tipping/data")
COMPLETED_SIMULATION_STATUSES = {"completed", "completed_with_fallbacks"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyse group-stage tipping simulations.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--html-out", type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    data_dir = args.data_dir
    html_out = args.html_out or data_dir / "group_stage_analysis.html"
    json_out = args.json_out or data_dir / "group_stage_analysis.json"

    analysis = build_analysis(data_dir)
    json_out.write_text(json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_out.write_text(render_html(analysis), encoding="utf-8")

    print(f"Wrote {html_out}")
    print(f"Wrote {json_out}")
    print(
        "Analysed "
        f"{analysis['summary']['entrant_count']} entrants, "
        f"{analysis['summary']['group_match_count']} group matches, "
        f"{analysis['summary']['matches_with_result_disagreement']} matches with result disagreement."
    )


def build_analysis(data_dir: Path) -> dict[str, Any]:
    fixtures = load_json(data_dir / "fixtures.json")
    groups = load_json(data_dir / "groups.json")
    registry = load_json(data_dir / "registry.json")
    predictions = load_json(data_dir / "predictions.json")
    simulations = load_json(data_dir / "simulations.json")

    active_registry = [row for row in registry if row.get("status", "active") == "active"]
    active_ids = {row["id"] for row in active_registry}
    registry_by_id = {row["id"]: row for row in registry}
    latest_simulations = latest_completed_simulations(simulations, active_ids)
    group_fixtures = sorted(
        [fixture for fixture in fixtures if fixture.get("stage") == "group"],
        key=lambda item: int(item["match_number"]),
    )
    fixture_by_id = {fixture["match_id"]: fixture for fixture in fixtures}

    entrants = []
    matches_by_entrant: dict[str, dict[str, dict[str, Any]]] = {}
    group_tables_by_entrant: dict[str, dict[str, list[dict[str, Any]]]] = {}
    third_qualifiers_by_entrant: dict[str, set[str]] = {}
    qualifiers_by_entrant: dict[str, list[dict[str, str]]] = {}

    for contestant in active_registry:
        contestant_id = contestant["id"]
        simulation = latest_simulations.get(contestant_id)
        if simulation is None:
            entrants.append(
                {
                    "contestant_id": contestant_id,
                    "name": contestant.get("name", contestant_id),
                    "has_simulation": False,
                    "registry_status": contestant.get("status", "active"),
                }
            )
            continue

        group_matches = [match for match in simulation.get("matches", []) if match.get("stage") == "group"]
        matches_by_entrant[contestant_id] = {match["match_id"]: match for match in group_matches}
        group_tables = normalize_group_tables(simulation.get("group_standings", {}))
        group_tables_by_entrant[contestant_id] = group_tables
        third_qualifiers = {row["team"] for row in best_third_place_rows(group_tables)}
        third_qualifiers_by_entrant[contestant_id] = third_qualifiers
        qualifiers_by_entrant[contestant_id] = entrant_qualifiers(group_tables, third_qualifiers)

        outcome_counts = Counter(result_key(match["score_a"], match["score_b"]) for match in group_matches)
        total_goals = sum(int(match["score_a"]) + int(match["score_b"]) for match in group_matches)
        entrants.append(
            {
                "contestant_id": contestant_id,
                "name": contestant.get("name", simulation.get("contestant_name", contestant_id)),
                "has_simulation": True,
                "simulated_at": simulation.get("simulated_at"),
                "simulation_status": simulation.get("status"),
                "error_count": int(simulation.get("error_count", 0)),
                "group_fallbacks": sum(1 for match in group_matches if match.get("fallback_used")),
                "group_invalid": sum(1 for match in group_matches if not match.get("valid", True)),
                "group_matches": len(group_matches),
                "team_a_wins": outcome_counts.get("team_a", 0),
                "draws": outcome_counts.get("draw", 0),
                "team_b_wins": outcome_counts.get("team_b", 0),
                "avg_total_goals": round(total_goals / len(group_matches), 2) if group_matches else 0,
                "champion": simulation.get("champion"),
                "runner_up": simulation.get("runner_up"),
            }
        )

    simulated_entrants = [entry for entry in entrants if entry.get("has_simulation")]
    group_match_analysis = analyse_group_matches(group_fixtures, simulated_entrants, matches_by_entrant)
    qualification = analyse_qualification(
        groups,
        simulated_entrants,
        group_tables_by_entrant,
        third_qualifiers_by_entrant,
        qualifiers_by_entrant,
    )
    agreement = analyse_agreement(group_fixtures, simulated_entrants, matches_by_entrant)
    submitted_predictions = analyse_submitted_predictions(
        predictions,
        group_fixtures,
        fixture_by_id,
        registry_by_id,
        active_registry,
    )

    top_disagreements = sorted(
        group_match_analysis,
        key=lambda row: (
            -float(row["disagreement_score"]),
            -int(row["unique_scorelines"]),
            int(row["match_number"]),
        ),
    )[:12]
    mixed_advancement = sorted(
        [
            row
            for row in qualification["teams"]
            if 0 < int(row["advance_count"]) < len(simulated_entrants)
        ],
        key=lambda row: (
            abs((int(row["advance_count"]) / max(len(simulated_entrants), 1)) - 0.5),
            row["group"],
            row["team"],
        ),
    )[:12]
    unanimous_matches = sum(1 for row in group_match_analysis if int(row["unique_outcomes"]) == 1)
    disagreement_matches = len(group_match_analysis) - unanimous_matches
    no_majority_matches = sum(
        1
        for row in group_match_analysis
        if int(row["top_outcome_count"]) <= len(simulated_entrants) // 2
    )
    fallback_entrants = [
        {
            "contestant_id": entry["contestant_id"],
            "name": entry["name"],
            "group_fallbacks": entry.get("group_fallbacks", 0),
            "error_count": entry.get("error_count", 0),
        }
        for entry in simulated_entrants
        if entry.get("group_fallbacks", 0) or entry.get("error_count", 0)
    ]

    return {
        "generated_at": iso_now(),
        "source_data_dir": str(data_dir),
        "summary": {
            "entrant_count": len(simulated_entrants),
            "active_registry_count": len(active_registry),
            "group_match_count": len(group_fixtures),
            "matches_with_result_disagreement": disagreement_matches,
            "unanimous_result_matches": unanimous_matches,
            "no_majority_result_matches": no_majority_matches,
            "average_disagreement_score": round(
                sum(float(row["disagreement_score"]) for row in group_match_analysis) / len(group_match_analysis),
                3,
            )
            if group_match_analysis
            else 0,
            "teams_with_mixed_advancement": len(
                [
                    row
                    for row in qualification["teams"]
                    if 0 < int(row["advance_count"]) < len(simulated_entrants)
                ]
            ),
            "submitted_prediction_matches": len(submitted_predictions),
            "fallback_entrants": fallback_entrants,
        },
        "entrants": entrants,
        "matches": group_match_analysis,
        "top_disagreements": top_disagreements,
        "qualification": qualification,
        "mixed_advancement": mixed_advancement,
        "agreement": agreement,
        "submitted_predictions": submitted_predictions,
    }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_completed_simulations(
    simulations: list[dict[str, Any]],
    active_ids: set[str],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for simulation in simulations:
        contestant_id = simulation.get("contestant_id")
        if contestant_id not in active_ids:
            continue
        if simulation.get("status") not in COMPLETED_SIMULATION_STATUSES:
            continue
        existing = latest.get(contestant_id)
        if existing is None or str(simulation.get("simulated_at", "")) > str(existing.get("simulated_at", "")):
            latest[contestant_id] = simulation
    return latest


def normalize_group_tables(raw_tables: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    for group, rows in raw_tables.items():
        tables[group] = sorted((dict(row) for row in rows), key=lambda row: int(row.get("rank", 99)))
    return tables


def result_key(score_a: int, score_b: int) -> str:
    if int(score_a) > int(score_b):
        return "team_a"
    if int(score_b) > int(score_a):
        return "team_b"
    return "draw"


def result_label(fixture: dict[str, Any], key: str) -> str:
    if key == "team_a":
        return str(fixture["team_a"])
    if key == "team_b":
        return str(fixture["team_b"])
    return "Draw"


def team_sort_key(row: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        -int(row.get("points", 0)),
        -int(row.get("goal_difference", 0)),
        -int(row.get("goals_for", 0)),
        str(row.get("team", "")).lower(),
    )


def best_third_place_rows(group_tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    third_rows = [rows[2] for rows in group_tables.values() if len(rows) >= 3]
    return sorted(third_rows, key=team_sort_key)[:8]


def entrant_qualifiers(
    group_tables: dict[str, list[dict[str, Any]]],
    third_qualifiers: set[str],
) -> list[dict[str, str]]:
    qualifiers = []
    for group in sorted(group_tables):
        for row in group_tables[group]:
            rank = int(row.get("rank", 99))
            team = str(row["team"])
            if rank <= 2:
                route = "winner" if rank == 1 else "runner_up"
            elif rank == 3 and team in third_qualifiers:
                route = "third_place"
            else:
                continue
            qualifiers.append({"group": group, "team": team, "route": route})
    return qualifiers


def analyse_group_matches(
    group_fixtures: list[dict[str, Any]],
    entrants: list[dict[str, Any]],
    matches_by_entrant: dict[str, dict[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    analysed = []
    entrant_count = len(entrants)
    for fixture in group_fixtures:
        outcome_counts: Counter[str] = Counter()
        scoreline_counts: Counter[str] = Counter()
        entrants_by_outcome: dict[str, list[str]] = defaultdict(list)
        predictions = []
        for entrant in entrants:
            contestant_id = entrant["contestant_id"]
            match = matches_by_entrant.get(contestant_id, {}).get(fixture["match_id"])
            if match is None:
                continue
            key = result_key(match["score_a"], match["score_b"])
            scoreline = f"{match['score_a']}-{match['score_b']}"
            outcome_counts[key] += 1
            scoreline_counts[scoreline] += 1
            entrants_by_outcome[key].append(entrant["name"])
            predictions.append(
                {
                    "contestant_id": contestant_id,
                    "name": entrant["name"],
                    "scoreline": scoreline,
                    "outcome_key": key,
                    "outcome_label": result_label(fixture, key),
                    "confidence": match.get("confidence"),
                    "fallback_used": bool(match.get("fallback_used")),
                    "valid": bool(match.get("valid", True)),
                }
            )

        top_key, top_count = most_common_or_empty(outcome_counts)
        unique_outcomes = len(outcome_counts)
        disagreement_score = 1 - (top_count / entrant_count) if entrant_count else 0
        entropy = normalized_entropy(outcome_counts, 3)
        analysed.append(
            {
                "match_id": fixture["match_id"],
                "match_number": fixture["match_number"],
                "group": fixture.get("group"),
                "kickoff_at": fixture.get("kickoff_at"),
                "team_a": fixture.get("team_a"),
                "team_b": fixture.get("team_b"),
                "outcome_counts": [
                    {
                        "key": key,
                        "label": result_label(fixture, key),
                        "count": outcome_counts.get(key, 0),
                        "share": round(outcome_counts.get(key, 0) / entrant_count, 3) if entrant_count else 0,
                        "entrants": sorted(entrants_by_outcome.get(key, []), key=str.lower),
                    }
                    for key in ["team_a", "draw", "team_b"]
                    if outcome_counts.get(key, 0) > 0
                ],
                "top_outcome_key": top_key,
                "top_outcome_label": result_label(fixture, top_key) if top_key else "",
                "top_outcome_count": top_count,
                "unique_outcomes": unique_outcomes,
                "unique_scorelines": len(scoreline_counts),
                "top_scorelines": [
                    {"scoreline": scoreline, "count": count}
                    for scoreline, count in scoreline_counts.most_common(5)
                ],
                "disagreement_score": round(disagreement_score, 3),
                "entropy": round(entropy, 3),
                "predictions": sorted(predictions, key=lambda row: row["name"].lower()),
            }
        )
    return analysed


def most_common_or_empty(counter: Counter[str]) -> tuple[str, int]:
    if not counter:
        return "", 0
    return counter.most_common(1)[0]


def normalized_entropy(counter: Counter[str], possible_outcomes: int) -> float:
    total = sum(counter.values())
    if not total or possible_outcomes <= 1:
        return 0
    entropy = 0.0
    for count in counter.values():
        probability = count / total
        entropy -= probability * math.log(probability)
    return entropy / math.log(possible_outcomes)


def analyse_qualification(
    groups: dict[str, list[str]],
    entrants: list[dict[str, Any]],
    group_tables_by_entrant: dict[str, dict[str, list[dict[str, Any]]]],
    third_qualifiers_by_entrant: dict[str, set[str]],
    qualifiers_by_entrant: dict[str, list[dict[str, str]]],
) -> dict[str, Any]:
    entrant_count = len(entrants)
    team_stats: dict[str, dict[str, Any]] = {}
    for group, teams in sorted(groups.items()):
        for team in teams:
            team_stats[team] = {
                "group": group,
                "team": team,
                "rank_counts": {str(rank): 0 for rank in range(1, 5)},
                "win_group_count": 0,
                "top_two_count": 0,
                "third_place_advance_count": 0,
                "advance_count": 0,
                "eliminated_count": 0,
                "entrant_outcomes": [],
            }

    group_winners: dict[str, Counter[str]] = {group: Counter() for group in groups}
    group_qualifiers: dict[str, dict[str, Counter[str]]] = {
        group: {team: Counter() for team in teams}
        for group, teams in groups.items()
    }

    for entrant in entrants:
        contestant_id = entrant["contestant_id"]
        group_tables = group_tables_by_entrant.get(contestant_id, {})
        third_qualifiers = third_qualifiers_by_entrant.get(contestant_id, set())
        advanced = {row["team"] for row in qualifiers_by_entrant.get(contestant_id, [])}
        for group, rows in group_tables.items():
            if rows:
                group_winners.setdefault(group, Counter())[str(rows[0]["team"])] += 1
            for row in rows:
                team = str(row["team"])
                rank = int(row.get("rank", 0))
                stats = team_stats.setdefault(
                    team,
                    {
                        "group": group,
                        "team": team,
                        "rank_counts": {str(rank): 0 for rank in range(1, 5)},
                        "win_group_count": 0,
                        "top_two_count": 0,
                        "third_place_advance_count": 0,
                        "advance_count": 0,
                        "eliminated_count": 0,
                        "entrant_outcomes": [],
                    },
                )
                stats["rank_counts"][str(rank)] = int(stats["rank_counts"].get(str(rank), 0)) + 1
                if rank == 1:
                    stats["win_group_count"] += 1
                    group_qualifiers.setdefault(group, {}).setdefault(team, Counter())["winner"] += 1
                if rank <= 2:
                    stats["top_two_count"] += 1
                    if rank == 2:
                        group_qualifiers.setdefault(group, {}).setdefault(team, Counter())["runner_up"] += 1
                if rank == 3 and team in third_qualifiers:
                    stats["third_place_advance_count"] += 1
                    group_qualifiers.setdefault(group, {}).setdefault(team, Counter())["third_place"] += 1
                if team in advanced:
                    stats["advance_count"] += 1
                    advanced_label = "advance"
                else:
                    stats["eliminated_count"] += 1
                    advanced_label = "eliminated"
                stats["entrant_outcomes"].append(
                    {
                        "contestant_id": contestant_id,
                        "name": entrant["name"],
                        "rank": rank,
                        "advanced": advanced_label,
                    }
                )

    teams = []
    for team, stats in team_stats.items():
        rank_counts = stats["rank_counts"]
        possible_ranks = [
            rank
            for rank in range(1, 5)
            if int(rank_counts.get(str(rank), 0)) > 0
        ]
        row = dict(stats)
        row["advance_share"] = round(row["advance_count"] / entrant_count, 3) if entrant_count else 0
        row["top_two_share"] = round(row["top_two_count"] / entrant_count, 3) if entrant_count else 0
        row["possible_ranks"] = possible_ranks
        teams.append(row)

    return {
        "entrant_count": entrant_count,
        "teams": sorted(teams, key=lambda row: (row["group"], row["team"])),
        "group_winners": [
            {
                "group": group,
                "winners": [
                    {
                        "team": team,
                        "count": count,
                        "share": round(count / entrant_count, 3) if entrant_count else 0,
                    }
                    for team, count in counter.most_common()
                ],
            }
            for group, counter in sorted(group_winners.items())
        ],
        "group_qualifiers": [
            {
                "group": group,
                "teams": [
                    {
                        "team": team,
                        "winner": routes.get("winner", 0),
                        "runner_up": routes.get("runner_up", 0),
                        "third_place": routes.get("third_place", 0),
                        "advance": routes.get("winner", 0)
                        + routes.get("runner_up", 0)
                        + routes.get("third_place", 0),
                    }
                    for team, routes in sorted(team_counters.items())
                ],
            }
            for group, team_counters in sorted(group_qualifiers.items())
        ],
        "entrant_tables": [
            {
                "contestant_id": entrant["contestant_id"],
                "name": entrant["name"],
                "tables": group_tables_by_entrant.get(entrant["contestant_id"], {}),
                "qualifiers": qualifiers_by_entrant.get(entrant["contestant_id"], []),
                "third_qualifiers": sorted(third_qualifiers_by_entrant.get(entrant["contestant_id"], set())),
            }
            for entrant in entrants
        ],
    }


def analyse_agreement(
    group_fixtures: list[dict[str, Any]],
    entrants: list[dict[str, Any]],
    matches_by_entrant: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    match_ids = [fixture["match_id"] for fixture in group_fixtures]
    rows = []
    pairs = []
    for entrant in entrants:
        cells = []
        for other in entrants:
            result_agree = 0
            exact_agree = 0
            compared = 0
            for match_id in match_ids:
                match = matches_by_entrant.get(entrant["contestant_id"], {}).get(match_id)
                other_match = matches_by_entrant.get(other["contestant_id"], {}).get(match_id)
                if match is None or other_match is None:
                    continue
                compared += 1
                if result_key(match["score_a"], match["score_b"]) == result_key(other_match["score_a"], other_match["score_b"]):
                    result_agree += 1
                if int(match["score_a"]) == int(other_match["score_a"]) and int(match["score_b"]) == int(other_match["score_b"]):
                    exact_agree += 1
            pct = result_agree / compared if compared else 0
            exact_pct = exact_agree / compared if compared else 0
            cell = {
                "contestant_id": other["contestant_id"],
                "name": other["name"],
                "result_agreement_count": result_agree,
                "result_agreement_pct": round(pct, 3),
                "exact_score_agreement_pct": round(exact_pct, 3),
            }
            cells.append(cell)
            if entrant["contestant_id"] < other["contestant_id"]:
                pairs.append(
                    {
                        "a": entrant["name"],
                        "b": other["name"],
                        "result_agreement_count": result_agree,
                        "result_agreement_pct": round(pct, 3),
                        "exact_score_agreement_pct": round(exact_pct, 3),
                    }
                )
        rows.append(
            {
                "contestant_id": entrant["contestant_id"],
                "name": entrant["name"],
                "cells": cells,
            }
        )

    pairs_sorted = sorted(pairs, key=lambda row: row["result_agreement_pct"])
    return {
        "match_count": len(match_ids),
        "matrix": rows,
        "least_aligned_pairs": pairs_sorted[:8],
        "most_aligned_pairs": list(reversed(pairs_sorted[-8:])),
    }


def analyse_submitted_predictions(
    predictions: list[dict[str, Any]],
    group_fixtures: list[dict[str, Any]],
    fixture_by_id: dict[str, dict[str, Any]],
    registry_by_id: dict[str, dict[str, Any]],
    active_registry: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    active_ids = {row["id"] for row in active_registry}
    group_match_ids = {fixture["match_id"] for fixture in group_fixtures}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for prediction in predictions:
        if prediction.get("contestant_id") not in active_ids:
            continue
        if prediction.get("match_id") in group_match_ids:
            grouped[prediction["match_id"]].append(prediction)

    analysed = []
    active_count = len(active_registry)
    for match_id, rows in sorted(grouped.items(), key=lambda item: int(fixture_by_id[item[0]]["match_number"])):
        fixture = fixture_by_id[match_id]
        outcome_counts: Counter[str] = Counter()
        scoreline_counts: Counter[str] = Counter()
        prediction_rows = []
        for row in rows:
            contestant = registry_by_id.get(row["contestant_id"], {})
            prediction = row.get("prediction") or {}
            if not row.get("valid") or "predicted_score_a" not in prediction or "predicted_score_b" not in prediction:
                prediction_rows.append(
                    {
                        "contestant_id": row["contestant_id"],
                        "name": contestant.get("name", row["contestant_id"]),
                        "valid": False,
                        "error": row.get("error"),
                    }
                )
                continue
            key = result_key(prediction["predicted_score_a"], prediction["predicted_score_b"])
            scoreline = f"{prediction['predicted_score_a']}-{prediction['predicted_score_b']}"
            outcome_counts[key] += 1
            scoreline_counts[scoreline] += 1
            prediction_rows.append(
                {
                    "contestant_id": row["contestant_id"],
                    "name": contestant.get("name", row["contestant_id"]),
                    "valid": True,
                    "scoreline": scoreline,
                    "outcome_key": key,
                    "outcome_label": result_label(fixture, key),
                    "confidence": prediction.get("confidence"),
                    "requested_at": row.get("requested_at"),
                }
            )

        top_key, top_count = most_common_or_empty(outcome_counts)
        analysed.append(
            {
                "match_id": match_id,
                "match_number": fixture["match_number"],
                "group": fixture.get("group"),
                "team_a": fixture.get("team_a"),
                "team_b": fixture.get("team_b"),
                "prediction_count": len(rows),
                "missing_count": max(active_count - len(rows), 0),
                "top_outcome_key": top_key,
                "top_outcome_label": result_label(fixture, top_key) if top_key else "",
                "top_outcome_count": top_count,
                "outcome_counts": [
                    {
                        "key": key,
                        "label": result_label(fixture, key),
                        "count": outcome_counts.get(key, 0),
                    }
                    for key in ["team_a", "draw", "team_b"]
                    if outcome_counts.get(key, 0) > 0
                ],
                "top_scorelines": [
                    {"scoreline": scoreline, "count": count}
                    for scoreline, count in scoreline_counts.most_common(5)
                ],
                "predictions": sorted(prediction_rows, key=lambda item: item["name"].lower()),
            }
        )
    return analysed


def iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def render_html(analysis: dict[str, Any]) -> str:
    data = json.dumps(analysis, ensure_ascii=False)
    safe_data = (
        data.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    title = "World Cup Tipping - Group Stage Analysis"
    generated = html.escape(str(analysis["generated_at"]))
    source = html.escape(str(analysis["source_data_dir"]))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172026;
      --muted: #61717c;
      --line: #d9e1e5;
      --panel: #ffffff;
      --page: #f5f7f4;
      --green: #24745b;
      --amber: #b66d1c;
      --red: #b94d45;
      --blue: #2d63a3;
      --purple: #7357a6;
      --teal: #2f817d;
      --shadow: 0 10px 30px rgba(23, 32, 38, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--page);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    header {{
      background: #102127;
      color: #f8fbf9;
      padding: 28px clamp(18px, 4vw, 48px);
      border-bottom: 6px solid #e3b449;
    }}
    header h1 {{
      margin: 6px 0 8px;
      font-size: clamp(28px, 4vw, 48px);
      letter-spacing: 0;
    }}
    header p {{ max-width: 920px; margin: 0; color: #c5d2d1; }}
    main {{
      width: min(1500px, 100%);
      margin: 0 auto;
      padding: 24px clamp(14px, 3vw, 34px) 56px;
    }}
    h2 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: 0;
    }}
    h3 {{
      margin: 0;
      font-size: 16px;
      letter-spacing: 0;
    }}
    .kicker {{
      text-transform: uppercase;
      font-size: 12px;
      letter-spacing: .08em;
      color: #e3b449;
      font-weight: 800;
    }}
    .section {{
      margin-top: 28px;
    }}
    .section-head {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 12px;
    }}
    .section-head p {{
      margin: 4px 0 0;
      color: var(--muted);
      max-width: 860px;
    }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .metric, .panel, .match-card, .group-card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }}
    .metric {{
      padding: 14px;
      min-height: 96px;
    }}
    .metric .value {{
      font-size: 30px;
      font-weight: 850;
      margin-bottom: 2px;
    }}
    .metric .label, .muted {{
      color: var(--muted);
      font-size: 13px;
    }}
    .panel {{
      padding: 16px;
      overflow: auto;
    }}
    .toolbar {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
      margin-bottom: 12px;
    }}
    button, select, input {{
      border: 1px solid var(--line);
      border-radius: 6px;
      min-height: 34px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      padding: 6px 10px;
    }}
    button {{
      cursor: pointer;
      font-weight: 700;
    }}
    button.active {{
      color: #fff;
      background: #1d5963;
      border-color: #1d5963;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }}
    th, td {{
      padding: 8px 9px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      font-size: 12px;
      color: #41515a;
      background: #eef3f1;
      position: sticky;
      top: 0;
      z-index: 1;
    }}
    .number {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}
    .match-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 12px;
    }}
    .match-card {{
      padding: 14px;
      min-width: 0;
    }}
    .match-title {{
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: start;
      margin-bottom: 10px;
    }}
    .match-title strong {{
      display: block;
      font-size: 15px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .badge.green {{ color: #0f513d; background: #dceee6; }}
    .badge.amber {{ color: #7a4300; background: #f7e5c9; }}
    .badge.red {{ color: #7d211a; background: #f3d7d4; }}
    .badge.blue {{ color: #153e6c; background: #dbe8f7; }}
    .bars {{
      display: grid;
      gap: 7px;
      margin: 8px 0 10px;
    }}
    .bar-row {{
      display: grid;
      grid-template-columns: minmax(86px, 1fr) minmax(120px, 2fr) 38px;
      gap: 8px;
      align-items: center;
    }}
    .bar-label {{
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .bar-track {{
      height: 11px;
      border-radius: 999px;
      background: #edf1f2;
      overflow: hidden;
    }}
    .bar-fill {{
      height: 100%;
      border-radius: 999px;
      background: var(--teal);
    }}
    .bar-fill.draw {{ background: var(--amber); }}
    .bar-fill.team_b {{ background: var(--purple); }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 8px;
    }}
    .chip {{
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      background: #fbfcfc;
      white-space: nowrap;
    }}
    details {{
      margin-top: 10px;
      border-top: 1px solid var(--line);
      padding-top: 8px;
    }}
    summary {{
      cursor: pointer;
      color: #244f5c;
      font-weight: 750;
      font-size: 13px;
    }}
    .qual-table td.rankbar {{
      min-width: 150px;
    }}
    .rank-stack {{
      display: flex;
      width: 100%;
      height: 18px;
      overflow: hidden;
      border-radius: 4px;
      background: #edf1f2;
    }}
    .rank-seg {{
      height: 100%;
    }}
    .r1 {{ background: #24745b; }}
    .r2 {{ background: #2d63a3; }}
    .r3 {{ background: #b66d1c; }}
    .r4 {{ background: #b94d45; }}
    .entrant-layout {{
      display: grid;
      grid-template-columns: minmax(260px, 340px) 1fr;
      gap: 14px;
    }}
    .standings-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
      gap: 12px;
    }}
    .group-card {{
      padding: 12px;
      overflow: hidden;
    }}
    .group-card h3 {{
      margin-bottom: 8px;
    }}
    .matrix {{
      overflow: auto;
      max-height: 720px;
    }}
    .matrix th, .matrix td {{
      min-width: 62px;
      text-align: center;
      font-variant-numeric: tabular-nums;
    }}
    .matrix th:first-child, .matrix td:first-child {{
      position: sticky;
      left: 0;
      text-align: left;
      background: #fff;
      z-index: 2;
      min-width: 155px;
    }}
    .matrix th:first-child {{
      background: #eef3f1;
      z-index: 3;
    }}
    .tiny {{
      font-size: 11px;
      color: var(--muted);
    }}
    .split {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 12px;
    }}
    .warning {{
      border-left: 5px solid var(--amber);
    }}
    @media (max-width: 780px) {{
      .entrant-layout {{
        grid-template-columns: 1fr;
      }}
      .section-head {{
        display: block;
      }}
      .bar-row {{
        grid-template-columns: minmax(80px, 1fr) minmax(90px, 2fr) 34px;
      }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="kicker">World Cup tipping</div>
    <h1>Group Stage Analysis</h1>
    <p>Generated at {generated} from {source}. The report uses the latest completed full-tournament simulation for each active entrant, plus submitted tips that have already been collected.</p>
  </header>
  <main>
    <section class="section">
      <div id="metrics" class="metric-grid"></div>
    </section>

    <section class="section split">
      <div class="panel">
        <div class="section-head"><div><h2>Biggest Result Disagreements</h2><p>Matches where entrants most disagree on winner or draw.</p></div></div>
        <div id="topDisagreements"></div>
      </div>
      <div class="panel">
        <div class="section-head"><div><h2>Mixed Advancement Teams</h2><p>Teams that qualify in some entrant worlds and miss out in others.</p></div></div>
        <div id="mixedAdvancement"></div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Entrant Group Profiles</h2><p>Side-by-side group-stage tendencies from each entrant's latest simulation.</p></div>
      </div>
      <div id="entrantProfiles" class="panel"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Group Match Disagreement Map</h2><p>Each match shows the split between team A win, draw, and team B win across all entrant simulations.</p></div>
      </div>
      <div class="panel">
        <div id="groupButtons" class="toolbar"></div>
        <div id="matchGrid" class="match-grid"></div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Qualification Possibilities</h2><p>Top two qualify automatically; the best eight third-place teams are counted as third-place qualifiers.</p></div>
      </div>
      <div class="panel">
        <div class="toolbar">
          <select id="qualGroup"></select>
          <input id="teamSearch" type="search" placeholder="Search team">
        </div>
        <div id="qualificationTable"></div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Group Winner Spread</h2><p>How often each team wins its group across entrant simulations.</p></div>
      </div>
      <div id="groupWinnerGrid" class="match-grid"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Entrant Group Tables</h2><p>Projected group tables from one entrant's full simulation.</p></div>
      </div>
      <div class="panel">
        <div class="toolbar">
          <select id="entrantSelect"></select>
        </div>
        <div id="entrantView" class="entrant-layout"></div>
      </div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Entrant Agreement Matrix</h2><p>Percentage of the 72 group matches where each pair predicts the same result.</p></div>
      </div>
      <div id="agreementMatrix" class="panel matrix"></div>
    </section>

    <section class="section">
      <div class="section-head">
        <div><h2>Submitted Tips Collected So Far</h2><p>Live stored predictions, separate from the full simulations above.</p></div>
      </div>
      <div id="submittedPredictions" class="match-grid"></div>
    </section>
  </main>

  <script id="analysis-data" type="application/json">{safe_data}</script>
  <script>
    const DATA = JSON.parse(document.getElementById('analysis-data').textContent);
    const entrantCount = DATA.summary.entrant_count;

    function esc(value) {{
      return String(value ?? '').replace(/[&<>"']/g, char => ({{
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }}[char]));
    }}
    function pct(value) {{ return `${{Math.round((Number(value) || 0) * 100)}}%`; }}
    function countPct(count) {{ return entrantCount ? `${{count}}/${{entrantCount}}` : String(count); }}
    function disagreementBadge(score) {{
      if (score >= 0.5) return '<span class="badge red">high split</span>';
      if (score >= 0.25) return '<span class="badge amber">mixed</span>';
      return '<span class="badge green">mostly aligned</span>';
    }}
    function outcomeBars(outcomes) {{
      return `<div class="bars">${{outcomes.map(item => `
        <div class="bar-row">
          <div class="bar-label" title="${{esc(item.label)}}">${{esc(item.label)}}</div>
          <div class="bar-track"><div class="bar-fill ${{esc(item.key)}}" style="width:${{Math.max(2, item.share * 100)}}%"></div></div>
          <div class="number">${{item.count}}</div>
        </div>
      `).join('')}}</div>`;
    }}
    function scoreChips(scorelines) {{
      return `<div class="chips">${{scorelines.map(row => `<span class="chip">${{esc(row.scoreline)}} x${{row.count}}</span>`).join('')}}</div>`;
    }}
    function predictionDetails(predictions) {{
      return `<details><summary>Entrant predictions</summary><table>
        <thead><tr><th>Entrant</th><th>Score</th><th>Result</th><th>Flags</th></tr></thead>
        <tbody>${{predictions.map(row => `<tr>
          <td>${{esc(row.name)}}</td>
          <td>${{esc(row.scoreline || '')}}</td>
          <td>${{esc(row.outcome_label || '')}}</td>
          <td>${{row.fallback_used ? '<span class="badge amber">fallback</span>' : ''}}</td>
        </tr>`).join('')}}</tbody>
      </table></details>`;
    }}

    function renderMetrics() {{
      const s = DATA.summary;
      const metrics = [
        ['Entrants', s.entrant_count, `${{s.active_registry_count}} active in registry`],
        ['Group matches', s.group_match_count, '12 groups of 4 teams'],
        ['Result disagreements', s.matches_with_result_disagreement, `${{s.unanimous_result_matches}} unanimous`],
        ['No majority', s.no_majority_result_matches, 'No result reached 8 of 15'],
        ['Mixed advancement', s.teams_with_mixed_advancement, 'Teams with split qualification fate'],
        ['Live tip matches', s.submitted_prediction_matches, 'Collected in predictions.json']
      ];
      document.getElementById('metrics').innerHTML = metrics.map(([label, value, sub]) => `
        <div class="metric"><div class="value">${{esc(value)}}</div><div class="label">${{esc(label)}}</div><div class="tiny">${{esc(sub)}}</div></div>
      `).join('');
      if (s.fallback_entrants.length) {{
        document.getElementById('metrics').insertAdjacentHTML('beforeend', `
          <div class="metric warning"><div class="value">${{s.fallback_entrants.length}}</div><div class="label">Entrants with fallbacks</div>
          <div class="tiny">${{s.fallback_entrants.map(row => esc(row.name)).join(', ')}}</div></div>
        `);
      }}
    }}

    function renderTopDisagreements() {{
      document.getElementById('topDisagreements').innerHTML = `<table>
        <thead><tr><th>Match</th><th>Top result</th><th class="number">Split</th><th>Scores</th></tr></thead>
        <tbody>${{DATA.top_disagreements.map(row => `<tr>
          <td><strong>${{row.match_number}}</strong> ${{esc(row.team_a)}} vs ${{esc(row.team_b)}} <span class="tiny">Group ${{esc(row.group)}}</span></td>
          <td>${{esc(row.top_outcome_label)}}</td>
          <td class="number">${{row.top_outcome_count}}/${{entrantCount}}</td>
          <td>${{row.top_scorelines.map(item => `${{esc(item.scoreline)}} x${{item.count}}`).join(', ')}}</td>
        </tr>`).join('')}}</tbody>
      </table>`;
    }}

    function renderMixedAdvancement() {{
      document.getElementById('mixedAdvancement').innerHTML = `<table>
        <thead><tr><th>Team</th><th class="number">Advance</th><th class="number">Top 2</th><th class="number">3rd</th><th>Ranks</th></tr></thead>
        <tbody>${{DATA.mixed_advancement.map(row => `<tr>
          <td><strong>${{esc(row.team)}}</strong> <span class="tiny">Group ${{esc(row.group)}}</span></td>
          <td class="number">${{countPct(row.advance_count)}}</td>
          <td class="number">${{countPct(row.top_two_count)}}</td>
          <td class="number">${{countPct(row.third_place_advance_count)}}</td>
          <td>${{rankStack(row.rank_counts)}}</td>
        </tr>`).join('')}}</tbody>
      </table>`;
    }}

    function renderEntrantProfiles() {{
      const rows = DATA.entrants.filter(row => row.has_simulation);
      document.getElementById('entrantProfiles').innerHTML = `<table>
        <thead><tr><th>Entrant</th><th>Status</th><th class="number">A wins</th><th class="number">Draws</th><th class="number">B wins</th><th class="number">Avg goals</th><th class="number">Fallbacks</th><th>Champion</th></tr></thead>
        <tbody>${{rows.map(row => `<tr>
          <td><strong>${{esc(row.name)}}</strong><div class="tiny">${{esc(row.simulated_at || '')}}</div></td>
          <td>${{esc(row.simulation_status || '')}}${{row.error_count ? `<div class="tiny">${{row.error_count}} total errors</div>` : ''}}</td>
          <td class="number">${{row.team_a_wins}}</td>
          <td class="number">${{row.draws}}</td>
          <td class="number">${{row.team_b_wins}}</td>
          <td class="number">${{row.avg_total_goals}}</td>
          <td class="number">${{row.group_fallbacks || 0}}</td>
          <td>${{esc(row.champion || '')}}</td>
        </tr>`).join('')}}</tbody>
      </table>`;
    }}

    function renderGroupButtons() {{
      const groups = ['All', ...new Set(DATA.matches.map(row => row.group))];
      document.getElementById('groupButtons').innerHTML = groups.map(group => `<button data-group="${{esc(group)}}" class="${{group === 'All' ? 'active' : ''}}">${{esc(group)}}</button>`).join('');
      document.getElementById('groupButtons').addEventListener('click', event => {{
        if (event.target.tagName !== 'BUTTON') return;
        [...document.querySelectorAll('#groupButtons button')].forEach(button => button.classList.remove('active'));
        event.target.classList.add('active');
        renderMatchGrid(event.target.dataset.group);
      }});
    }}

    function renderMatchGrid(group = 'All') {{
      const rows = group === 'All' ? DATA.matches : DATA.matches.filter(row => row.group === group);
      document.getElementById('matchGrid').innerHTML = rows.map(row => `
        <article class="match-card">
          <div class="match-title">
            <div><strong>${{row.match_number}}. ${{esc(row.team_a)}} vs ${{esc(row.team_b)}}</strong><span class="tiny">Group ${{esc(row.group)}} - ${{row.unique_scorelines}} scorelines</span></div>
            ${{disagreementBadge(row.disagreement_score)}}
          </div>
          ${{outcomeBars(row.outcome_counts)}}
          <div class="tiny">Top result: ${{esc(row.top_outcome_label)}} (${{row.top_outcome_count}}/${{entrantCount}})</div>
          ${{scoreChips(row.top_scorelines)}}
          ${{predictionDetails(row.predictions)}}
        </article>
      `).join('');
    }}

    function rankStack(rankCounts) {{
      const total = Object.values(rankCounts).reduce((sum, value) => sum + Number(value || 0), 0) || 1;
      return `<div class="rank-stack" title="${{Object.entries(rankCounts).map(([rank, count]) => `${{rank}}: ${{count}}`).join(', ')}}">
        ${{[1,2,3,4].map(rank => {{
          const count = Number(rankCounts[String(rank)] || 0);
          return count ? `<div class="rank-seg r${{rank}}" style="width:${{count / total * 100}}%"></div>` : '';
        }}).join('')}}
      </div>
      <div class="tiny">${{[1,2,3,4].map(rank => `R${{rank}} ${{rankCounts[String(rank)] || 0}}`).join(' ')}}</div>`;
    }}

    function renderQualificationControls() {{
      const groups = ['All', ...new Set(DATA.qualification.teams.map(row => row.group))];
      document.getElementById('qualGroup').innerHTML = groups.map(group => `<option value="${{esc(group)}}">${{esc(group)}}</option>`).join('');
      document.getElementById('qualGroup').addEventListener('change', renderQualification);
      document.getElementById('teamSearch').addEventListener('input', renderQualification);
    }}

    function renderQualification() {{
      const group = document.getElementById('qualGroup').value;
      const query = document.getElementById('teamSearch').value.trim().toLowerCase();
      const rows = DATA.qualification.teams.filter(row =>
        (group === 'All' || row.group === group) &&
        (!query || row.team.toLowerCase().includes(query))
      );
      document.getElementById('qualificationTable').innerHTML = `<table class="qual-table">
        <thead><tr><th>Team</th><th class="number">Win group</th><th class="number">Top 2</th><th class="number">3rd qual</th><th class="number">Advance</th><th class="number">Eliminated</th><th>Rank spread</th></tr></thead>
        <tbody>${{rows.map(row => `<tr>
          <td><strong>${{esc(row.team)}}</strong> <span class="tiny">Group ${{esc(row.group)}}</span></td>
          <td class="number">${{countPct(row.win_group_count)}}</td>
          <td class="number">${{countPct(row.top_two_count)}}</td>
          <td class="number">${{countPct(row.third_place_advance_count)}}</td>
          <td class="number">${{countPct(row.advance_count)}}</td>
          <td class="number">${{countPct(row.eliminated_count)}}</td>
          <td class="rankbar">${{rankStack(row.rank_counts)}}</td>
        </tr>`).join('')}}</tbody>
      </table>`;
    }}

    function renderGroupWinners() {{
      document.getElementById('groupWinnerGrid').innerHTML = DATA.qualification.group_winners.map(group => `
        <article class="match-card">
          <div class="match-title"><strong>Group ${{esc(group.group)}}</strong><span class="badge blue">${{group.winners.length}} winners</span></div>
          ${{outcomeBars(group.winners.map(row => ({{
            key: 'team_a',
            label: row.team,
            count: row.count,
            share: row.share
          }})))}}
        </article>
      `).join('');
    }}

    function renderEntrantControls() {{
      const entries = DATA.qualification.entrant_tables;
      document.getElementById('entrantSelect').innerHTML = entries.map(row => `<option value="${{esc(row.contestant_id)}}">${{esc(row.name)}}</option>`).join('');
      document.getElementById('entrantSelect').addEventListener('change', renderEntrantView);
      renderEntrantView();
    }}

    function renderEntrantView() {{
      const contestantId = document.getElementById('entrantSelect').value || DATA.qualification.entrant_tables[0]?.contestant_id;
      const entry = DATA.qualification.entrant_tables.find(row => row.contestant_id === contestantId);
      const meta = DATA.entrants.find(row => row.contestant_id === contestantId) || {{}};
      if (!entry) {{
        document.getElementById('entrantView').innerHTML = '';
        return;
      }}
      const qualifierList = entry.qualifiers.map(row => `<li>${{esc(row.team)}} <span class="tiny">${{esc(row.group)}} ${{esc(row.route.replace('_', ' '))}}</span></li>`).join('');
      const groups = Object.entries(entry.tables).sort(([a], [b]) => a.localeCompare(b)).map(([group, rows]) => `
        <article class="group-card">
          <h3>Group ${{esc(group)}}</h3>
          <table>
            <thead><tr><th>R</th><th>Team</th><th class="number">Pts</th><th class="number">GD</th><th class="number">GF</th></tr></thead>
            <tbody>${{rows.map(row => `<tr>
              <td>${{row.rank}}</td><td>${{esc(row.team)}}</td><td class="number">${{row.points}}</td><td class="number">${{row.goal_difference}}</td><td class="number">${{row.goals_for}}</td>
            </tr>`).join('')}}</tbody>
          </table>
        </article>
      `).join('');
      document.getElementById('entrantView').innerHTML = `
        <aside class="panel">
          <h3>${{esc(entry.name)}}</h3>
          <p class="muted">Simulated at ${{esc(meta.simulated_at || '')}}. Group fallbacks: ${{meta.group_fallbacks || 0}}. Average goals: ${{meta.avg_total_goals || 0}}.</p>
          <div class="chips">
            <span class="chip">A wins ${{meta.team_a_wins || 0}}</span>
            <span class="chip">Draws ${{meta.draws || 0}}</span>
            <span class="chip">B wins ${{meta.team_b_wins || 0}}</span>
          </div>
          <h3 style="margin-top:16px">Qualifiers</h3>
          <ul>${{qualifierList}}</ul>
          <p class="tiny">Third-place qualifiers: ${{entry.third_qualifiers.map(esc).join(', ')}}</p>
        </aside>
        <div class="standings-grid">${{groups}}</div>
      `;
    }}

    function agreementColor(value) {{
      const pctValue = Number(value) || 0;
      const hue = 8 + (pctValue * 130);
      const light = 92 - (pctValue * 28);
      return `background:hsl(${{hue}} 48% ${{light}}%);`;
    }}
    function renderAgreementMatrix() {{
      const rows = DATA.agreement.matrix;
      document.getElementById('agreementMatrix').innerHTML = `<table>
        <thead><tr><th>Entrant</th>${{rows.map(row => `<th title="${{esc(row.name)}}">${{esc(row.name.split(' ')[0])}}</th>`).join('')}}</tr></thead>
        <tbody>${{rows.map(row => `<tr>
          <td><strong>${{esc(row.name)}}</strong></td>
          ${{row.cells.map(cell => `<td style="${{agreementColor(cell.result_agreement_pct)}}" title="${{esc(row.name)}} and ${{esc(cell.name)}}: ${{cell.result_agreement_count}}/${{DATA.agreement.match_count}} result agreements, exact score ${{pct(cell.exact_score_agreement_pct)}}">${{pct(cell.result_agreement_pct)}}</td>`).join('')}}
        </tr>`).join('')}}</tbody>
      </table>`;
    }}

    function renderSubmittedPredictions() {{
      const rows = DATA.submitted_predictions;
      if (!rows.length) {{
        document.getElementById('submittedPredictions').innerHTML = '<div class="panel">No submitted group tips are currently stored.</div>';
        return;
      }}
      document.getElementById('submittedPredictions').innerHTML = rows.map(row => `
        <article class="match-card">
          <div class="match-title">
            <div><strong>${{row.match_number}}. ${{esc(row.team_a)}} vs ${{esc(row.team_b)}}</strong><span class="tiny">Group ${{esc(row.group)}} - ${{row.prediction_count}} tips</span></div>
            <span class="badge blue">${{esc(row.top_outcome_label)}} ${{row.top_outcome_count}}</span>
          </div>
          ${{outcomeBars(row.outcome_counts.map(item => ({{
            key: item.key,
            label: item.label,
            count: item.count,
            share: item.count / Math.max(row.prediction_count, 1)
          }})))}}
          ${{scoreChips(row.top_scorelines)}}
          ${{predictionDetails(row.predictions)}}
        </article>
      `).join('');
    }}

    renderMetrics();
    renderTopDisagreements();
    renderMixedAdvancement();
    renderEntrantProfiles();
    renderGroupButtons();
    renderMatchGrid();
    renderQualificationControls();
    renderQualification();
    renderGroupWinners();
    renderEntrantControls();
    renderAgreementMatrix();
    renderSubmittedPredictions();
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
