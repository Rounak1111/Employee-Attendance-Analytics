# Attendance & Analytics API (hiring assessment)

## Specs (read only what the current phase needs)
- `openapi.yaml` is the contract and rules R1-R10. It wins over every other file.
- `DATA_MODEL.md` describes stored shapes. `BRIEFING.md` condenses both plus grading rules when supplied.
- `PROGRESS.md` is current state. Read it first and update it last.

## Hard constraints
- All application code lives in `app/main.py`. Start with `uvicorn app.main:app --port 8000`.
- Python 3.11+, FastAPI, synchronous PyMongo, MongoDB 6.0+ (grader uses 7.0).
- Read `MONGO_URI` and `MONGO_DB` from the environment. `load_dotenv` must not override real environment variables. Never hard-code connection details.
- Create every index at startup, idempotently.
- Analytics use MongoDB aggregation pipelines; do not fetch and loop in Python.
- Never return `_id` or `id`. API instants are epoch milliseconds. MongoDB stores timezone-aware UTC datetimes.
- Dates, months, and shift times are IST strings. Truncate instants to whole seconds first.
- Use Decimal half-up rounding, never `round()`. Reported numbers use 2 decimal places and rates 4.
- Analytics read stored `work_hours`, `late_minutes`, `overtime_minutes`, and `half_day`; never recompute them.
- A 500 is always a bug. Use 404 for missing resources, 409 for conflicts, and 422 for validation.
- Final repository contents: `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md`, `README.md`, plus optional `tests/` and `scripts/`. No `.env`, Dockerfile, or secrets.

## Working rules
- Do only the phase requested. Do not start the next phase or add extras.
- Do not use subagents, web search, or write planning documents.
- Search first and read targeted ranges; do not read whole files by default.
- Run tests with `pytest -q -x`. Allow at most 2 fix-and-retest cycles per problem, then stop and report.
- Keep `main.py` readable with section comments and small functions; add docstrings only for non-obvious logic.
- Never write `REVIEW.md` or `DECISIONS.md` prose for the candidate.
- Prefer simple, explicit code the candidate can explain line by line.

## End of every phase
1. Update `PROGRESS.md` with status, changes, open issues, and explain-it-to-me notes.
2. Commit with a clear message and push to the repository's main branch when a remote is configured and authorized.
3. Reply in at most 8 lines: what is done, what is not, and what the candidate should do next.
