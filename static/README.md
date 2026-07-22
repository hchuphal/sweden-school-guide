# Sweden School Guide MVP v0.16

FastAPI + SQLite web application for comparing Swedish schools. The current build supports four selectable city datasets:

- Göteborg region — Göteborg and Mölndal municipalities
- Stockholm
- Malmö
- Uppsala

## What changed in v0.16

- All four city options are active.
- Address lookup no longer rejects a matched address merely because the dropdown was set to another city.
- A matched address automatically selects the appropriate loaded city dataset.
- Mölndal addresses are handled inside the Göteborg-region dataset.
- Nearby search is performed by the backend and returns schools within a configurable radius.
- A background importer synchronises school facts from Skolverket's national school-unit register.
- The same pipeline can enrich imported schools with the national Skolinspektionen Skolenkäten files.
- Registry-only schools remain visible even when detailed rating or admission data is unavailable; missing data is shown as `n/a`, not converted into a misleading quality score.

## Data coverage

School name, address, municipality, ownership type, grade coverage and coordinates are imported from Skolverket where available. Survey ratings are imported from Skolinspektionen Skolenkäten when a matching school-unit code exists.

Admission realism remains municipality/school specific. Göteborg seeded schools retain the manually verified Göteborg placement signals. Schools in the other cities show `n/a` until local placement or private-admission rules are imported.

## Local run

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## Render Web Service

```text
Build Command: pip install -r requirements.txt
Start Command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
Health Check Path: /api/health
```

The included `render.yaml` enables the background registry and survey sync.

## Important endpoints

```text
GET  /api/metadata
GET  /api/schools?city=goteborg&year=current
GET  /api/nearby?q=Eklanda%20Äng%2076&city=goteborg
POST /api/admin/import/school-registry?surveys=true
POST /api/admin/import/skolenkaten?year=2026&apply=true
```

Protect admin endpoints by setting `ADMIN_TOKEN` in Render and sending it as the `x-admin-token` header.

## Import manually

```bash
PYTHONPATH=. python scripts/import_school_registry.py
```

Use `--skip-surveys` to import school facts only.

## Limitations

- Nearby distance is straight-line distance, not walking, driving or transit distance.
- The school-register parser is intentionally defensive because the official API is external and may evolve.
- Schools without coordinates cannot appear in nearby results until coordinates are supplied by the registry or a separate school-address geocoder.
- Quality ratings, academic results and admission rules may have different publication years and coverage.


## v0.16 directory fix

- The top-level directory follows the selected city.
- Registry-only schools remain visible even when survey ratings are unavailable.
- Empty city datasets trigger/poll the official registry sync.
- City school counts and rated-data counts are shown.
- Static/API responses use no-cache headers to avoid stale city data after deployment.


## v0.16 bundled city baselines

Stockholm, Malmö and Uppsala now include bundled official-directory fallback records. They load immediately without waiting for the Skolverket API. The live registry sync remains enabled as a refresh/enrichment path. Bundled fallback records may not contain coordinates, ratings or admission statistics until enriched.
