from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
IMPORT_DIR = DATA_DIR / "imports"
DB_PATH = Path(os.getenv("SCHOOLGUIDE_DB_PATH", DATA_DIR / "schoolguide.sqlite"))
DEFAULT_TARGET_YEAR = int(os.getenv("DEFAULT_TARGET_YEAR", "2027"))

BASELINE_FILE = DATA_DIR / "schools-2026.json"

SCHOOL_FIELDS = [
    "slug", "name", "type", "grades", "area", "address", "lat", "lng", "profile", "sources"
]

METRIC_FIELDS = [
    "qualityScore", "admissionScore", "admissionNote", "f0Satisfaction", "safety", "studyPeace",
    "support", "studentSatisfaction", "parentSatisfaction", "academicSignal", "decisionNote",
    "lastVerified", "verificationNote"
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schools (
                slug TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                type TEXT,
                grades TEXT,
                area TEXT,
                address TEXT,
                lat REAL,
                lng REAL,
                profile TEXT,
                sources_json TEXT DEFAULT '[]',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS school_year_metrics (
                slug TEXT NOT NULL,
                year INTEGER NOT NULL,
                qualityScore REAL,
                admissionScore REAL,
                admissionNote TEXT,
                f0Satisfaction REAL,
                safety REAL,
                studyPeace REAL,
                support REAL,
                studentSatisfaction REAL,
                parentSatisfaction REAL,
                academicSignal TEXT,
                decisionNote TEXT,
                lastVerified TEXT,
                verificationNote TEXT,
                imported_at TEXT NOT NULL,
                PRIMARY KEY (slug, year),
                FOREIGN KEY (slug) REFERENCES schools(slug)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS import_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_label TEXT NOT NULL,
                record_count INTEGER NOT NULL,
                imported_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def load_json_file(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "schools" in payload:
        payload = payload["schools"]
    if not isinstance(payload, list):
        raise ValueError(f"{path.name} must contain a list of school records or an object with a schools array")
    return payload


def upsert_schools(records: Iterable[dict[str, Any]], source_label: str) -> int:
    imported_at = now_iso()
    count = 0
    with db() as conn:
        for item in records:
            slug = item.get("slug")
            year = item.get("dataYear") or item.get("year")
            if not slug or not year:
                raise ValueError("Every record needs slug and dataYear/year")
            sources_json = json.dumps(item.get("sources", []), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO schools (slug, name, type, grades, area, address, lat, lng, profile, sources_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    type=excluded.type,
                    grades=excluded.grades,
                    area=excluded.area,
                    address=excluded.address,
                    lat=excluded.lat,
                    lng=excluded.lng,
                    profile=excluded.profile,
                    sources_json=excluded.sources_json,
                    updated_at=excluded.updated_at
                """,
                (
                    slug,
                    item.get("name"),
                    item.get("type"),
                    item.get("grades"),
                    item.get("area"),
                    item.get("address"),
                    item.get("lat"),
                    item.get("lng"),
                    item.get("profile"),
                    sources_json,
                    imported_at,
                ),
            )
            metric_values = [item.get(field) for field in METRIC_FIELDS]
            conn.execute(
                f"""
                INSERT INTO school_year_metrics (
                    slug, year, {', '.join(METRIC_FIELDS)}, imported_at
                ) VALUES ({', '.join(['?'] * (2 + len(METRIC_FIELDS) + 1))})
                ON CONFLICT(slug, year) DO UPDATE SET
                    {', '.join([f'{field}=excluded.{field}' for field in METRIC_FIELDS])},
                    imported_at=excluded.imported_at
                """,
                [slug, int(year), *metric_values, imported_at],
            )
            count += 1
        conn.execute(
            "INSERT INTO import_log (source_label, record_count, imported_at) VALUES (?, ?, ?)",
            (source_label, count, imported_at),
        )
        conn.commit()
    return count


def bootstrap_database() -> None:
    init_db()
    if BASELINE_FILE.exists():
        upsert_schools(load_json_file(BASELINE_FILE), BASELINE_FILE.name)
    for path in sorted(IMPORT_DIR.glob("schools-*.json")):
        if path.name.endswith(".example.json"):
            continue
        upsert_schools(load_json_file(path), path.name)


def metric_row_for(conn: sqlite3.Connection, slug: str, year_param: str) -> tuple[sqlite3.Row | None, bool, int | None]:
    if year_param == "latest":
        row = conn.execute(
            "SELECT * FROM school_year_metrics WHERE slug=? ORDER BY year DESC LIMIT 1",
            (slug,),
        ).fetchone()
        return row, False, None

    try:
        requested_year = int(year_param)
    except ValueError:
        raise HTTPException(status_code=400, detail="year must be 'latest' or a four-digit year")

    exact = conn.execute(
        "SELECT * FROM school_year_metrics WHERE slug=? AND year=?",
        (slug, requested_year),
    ).fetchone()
    if exact:
        return exact, False, requested_year

    fallback = conn.execute(
        """
        SELECT * FROM school_year_metrics
        WHERE slug=? AND year < ?
        ORDER BY year DESC
        LIMIT 1
        """,
        (slug, requested_year),
    ).fetchone()
    if fallback:
        return fallback, True, requested_year

    any_year = conn.execute(
        "SELECT * FROM school_year_metrics WHERE slug=? ORDER BY year DESC LIMIT 1",
        (slug,),
    ).fetchone()
    return any_year, True, requested_year


def combine_school_and_metric(school: sqlite3.Row, metric: sqlite3.Row, is_fallback: bool, requested_year: int | None) -> dict[str, Any]:
    result = dict(school)
    result["sources"] = json.loads(result.pop("sources_json") or "[]")
    for field in METRIC_FIELDS:
        result[field] = metric[field]
    result["dataYear"] = metric["year"]
    result["requestedDataYear"] = requested_year
    result["isFallback"] = bool(is_fallback)
    result["fallbackLabel"] = (
        f"Using {metric['year']} because {requested_year} data is not imported yet."
        if is_fallback and requested_year else None
    )
    return result


def get_available_years(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT DISTINCT year FROM school_year_metrics ORDER BY year DESC").fetchall()
    return [int(row["year"]) for row in rows]


app = FastAPI(title="Gothenburg School Guide", version="0.4.0")
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.on_event("startup")
def _startup() -> None:
    bootstrap_database()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "0.4.0", "time": now_iso()}


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    with db() as conn:
        available_years = get_available_years(conn)
        latest_year = max(available_years) if available_years else None
        count = conn.execute("SELECT COUNT(*) AS n FROM schools").fetchone()["n"]
        last_import = conn.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "version": "0.4.0",
        "schoolCount": count,
        "defaultTargetYear": DEFAULT_TARGET_YEAR,
        "latestAvailableYear": latest_year,
        "availableYears": available_years,
        "lastImport": dict(last_import) if last_import else None,
        "updateMode": "The API asks for the target year and falls back to the latest imported prior year per school.",
    }


@app.get("/api/schools")
def schools_api(year: str = Query(default=str(DEFAULT_TARGET_YEAR))) -> dict[str, Any]:
    with db() as conn:
        school_rows = conn.execute("SELECT * FROM schools ORDER BY name COLLATE NOCASE").fetchall()
        items = []
        fallback_count = 0
        for school in school_rows:
            metric, fallback, requested_year = metric_row_for(conn, school["slug"], year)
            if not metric:
                continue
            if fallback:
                fallback_count += 1
            items.append(combine_school_and_metric(school, metric, fallback, requested_year))
        available_years = get_available_years(conn)
    return {
        "requestedYear": year,
        "availableYears": available_years,
        "fallbackCount": fallback_count,
        "count": len(items),
        "schools": items,
    }


@app.get("/api/schools/{slug}")
def school_api(slug: str, year: str = Query(default=str(DEFAULT_TARGET_YEAR))) -> dict[str, Any]:
    with db() as conn:
        school = conn.execute("SELECT * FROM schools WHERE slug=?", (slug,)).fetchone()
        if not school:
            raise HTTPException(status_code=404, detail="School not found")
        metric, fallback, requested_year = metric_row_for(conn, slug, year)
        if not metric:
            raise HTTPException(status_code=404, detail="No metrics found for this school")
        history = conn.execute(
            "SELECT year, qualityScore, admissionScore, lastVerified FROM school_year_metrics WHERE slug=? ORDER BY year DESC",
            (slug,),
        ).fetchall()
    item = combine_school_and_metric(school, metric, fallback, requested_year)
    item["history"] = [dict(row) for row in history]
    return item


@app.post("/api/import")
async def import_api(request: Request) -> JSONResponse:
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token:
        supplied = request.headers.get("x-admin-token")
        if supplied != admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")
    payload = await request.json()
    records = payload.get("schools") if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="Send a JSON array or an object with a schools array")
    try:
        count = upsert_schools(records, "manual-api-import")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"ok": True, "imported": count, "time": now_iso()})
