from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

import httpx

from world_cup_tipping.models import isoformat_z, utc_now
from world_cup_tipping.runner import RunnerConfig, run_due_once
from world_cup_tipping.storage import JsonStore


def wait_for_server(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = httpx.get(url, timeout=0.5)
            if response.status_code == 200:
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Server did not start: {url}")


def test_fixed_server_prediction_and_scoring(tmp_path: Path, free_tcp_port: int) -> None:
    base_url = f"http://127.0.0.1:{free_tcp_port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "examples.fixed_fastapi_server.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(free_tcp_port),
        ],
        cwd=Path(__file__).resolve().parents[1],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_server(f"{base_url}/health")
        now = utc_now()
        store = JsonStore(tmp_path)
        store.ensure_defaults()
        store.write(
            "fixtures.json",
            [
                {
                    "match_id": "2026-TEST-001",
                    "match_number": 1,
                    "stage": "group",
                    "group": "A",
                    "team_a": "Mexico",
                    "team_b": "South Africa",
                    "team_a_placeholder": None,
                    "team_b_placeholder": None,
                    "kickoff_at": isoformat_z(now + timedelta(hours=2)),
                    "score_a": None,
                    "score_b": None,
                    "winner": None,
                    "status": "scheduled",
                }
            ],
        )
        store.write(
            "registry.json",
            [
                {
                    "id": "fixed",
                    "name": "Fixed Bot",
                    "url": f"{base_url}/predict",
                    "contact": "local",
                    "status": "active",
                }
            ],
        )

        asyncio.run(run_due_once(store, RunnerConfig(timeout_seconds=2.0), now))
        predictions = store.read("predictions.json")
        assert len(predictions) == 1
        assert predictions[0]["valid"] is True
        assert predictions[0]["prediction"]["predicted_score_a"] == 2

        fixtures = store.read("fixtures.json")
        fixtures[0]["score_a"] = 2
        fixtures[0]["score_b"] = 1
        fixtures[0]["winner"] = "Mexico"
        fixtures[0]["status"] = "completed"
        store.write("fixtures.json", fixtures)

        asyncio.run(run_due_once(store, RunnerConfig(timeout_seconds=2.0), now))
        scores = store.read("scores.json")
        assert scores == [
            {
                "contestant_id": "fixed",
                "match_id": "2026-TEST-001",
                "points": 1.5,
                "reason": "exact_score",
                "scored_at": scores[0]["scored_at"],
            }
        ]
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def test_retrospective_run_scores_completed_match_without_prediction(tmp_path: Path) -> None:
    now = utc_now()
    store = JsonStore(tmp_path)
    store.ensure_defaults()
    store.write(
        "fixtures.json",
        [
            {
                "match_id": "2026-PAST-001",
                "match_number": 1,
                "stage": "group",
                "group": "A",
                "team_a": "Mexico",
                "team_b": "South Africa",
                "team_a_placeholder": None,
                "team_b_placeholder": None,
                "kickoff_at": isoformat_z(now - timedelta(hours=2)),
                "score_a": 2,
                "score_b": 1,
                "winner": "Mexico",
                "status": "completed",
            }
        ],
    )
    store.write(
        "registry.json",
        [
            {
                "id": "late-bot",
                "name": "Late Bot",
                "url": "http://127.0.0.1:9999/predict",
                "contact": "local",
                "status": "active",
            }
        ],
    )

    result = asyncio.run(run_due_once(store, RunnerConfig(timeout_seconds=0.1), now))

    assert result["jobs_attempted"] == 0
    assert result["scores_added"] == 1
    assert store.read("scores.json") == [
        {
            "contestant_id": "late-bot",
            "match_id": "2026-PAST-001",
            "points": 0.0,
            "reason": "missing_prediction",
            "scored_at": store.read("scores.json")[0]["scored_at"],
        }
    ]
