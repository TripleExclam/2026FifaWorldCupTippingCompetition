from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

import httpx

from .models import result_key
from .storage import JsonStore, get_store


FIFA_SCORES_FIXTURES_URL = (
    "https://www.fifa.com/en/tournaments/mens/worldcup/"
    "canadamexicousa2026/scores-fixtures?country=AU&wtw-filter=ALL"
)
FIFA_API_BASE_URL = "https://api.fifa.com/api/v3"
FIFA_WORLD_CUP_COMPETITION_ID = "17"
FIFA_WORLD_CUP_2026_SEASON_ID = "285023"
FIFA_PLAYED_MATCH_STATUS = 0


@dataclass(frozen=True)
class FifaSourceConfig:
    api_base_url: str = FIFA_API_BASE_URL
    competition_id: str = FIFA_WORLD_CUP_COMPETITION_ID
    season_id: str = FIFA_WORLD_CUP_2026_SEASON_ID
    locale: str = "en"
    count: int = 500
    timeout_seconds: float = 15.0


@dataclass(frozen=True)
class ScrapedResult:
    match_number: int
    source_match_id: str
    team_a: str | None
    team_b: str | None
    score_a: int | None
    score_b: int | None
    penalty_score_a: int | None
    penalty_score_b: int | None
    winner_side: str | None
    match_status: int | None
    result_type: int | None
    officiality_status: int | None
    completed: bool


@dataclass
class ResultScrapeReport:
    source: str = "fifa"
    fetched: int = 0
    matched: int = 0
    result_updates: int = 0
    team_updates: int = 0
    stale_scores_removed: int = 0
    changed_match_ids: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.result_updates or self.team_updates or self.stale_scores_removed)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ResultSource(Protocol):
    async def fetch(self) -> list[ScrapedResult]:
        raise NotImplementedError


class FifaResultSource:
    def __init__(self, config: FifaSourceConfig | None = None) -> None:
        self.config = config or FifaSourceConfig()

    async def fetch(self) -> list[ScrapedResult]:
        headers = {"User-Agent": "world-cup-tipping-result-scraper/1.0"}
        timeout = httpx.Timeout(self.config.timeout_seconds)
        async with httpx.AsyncClient(
            base_url=self.config.api_base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        ) as client:
            rows = await _fetch_all_fifa_matches(client, self.config)
        return parse_fifa_matches(rows)


async def _fetch_all_fifa_matches(client: httpx.AsyncClient, config: FifaSourceConfig) -> list[dict[str, Any]]:
    params = {
        "language": config.locale,
        "count": str(config.count),
        "idCompetition": config.competition_id,
        "idSeason": config.season_id,
    }
    rows: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    continuation_headers: dict[str, str] = {}

    while True:
        response = await client.get("/calendar/matches", params=params, headers=continuation_headers)
        response.raise_for_status()
        payload = response.json()
        page = payload.get("Results") or []
        rows.extend(item for item in page if isinstance(item, dict))

        continuation_hash = payload.get("ContinuationHash")
        continuation_token = payload.get("ContinuationToken")
        if not page or not continuation_hash or not continuation_token:
            break
        continuation_hash = str(continuation_hash)
        if continuation_hash in seen_hashes:
            break
        seen_hashes.add(continuation_hash)
        params["continuationhash"] = continuation_hash
        continuation_headers = {"x-mdp-continuation-token": str(continuation_token)}

    return rows


def parse_fifa_matches(rows: list[dict[str, Any]]) -> list[ScrapedResult]:
    results = []
    for row in rows:
        result = parse_fifa_match(row)
        if result is not None:
            results.append(result)
    return results


def parse_fifa_match(row: dict[str, Any]) -> ScrapedResult | None:
    match_number = _int_or_none(row.get("MatchNumber"))
    if match_number is None:
        return None

    home = row.get("Home") if isinstance(row.get("Home"), dict) else {}
    away = row.get("Away") if isinstance(row.get("Away"), dict) else {}
    score_a = _int_or_none(row.get("HomeTeamScore"))
    score_b = _int_or_none(row.get("AwayTeamScore"))
    if score_a is None:
        score_a = _int_or_none(home.get("Score"))
    if score_b is None:
        score_b = _int_or_none(away.get("Score"))

    match_status = _int_or_none(row.get("MatchStatus"))
    result_type = _int_or_none(row.get("ResultType"))
    officiality_status = _int_or_none(row.get("OfficialityStatus"))
    completed = match_status == FIFA_PLAYED_MATCH_STATUS and score_a is not None and score_b is not None

    return ScrapedResult(
        match_number=match_number,
        source_match_id=str(row.get("IdMatch") or ""),
        team_a=_team_name(home),
        team_b=_team_name(away),
        score_a=score_a,
        score_b=score_b,
        penalty_score_a=_int_or_none(row.get("HomeTeamPenaltyScore")),
        penalty_score_b=_int_or_none(row.get("AwayTeamPenaltyScore")),
        winner_side=_winner_side(row, home, away, score_a, score_b),
        match_status=match_status,
        result_type=result_type,
        officiality_status=officiality_status,
        completed=completed,
    )


