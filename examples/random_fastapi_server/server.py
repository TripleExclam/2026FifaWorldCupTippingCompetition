from random import choice, randint, random
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Random World Cup Prediction Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/predict")
def predict(payload: dict[str, Any]):
    team_a = payload["team_a"]
    team_b = payload["team_b"]
    stage = payload["stage"]

    score_a = randint(0, 4)
    score_b = randint(0, 4)

    winner = None
    if score_a > score_b:
        winner = team_a
    elif score_b > score_a:
        winner = team_b
    elif stage != "group":
        winner = choice([team_a, team_b])

    return {
        "predicted_score_a": score_a,
        "predicted_score_b": score_b,
        "predicted_winner": winner,
        "confidence": round(random(), 2),
    }
