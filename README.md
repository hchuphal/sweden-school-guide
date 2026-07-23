# Sweden School Guide MVP v0.24

FastAPI + SQLite web application for comparing schools in four selectable datasets:

- Göteborg region — Göteborg and Mölndal municipalities
- Stockholm
- Malmö
- Uppsala

## What changed in v0.24

- Nearby results now use order-independent school-entity clustering across tracked and OpenStreetMap records.
- Duplicate detection combines school-unit IDs, OpenStreetMap IDs, Swedish-normalized names, street/house identities and coordinate proximity.
- The richest official/tracked record is retained while map-confirmed distance and sources are merged into it.
- Separate campuses and branches remain separate.
- Added regression tests for Swedish linking-s variants, three-source duplicates and chain campuses.

## Data interpretation

Skolenkäten provides survey indicators. Academic results and admission realism are separate datasets and remain `n/a` when no verified import exists.

A map-discovered school is evidence that a school feature is mapped near the address. Its current grades, ownership and admission rules should be confirmed through the municipality, Skolverket or the school itself.

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

No new environment variables are required. Optional settings:

```text
PHOTON_URL=https://photon.komoot.io/api/
OVERPASS_URLS=https://overpass-api.de/api/interpreter,https://overpass.kumi.systems/api/interpreter
MAP_SCHOOL_CACHE_TTL_SECONDS=86400
```

## Important endpoints

```text
GET  /api/metadata
GET  /api/schools?city=goteborg&year=current
GET  /api/nearby?q=Gräddgatan%205&city=goteborg
GET  /api/nearby?q=41248&city=stockholm
POST /api/admin/import/school-registry?surveys=true
```

## Limitations

- Distance remains straight-line distance, not walking, driving or transit distance.
- OpenStreetMap can be incomplete or contain stale tags.
- Map-only schools may lack verified grade and ownership information.
- The live map-discovery radius is capped at 12 km to keep Overpass queries reliable.
- Bundled city directories remain fallbacks rather than complete national-register snapshots.
