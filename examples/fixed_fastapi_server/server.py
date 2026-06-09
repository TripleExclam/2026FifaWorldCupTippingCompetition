from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware


app = FastAPI(title="Fixed World Cup Prediction Server")
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
def predict(payload: dict[str, Any]) -> dict[str, Any]:
    team_a = payload["team_a"]
    return {
        "predicted_score_a": 2,
        "predicted_score_b": 1,
        "predicted_winner": team_a,
        "confidence": 0.9,
    }
