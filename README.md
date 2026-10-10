# Attendance & Analytics API

This project implements the FastAPI attendance and analytics service described by the OpenAPI contract. All application logic lives in [app/main.py](app/main.py) and uses MongoDB for persistence, validation, and aggregation.

## What is included
- Employee CRUD and indexing at startup
- Punch-in, punch-out, and attendance regularization flows with audit history
- MongoDB-backed analytics for monthly employee summaries and department rollups
- Late leaderboard and department trend endpoints with ranking and date gap filling
- Explain endpoint for MongoDB execution plans

## Local run
```bash
python -m venv .venv
. .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
export MONGO_URI=mongodb://localhost:27017
export MONGO_DB=attendance_db
uvicorn app.main:app --port 8000 --reload
```

A MongoDB 7.x-compatible server is required. If MongoDB is not running, the app still starts but health checks and DB-backed tests will report unavailable instead of failing loudly.

## Contract and rules
- The source of truth is [openapi.yaml](openapi.yaml).
- Time and attendance calculations use IST-aware rules, UTC normalization for storage, and Decimal half-up rounding.
- Public responses never expose MongoDB `_id` values and always use epoch milliseconds for time instants.

## Validation
```bash
pytest -q -x
```
The repository is intentionally kept free of secrets and local environment files.
