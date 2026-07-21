# Gothenburg School Guide MVP v0.6

A Render-deployable FastAPI + SQLite MVP for comparing Gothenburg schools for expat families choosing F0/F–9.

## What changed in v0.6

- Removed the confusing hard-coded target year `2027` from the frontend.
- Default year mode is now `current`.
- The backend uses the newest imported official data year as the current year.
- If the current year is missing for a specific school, that school falls back to its latest verified prior-year record.
- The top-directory helper text is cleaner and no longer mentions demo data in the hero area.
- Methodology now explicitly identifies Skolinspektionen Skolenkäten as a recommended primary survey source.

## Data-year behavior

The frontend calls:

```text
/api/schools?year=current
```

The backend resolves `current` like this:

1. Find the newest year that exists in the database, for example 2026.
2. Use that year for all schools where a record exists.
3. For any school missing that current-year record, use the latest verified prior year for that school.
4. Show the data year and confidence on every card.

This means the app will not ask for 2027 before 2027 data has actually been imported. When official 2027 files are later added, 2027 becomes the current year automatically.

## Quality score formula

The backend calculates quality score from rating fields using these weights:

| Component | Weight |
|---|---:|
| F0 parent satisfaction | 20% |
| Safety / trygghet | 20% |
| Study peace / studiero | 15% |
| Support / stöd | 10% |
| Student satisfaction | 10% |
| Parent satisfaction | 10% |
| Academic signal | 10% |
| Data confidence | 5% |

Missing values use a neutral 6.5/10 placeholder and lower the data-confidence component. Admission realism is separate and does not affect quality score.

## Recommended source model

Use separate official sources for separate concepts:

- **Skolinspektionen Skolenkäten**: primary source for survey/rating indicators such as F0 guardian satisfaction, trygghet, studiero, stöd/stimulans and pupil survey values.
- **Skolverket / Utbildningsguiden / Skolverket statistics**: school-unit facts, grade coverage, huvudman/type, teacher ratios, national tests and meritvärde.
- **Göteborg Stad placement statistics**: municipal admission realism, including first-choice placement and sibling-priority share.
- **Individual fristående school pages**: queue rules, preschool priority, sibling priority and application timing.

## Local run

```bash
cd gothenburg-school-guide-v0.6
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open:

```text
http://localhost:8000
```

## Render Web Service

Use:

```text
Build command: pip install -r requirements.txt
Start command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

The included `render.yaml` is ready for a Render Web Service.

## API

```text
/api/health
/api/metadata
/api/methodology
/api/schools
/api/schools?year=current
/api/schools?year=2026
```

## Importing a future year

Add a file such as:

```text
data/imports/schools-2027.json
```

with records using the same schema as `data/schools-2026.json`, then restart/redeploy the service. The current year will become 2027 automatically because it is now the newest imported year.

## Limitation

This MVP does not yet scrape or download Skolinspektionen/Skolverket live. It is import-ready: official data must be transformed into the app schema and imported as JSON, or later handled by a dedicated importer script.
