# Gothenburg School Guide MVP v0.4

A backend/database version of the Gothenburg school-choice app for expat families.

## What changed in v0.4

- Converted from a static-only site to a Render Web Service.
- Added a FastAPI backend.
- Added a SQLite database created on startup.
- Added `/api/schools?year=2027` with per-school year fallback.
- Added `/api/metadata` so the frontend can display the available data years.
- Added automatic import on startup from:
  - `data/schools-2026.json`
  - any future files matching `data/imports/schools-*.json`
- Improved the directory top note so the page no longer has the awkward long sentence at the top.

## Year fallback behavior

The frontend asks the backend for target year `2027`.

For each school:

1. If 2027 data exists, the API returns the 2027 record.
2. If 2027 data does not exist, the API returns the latest verified earlier year, such as 2026.
3. The school card shows the actual `Data year` used.
4. If fallback is active, the card displays a fallback badge.

This means the app is now ready for 2027 data, but it does not scrape Skolverket live yet. A 2027 import file or future Skolverket importer must provide the official 2027 records.

## Local run

```bash
cd gothenburg-school-guide-v0.4
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000
```

API:

```text
http://localhost:8000/api/metadata
http://localhost:8000/api/schools?year=2027
```

## Render deployment

Create a Render Web Service from the repository root.

Use:

```text
Build command: pip install -r requirements.txt
Start command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` is ready for a Render Blueprint.

## Importing 2027 data

Option A: add a file such as:

```text
data/imports/schools-2027.json
```

Use the same record shape as `data/schools-2026.json`, but set:

```json
"dataYear": 2027,
"lastVerified": "<verification date>"
```

On restart/redeploy, the backend imports it automatically.

Option B: POST to `/api/import`.

If `ADMIN_TOKEN` is configured, send it as:

```text
x-admin-token: your-token
```

Payload:

```json
{
  "schools": [
    {
      "slug": "jattestensskolan",
      "name": "Jättestensskolan",
      "type": "Municipal",
      "grades": "F–9",
      "area": "Jättesten / Biskopsgården side",
      "address": "Norrviksgatan 1, Göteborg",
      "lat": 57.7219,
      "lng": 11.8988,
      "profile": "Local continuity, F–9",
      "qualityScore": 74,
      "admissionScore": 48,
      "dataYear": 2027,
      "lastVerified": "Official 2027 import"
    }
  ]
}
```

## Important limitation

This is still an MVP. It supports backend storage and imported yearly data, but a true production version should add:

- PostgreSQL instead of file-based SQLite on Render.
- A Skolverket importer.
- Göteborg placement-stat parser.
- Real geocoding and route distance.
- Admin UI for source verification.
- Source-level audit trail per metric.