def apply_scraped_results(fixtures: list[dict[str, Any]], scraped_results: list[ScrapedResult]) -> ResultScrapeReport:
    report = ResultScrapeReport(fetched=len(scraped_results))
    fixtures_by_number = {
        fixture.get("match_number"): fixture
        for fixture in fixtures
        if isinstance(fixture.get("match_number"), int)
    }

    for result in scraped_results:
        fixture = fixtures_by_number.get(result.match_number)
        if fixture is None:
            continue
        report.matched += 1

        if _apply_resolved_teams(fixture, result):
            report.team_updates += 1

        if not result.completed or result.score_a is None or result.score_b is None:
            continue

        payload = {
            "score_a": result.score_a,
            "score_b": result.score_b,
            "winner": _fixture_winner(fixture, result),
            "status": "completed",
            "result_source": "fifa",
            "source_match_id": result.source_match_id,
        }
        if result.penalty_score_a is not None or "penalty_score_a" in fixture:
            payload["penalty_score_a"] = result.penalty_score_a
        if result.penalty_score_b is not None or "penalty_score_b" in fixture:
            payload["penalty_score_b"] = result.penalty_score_b

        score_changed = any(fixture.get(field) != payload[field] for field in ["score_a", "score_b", "winner", "status"])
        payload_changed = any(fixture.get(field) != value for field, value in payload.items())
        if not payload_changed:
            continue

        fixture.update(payload)
        report.result_updates += 1
        if score_changed:
            report.changed_match_ids.append(fixture["match_id"])

    return report


async def scrape_results_once(
    store: JsonStore | None = None,
    source: ResultSource | None = None,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    store = store or get_store()
    source = source or FifaResultSource()
    scraped_results = await source.fetch()

    with store.locked():
        fixtures = store.read("fixtures.json")
        report = apply_scraped_results(fixtures, scraped_results)
        changed_match_ids = set(report.changed_match_ids)
        if changed_match_ids:
            scores = store.read("scores.json")
            filtered_scores = [score for score in scores if score.get("match_id") not in changed_match_ids]
            report.stale_scores_removed = len(scores) - len(filtered_scores)
        else:
            filtered_scores = None

        if not dry_run and report.changed:
            store.write("fixtures.json", fixtures)
            if filtered_scores is not None and report.stale_scores_removed:
                store.write("scores.json", filtered_scores)

    return report.as_dict()


def _apply_resolved_teams(fixture: dict[str, Any], result: ScrapedResult) -> bool:
    changed = False
    if result.team_a and not fixture.get("team_a"):
        fixture["team_a"] = result.team_a
        changed = True
    if result.team_b and not fixture.get("team_b"):
        fixture["team_b"] = result.team_b
        changed = True
    return changed


def _fixture_winner(fixture: dict[str, Any], result: ScrapedResult) -> str | None:
    if result.score_a is None or result.score_b is None:
        return None
    score_result = result_key(result.score_a, result.score_b)
    if score_result == "team_a":
        return fixture.get("team_a") or result.team_a
    if score_result == "team_b":
        return fixture.get("team_b") or result.team_b
    if result.winner_side == "team_a":
        return fixture.get("team_a") or result.team_a
    if result.winner_side == "team_b":
        return fixture.get("team_b") or result.team_b
    return None


def _winner_side(
    row: dict[str, Any],
    home: dict[str, Any],
    away: dict[str, Any],
    score_a: int | None,
    score_b: int | None,
) -> str | None:
    winner_id = row.get("Winner")
    if winner_id is not None:
        winner_id = str(winner_id)
        if winner_id == str(home.get("IdTeam")):
            return "team_a"
        if winner_id == str(away.get("IdTeam")):
            return "team_b"

    if score_a is None or score_b is None:
        return None
    score_result = result_key(score_a, score_b)
    if score_result == "draw":
        return None
    return score_result


def _team_name(team: dict[str, Any]) -> str | None:
    name = _localized_description(team.get("TeamName"))
    if name:
        return name
    short_name = team.get("ShortClubName")
    return str(short_name) if short_name else None


def _localized_description(values: Any) -> str | None:
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, dict) and item.get("Description"):
            return str(item["Description"])
    return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None
