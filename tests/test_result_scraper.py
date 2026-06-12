from __future__ import annotations

import asyncio
from pathlib import Path

from world_cup_tipping.result_scraper import (
    ScrapedResult,
    apply_scraped_results,
    parse_fifa_match,
    scrape_results_once,
)
from world_cup_tipping.storage import JsonStore


def fifa_match(
    match_number: int,
    *,
    home: str = "Mexico",
    away: str = "South Africa",
    home_id: str = "43911",
    away_id: str = "43883",
    score_a: int | None = 2,
    score_b: int | None = 0,
    winner: str | None = "43911",
    match_status: int = 0,
    result_type: int = 1,
    penalties: tuple[int | None, int | None] = (None, None),
) -> dict:
    return {
        "IdMatch": f"400000{match_number}",
        "MatchNumber": match_number,
        "MatchStatus": match_status,
        "ResultType": result_type,
        "OfficialityStatus": 1 if match_status == 0 else 0,
        "HomeTeamScore": score_a,
        "AwayTeamScore": score_b,
        "HomeTeamPenaltyScore": penalties[0],
        "AwayTeamPenaltyScore": penalties[1],
        "Winner": winner,
        "Home": {
            "IdTeam": home_id,
            "TeamName": [{"Locale": "en-GB", "Description": home}],
            "Score": score_a,
        },
        "Away": {
            "IdTeam": away_id,
            "TeamName": [{"Locale": "en-GB", "Description": away}],
            "Score": score_b,
        },
    }


def fixture(match_number: int, team_a: str | None = "Mexico", team_b: str | None = "South Africa") -> dict:
    return {
        "match_id": f"2026-{match_number:03d}",
        "match_number": match_number,
        "stage": "group",
        "group": "A",
        "team_a": team_a,
        "team_b": team_b,
        "team_a_placeholder": None,
        "team_b_placeholder": None,
        "kickoff_at": "2026-06-11T19:00:00Z",
        "score_a": None,
        "score_b": None,
        "winner": None,
        "status": "scheduled",
    }


def test_parses_completed_fifa_match() -> None:
    result = parse_fifa_match(fifa_match(1))

    assert result is not None
    assert result.match_number == 1
    assert result.completed is True
    assert result.score_a == 2
    assert result.score_b == 0
    assert result.winner_side == "team_a"


def test_apply_scraped_results_updates_completed_matches_only_and_preserves_local_names() -> None:
    fixtures = [
        fixture(1),
        fixture(2, team_a="Korea Republic", team_b="Czech Republic"),
    ]
    scraped = [
        parse_fifa_match(fifa_match(1)),
        parse_fifa_match(
            fifa_match(
                2,
                home="Korea Republic",
                away="Czechia",
                score_a=None,
                score_b=None,
                winner=None,
                match_status=1,
                result_type=0,
            )
        ),
    ]

    report = apply_scraped_results(fixtures, [result for result in scraped if result is not None])

    assert report.fetched == 2
    assert report.matched == 2
    assert report.result_updates == 1
    assert fixtures[0]["score_a"] == 2
    assert fixtures[0]["score_b"] == 0
    assert fixtures[0]["winner"] == "Mexico"
    assert fixtures[0]["status"] == "completed"
    assert fixtures[0]["result_source"] == "fifa"
    assert fixtures[1]["team_b"] == "Czech Republic"
    assert fixtures[1]["score_a"] is None
    assert fixtures[1]["status"] == "scheduled"


def test_apply_scraped_results_resolves_knockout_teams_and_penalty_winner() -> None:
    knockout = fixture(89, team_a=None, team_b=None)
    knockout["stage"] = "round_of_16"
    knockout["group"] = None
    knockout["team_a_placeholder"] = "Winner 74"
    knockout["team_b_placeholder"] = "Winner 77"
    result = parse_fifa_match(
        fifa_match(
            89,
            home="Canada",
            away="Brazil",
            home_id="43899",
            away_id="43924",
            score_a=1,
            score_b=1,
            winner="43924",
            penalties=(4, 5),
        )
    )

    report = apply_scraped_results([knockout], [result] if result is not None else [])

    assert report.team_updates == 1
    assert report.result_updates == 1
    assert knockout["team_a"] == "Canada"
    assert knockout["team_b"] == "Brazil"
    assert knockout["winner"] == "Brazil"
    assert knockout["penalty_score_a"] == 4
    assert knockout["penalty_score_b"] == 5


def test_scrape_results_once_removes_stale_scores_for_corrected_result(tmp_path: Path) -> None:
    class FakeSource:
        async def fetch(self) -> list[ScrapedResult]:
            return [
                ScrapedResult(
                    match_number=1,
                    source_match_id="4000001",
                    team_a="Mexico",
                    team_b="South Africa",
                    score_a=3,
                    score_b=0,
                    penalty_score_a=None,
                    penalty_score_b=None,
                    winner_side="team_a",
                    match_status=0,
                    result_type=1,
                    officiality_status=1,
                    completed=True,
                )
            ]

    store = JsonStore(tmp_path)
    store.ensure_defaults()
    completed_fixture = fixture(1)
    completed_fixture.update({"score_a": 2, "score_b": 0, "winner": "Mexico", "status": "completed"})
    store.write("fixtures.json", [completed_fixture])
    store.write(
        "scores.json",
        [{"contestant_id": "fixed", "match_id": "2026-001", "points": 1.5, "reason": "exact_score"}],
    )

    report = asyncio.run(scrape_results_once(store, source=FakeSource()))

    assert report["result_updates"] == 1
    assert report["stale_scores_removed"] == 1
    assert store.read("fixtures.json")[0]["score_a"] == 3
    assert store.read("scores.json") == []
