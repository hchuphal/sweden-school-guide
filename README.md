# Sweden School Guide MVP v0.19

FastAPI + SQLite web application for comparing schools in four selectable datasets:

- Göteborg region — Göteborg and Mölndal municipalities
- Stockholm
- Malmö
- Uppsala

## What changed in v0.19

- City directory requests are now cancellable and protected by a request generation token.
- A delayed background refresh from Uppsala, Malmö or another city can no longer overwrite the currently selected city.
- The frontend validates both the response city and every returned school city key before rendering.
- The backend also asserts city isolation for `/api/schools`.
- Survey-sync notices now appear above the card grid instead of becoming the first grid card.


### National survey-data correction

- Imports both **Skolenkäten 2026 and 2025**, because the national survey runs on a two-year cycle and half of Sweden's schools participate each year.
- Uses the official 2025 filename for the förskoleklass guardian file (`fklass` rather than the 2026 `forskoleklass` filename).
- Matches survey rows by school-unit ID first, then conservatively by school name plus municipality.
- Supports bundled city records that do not yet have a Skolverket school-unit ID; a unique survey match can supply the ID.
- Runs survey import independently from the school-registry import. A registry failure therefore no longer prevents survey enrichment.
- Ignores empty year rows when selecting rating data. A blank 2026 registry row can no longer hide an available 2025 survey row.
- Counts only schools with actual published quality metrics in the directory header. A date-only registry row is no longer presented as rating data.
- Exposes separate `registrySync` and `surveySync` status in API responses and displays survey progress/failure clearly in the directory.

### Postcode and nearby-search correction

- A five-digit Swedish postcode entered with or without a space must match the postcode returned by the geocoder.
- Unrelated results with a different postcode are rejected.
- Postcode searches use Nominatim's structured `postalcode` field before free-form fallbacks.
- An address/postcode in another supported dataset automatically switches the city. For example, `41248` searched while Stockholm is selected should resolve to Göteborg rather than an unrelated Stockholm landmark.
- Old incorrect geocoder cache records are bypassed with a new cache version.
- Empty nearby results now report how many loaded schools have coordinates.

### Registry robustness

- Handles link-based and metadata-based pagination shapes from the national school-unit register.
- Bundled records remain the immediate fallback; the live registry adds official IDs, addresses and coordinates when available.

## Data interpretation

Skolenkäten provides survey indicators. Academic results and admission realism are separate datasets. They remain `n/a` when no verified academic-statistics or municipality/school admission import exists; v0.19 does not invent values.

Some published survey cells may also remain `n/a` because a school did not participate in either loaded year or because the authority suppresses results based on too few responses.

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

The included `render.yaml` enables the registry and two-year survey background sync.

## Important endpoints

```text
GET  /api/metadata
GET  /api/schools?city=stockholm&year=current
GET  /api/nearby?q=41248&city=stockholm
POST /api/admin/import/school-registry?surveys=true
POST /api/admin/import/skolenkaten?year=2026&apply=true
POST /api/admin/import/skolenkaten?year=2025&apply=true
```

Protect admin endpoints with `ADMIN_TOKEN` and the `x-admin-token` header.

## Limitations

- Nearby distance is straight-line distance, not walking, driving or transit distance.
- Schools without coordinates cannot appear in nearby results until the registry supplies coordinates.
- The bundled city lists are fallbacks, not guaranteed complete national-register snapshots.
- Live source availability and privacy suppression affect how many survey values can be displayed.
