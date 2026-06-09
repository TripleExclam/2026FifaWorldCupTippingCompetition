from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


STAGE_GROUP = "group"
KNOCKOUT_STAGES = {
    "round_of_32",
    "round_of_16",
    "quarterfinal",
    "semifinal",
    "third_place",
    "final",
}

STAGE_LABELS = {
    "group": "Group",
    "round_of_32": "Round of 32",
    "round_of_16": "Round of 16",
    "quarterfinal": "Quarterfinal",
    "semifinal": "Semifinal",
    "third_place": "Third Place",
    "final": "Final",
}

STAGE_BY_MATCH_NUMBER = {
    "group": range(1, 73),
    "round_of_32": range(73, 89),
    "round_of_16": range(89, 97),
    "quarterfinal": range(97, 101),
    "semifinal": range(101, 103),
    "third_place": range(103, 104),
    "final": range(104, 105),
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_z(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def stage_for_match_number(match_number: int) -> str:
    for stage, numbers in STAGE_BY_MATCH_NUMBER.items():
        if match_number in numbers:
            return stage
    raise ValueError(f"Unsupported match number: {match_number}")


def match_id_for_number(match_number: int) -> str:
    return f"2026-{match_number:03d}"


def result_key(score_a: int, score_b: int) -> str:
    if score_a > score_b:
        return "team_a"
    if score_b > score_a:
        return "team_b"
    return "draw"


def winner_from_score(team_a: str | None, team_b: str | None, score_a: int, score_b: int) -> str | None:
    result = result_key(score_a, score_b)
    if result == "team_a":
        return team_a
    if result == "team_b":
        return team_b
    return None


def display_team(fixture: dict[str, Any], side: str) -> str:
    team = fixture.get(f"team_{side}")
    if team:
        return str(team)
    placeholder = fixture.get(f"team_{side}_placeholder")
    if placeholder:
        return str(placeholder)
    return "TBD"


def is_resolved_fixture(fixture: dict[str, Any]) -> bool:
    return bool(fixture.get("team_a") and fixture.get("team_b"))


def is_completed_fixture(fixture: dict[str, Any]) -> bool:
    return fixture.get("score_a") is not None and fixture.get("score_b") is not None


def completed_results(fixtures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for fixture in sorted(fixtures, key=lambda item: item.get("kickoff_at") or ""):
        if not is_completed_fixture(fixture):
            continue
        rows.append(
            {
                "match_id": fixture["match_id"],
                "stage": fixture["stage"],
                "team_a": fixture.get("team_a"),
                "team_b": fixture.get("team_b"),
                "score_a": fixture.get("score_a"),
                "score_b": fixture.get("score_b"),
                "winner": fixture.get("winner"),
            }
        )
    return rows

