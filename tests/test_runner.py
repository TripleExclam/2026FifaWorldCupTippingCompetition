from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from world_cup_tipping.models import isoformat_z
from world_cup_tipping import runner
from world_cup_tipping.runner import RunnerConfig, due_prediction_jobs
from world_cup_tipping.storage import JsonStore


def fixture(match_id: str, kickoff_at: datetime) -> dict:
    return {
        "match_id": match_id,
        "match_number": int(match_id.rsplit("-", maxsplit=1)[1]),
        "stage": "group",
        "group": "A",
        "team_a": "Mexico",
        "team_b": "South Africa",
        "team_a_placeholder": None,
        "team_b_placeholder": None,
        "kickoff_at": isoformat_z(kickoff_at),
        "score_a": None,
        "score_b": None,
        "winner": None,
        "status": "scheduled",
    }


def test_default_runner_lookahead_checks_fixtures_24_hours_in_advance() -> None:
    now = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)
    registry = [{"id": "active-bot", "url": "http://example.com/predict", "status": "active"}]
    fixtures = [
        fixture("2026-001", now + timedelta(hours=23)),
        fixture("2026-002", now + timedelta(hours=25)),
    ]

    jobs = due_prediction_jobs(fixtures, registry, [], now, RunnerConfig())

    assert [(job_fixture["match_id"], contestant["id"]) for job_fixture, contestant in jobs] == [
        ("2026-001", "active-bot")
    ]


def test_failed_prediction_is_retryable_before_lock() -> None:
    now = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)
    registry = [{"id": "active-bot", "url": "http://example.com/predict", "status": "active"}]
    fixtures = [fixture("2026-001", now + timedelta(hours=2))]
    predictions = [
        {
            "id": "failed-attempt",
            "contestant_id": "active-bot",
            "match_id": "2026-001",
            "requested_at": isoformat_z(now - timedelta(hours=1)),
            "valid": False,
            "prediction": None,
            "raw_response": None,
            "error": "ConnectError: unavailable",
        }
    ]

    jobs = due_prediction_jobs(fixtures, registry, predictions, now, RunnerConfig(lock_minutes=30))

    assert [(job_fixture["match_id"], contestant["id"]) for job_fixture, contestant in jobs] == [
        ("2026-001", "active-bot")
    ]


def test_valid_prediction_is_not_retryable() -> None:
    now = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)
    registry = [{"id": "active-bot", "url": "http://example.com/predict", "status": "active"}]
    fixtures = [fixture("2026-001", now + timedelta(hours=2))]
    predictions = [
        {
            "id": "valid-attempt",
            "contestant_id": "active-bot",
            "match_id": "2026-001",
            "requested_at": isoformat_z(now - timedelta(hours=1)),
            "valid": True,
            "prediction": {
                "predicted_score_a": 2,
                "predicted_score_b": 1,
                "predicted_winner": "Mexico",
                "confidence": None,
            },
            "raw_response": {
                "predicted_score_a": 2,
                "predicted_score_b": 1,
                "predicted_winner": "Mexico",
            },
            "error": None,
        }
    ]

    jobs = due_prediction_jobs(fixtures, registry, predictions, now, RunnerConfig(lock_minutes=30))

    assert jobs == []


def test_failed_prediction_is_not_retryable_inside_lock_window() -> None:
    now = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)
    registry = [{"id": "active-bot", "url": "http://example.com/predict", "status": "active"}]
    fixtures = [fixture("2026-001", now + timedelta(minutes=20))]
    predictions = [
        {
            "id": "failed-attempt",
            "contestant_id": "active-bot",
            "match_id": "2026-001",
            "requested_at": isoformat_z(now - timedelta(hours=1)),
            "valid": False,
            "prediction": None,
            "raw_response": None,
            "error": "ReadTimeout: unavailable",
        }
    ]

    jobs = due_prediction_jobs(fixtures, registry, predictions, now, RunnerConfig(lock_minutes=30))

    assert jobs == []


def test_successful_retry_replaces_failed_prediction_record(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime(2026, 6, 11, 0, 0, tzinfo=UTC)
    store = JsonStore(tmp_path)
    store.ensure_defaults()
    store.write("fixtures.json", [fixture("2026-001", now + timedelta(hours=2))])
    store.write(
        "registry.json",
        [{"id": "active-bot", "url": "http://example.com/predict", "status": "active"}],
    )
    store.write(
        "predictions.json",
        [
            {
                "id": "failed-attempt",
                "contestant_id": "active-bot",
                "match_id": "2026-001",
                "requested_at": isoformat_z(now - timedelta(hours=1)),
                "valid": False,
                "prediction": None,
                "raw_response": None,
                "error": "ConnectError: unavailable",
            }
        ],
    )

    async def fake_call_prediction_jobs(
        jobs: list[tuple[dict[str, Any], dict[str, Any]]],
        previous_results: list[dict[str, Any]],
        config: RunnerConfig,
    ) -> list[dict[str, Any]]:
        assert [(job_fixture["match_id"], contestant["id"]) for job_fixture, contestant in jobs] == [
            ("2026-001", "active-bot")
        ]
        assert previous_results == []
        return [
            {
                "id": "successful-retry",
                "contestant_id": "active-bot",
                "match_id": "2026-001",
                "requested_at": isoformat_z(now),
                "valid": True,
                "prediction": {
                    "predicted_score_a": 2,
                    "predicted_score_b": 1,
                    "predicted_winner": "Mexico",
                    "confidence": None,
                },
                "raw_response": {
                    "predicted_score_a": 2,
                    "predicted_score_b": 1,
                    "predicted_winner": "Mexico",
                },
                "error": None,
            }
        ]

    monkeypatch.setattr(runner, "_call_prediction_jobs", fake_call_prediction_jobs)

    result = asyncio.run(runner.run_due_once(store, RunnerConfig(), now))

    assert result["jobs_attempted"] == 1
    assert result["predictions_recorded"] == 1
    assert store.read("predictions.json") == [
        {
            "id": "successful-retry",
            "contestant_id": "active-bot",
            "match_id": "2026-001",
            "requested_at": isoformat_z(now),
            "valid": True,
            "prediction": {
                "predicted_score_a": 2,
                "predicted_score_b": 1,
                "predicted_winner": "Mexico",
                "confidence": None,
            },
            "raw_response": {
                "predicted_score_a": 2,
                "predicted_score_b": 1,
                "predicted_winner": "Mexico",
            },
            "error": None,
        }
    ]
