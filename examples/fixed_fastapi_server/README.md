# Fixed FastAPI Contestant Server

This deterministic local server returns a `2-1` prediction for every non-draw fixture.
It allows browser CORS requests so the leaderboard API tester can call it directly.

```bash
uvicorn examples.fixed_fastapi_server.server:app --host 127.0.0.1 --port 8001
```
