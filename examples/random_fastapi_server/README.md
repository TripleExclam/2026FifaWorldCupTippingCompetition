# Random FastAPI Contestant Server

This is a minimal contestant server for the FIFA World Cup tipping competition.
It allows browser CORS requests so the leaderboard API tester can call it directly.

It exposes:

```http
POST /predict
GET /health
```

## Run

```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8000
```

## Test

```bash
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{
    "match_id": "2026-GROUP-A-001",
    "stage": "group",
    "team_a": "Australia",
    "team_b": "France",
    "previous_results": []
  }'
```
