# Sweden School Guide MVP v0.20

FastAPI + SQLite web application for comparing schools in four selectable datasets:

- Göteborg region — Göteborg and Mölndal municipalities
- Stockholm
- Malmö
- Uppsala

## What changed in v0.20

### Nearby searches no longer depend on the partial tracked list

The nearby endpoint now combines two sources:

1. Tracked records already stored from Skolverket, municipal directories and bundled fallbacks.
2. Schools discovered around the matched coordinates through OpenStreetMap Overpass.

The two result sets are deduplicated by school name and proximity, then sorted strictly by straight-line distance. Map-only schools are clearly marked and receive no invented quality, academic or admission values.

Map discovery is cached in SQLite for 24 hours and tries two Overpass endpoints. It searches up to 12 km around the address; tracked records can still be returned from the full selected radius.

### Göteborg/Kallebäck correction

`Fridaskolan Kallebäck`, school-unit ID `71240644`, is included as an official bundled fallback with:

- Kallebäcks Torggata 32, 412 77 Göteborg
- F–9
- coordinates for nearby ranking
- published F0 guardian survey values for spring 2026

This makes it available even if the national registry background synchronisation is incomplete.

### Postcode and full-address correction

- Postcode-only searches try Nominatim structured search, city-aware free-form searches and Photon.
- Known/derived postcode centroids provide an explicitly approximate fallback when public geocoders return no exact postcode point.
- `412 48` is mapped to the Göteborg dataset and searches from an approximate postcode centre when necessary.
- A full street address is not discarded merely because the map provider reports a neighbouring or outdated postcode. The street and house-number match is used and the UI displays a warning.
- Geocoder cache versioning bypasses prior incorrect results.

### Existing v0.19 protections retained

- City requests are cancellable and stale responses cannot overwrite the selected directory.
- The backend and frontend validate city isolation.
- Skolenkäten 2025 and 2026 enrichment remains separate from the school-registry import.

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
