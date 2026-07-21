# Gothenburg School Guide MVP v0.7

A Render-ready FastAPI web app for comparing Gothenburg schools for expat families choosing F0/förskoleklass through F–9.

## What v0.7 adds

- Backend + SQLite database from v0.6 retained.
- Current-year data mode retained: the app uses the newest imported year and falls back per school only when the current year is missing.
- Quality score is computed by the backend, not manually sorted.
- **Skolinspektionen Skolenkäten importer added.**
- A prebuilt `data/imports/schools-2026-skolenkaten.json` import is included, generated from the official Skolenkäten 2026 Excel files.
- CLI script: `scripts/import_skolenkaten.py`.
- Admin endpoint: `POST /api/admin/import/skolenkaten?year=2026&apply=true`.

## Important source model

The app separates source types:

| Data type | Primary source |
|---|---|
| Survey/rating fields | Skolinspektionen Skolenkäten Excel files |
| School-unit facts and displayed school pages | Skolverket / Utbildningsguiden |
| Academic indicators | Skolverket / Utbildningsguiden / national statistics |
| Municipal admission realism | Göteborg Stad placement statistics |
| Private-school queue rules | Each private school's own admission page |

Skolenkäten survey fields imported in v0.7 include:

- F0 guardian satisfaction
- Safety / trygghet
- Study peace / studiero
- Support / stöd
- Grade 5 or grade 8 pupil satisfaction
- Guardian satisfaction for grundskola

Academic score and admission realism are **not** overwritten by the Skolenkäten importer.

## Current import behavior

For the 15 seeded schools, the included Skolenkäten 2026 import matched 11 schools by `skolenhetskod`.

Schools not matched in the 2026 Skolenkäten Excel files keep their existing verified values and confidence/missing-data treatment:

- Innovitaskolan St Jörgen
- IES Södra Änggården
- Göteborgs Högre Samskola / Lilla Samskolan
- Vittra Kronhusparken

That can happen when a school did not participate in that Skolenkäten year, has no published row in that file, or uses another/reporting unit.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open:

```text
http://localhost:8000
```

## Render Web Service settings

```text
Build command: pip install -r requirements.txt
Start command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

## Re-import Skolenkäten data from CLI

```bash
PYTHONPATH=. python scripts/import_skolenkaten.py --year 2026
```

This downloads the official Excel files into `data/source_cache/skolenkaten/2026/`, parses them, and writes:

```text
data/imports/schools-2026-skolenkaten.json
```

The app imports JSON files in `data/imports/` on startup.

## Re-import through API

```bash
curl -X POST "https://YOUR-APP.onrender.com/api/admin/import/skolenkaten?year=2026&apply=true" \
  -H "x-admin-token: YOUR_ADMIN_TOKEN"
```

Set `ADMIN_TOKEN` in Render if you want the endpoint protected.

## API endpoints

```text
/
/api/health
/api/metadata
/api/methodology
/api/schools?year=current
/api/schools/{slug}?year=current
/api/admin/import/skolenkaten?year=2026&apply=true
/api/import
```

## Notes

This is still an MVP. It is not yet a fully automated Skolverket/Göteborg import pipeline. The next version should add:

- Skolverket school-unit API importer
- Göteborg placement-stat parser
- proper PostgreSQL instead of SQLite for production persistence
- real geocoding and route distance
- separate year trends in the UI
