from __future__ import annotations

import json
import os
import sqlite3
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.skolenkaten_importer import (
    build_skolenkaten_import,
    ensure_source_files,
    load_baseline,
    write_import_json,
)

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
IMPORT_DIR = DATA_DIR / "imports"
DB_PATH = Path(os.getenv("SCHOOLGUIDE_DB_PATH", DATA_DIR / "schoolguide.sqlite"))
DEFAULT_YEAR_MODE = os.getenv("DEFAULT_YEAR_MODE", "current").strip().lower()
BASELINE_FILE = DATA_DIR / "schools-2026.json"
GEOCODER_URL = os.getenv("GEOCODER_URL", "https://nominatim.openstreetmap.org/search")
GEOCODER_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "SwedenSchoolGuide/0.12")
GEOCODER_EMAIL = os.getenv("GEOCODER_EMAIL", "").strip()
CITY_CONFIG = {
    "goteborg": {
        "label": "Göteborg",
        "aliases": {"goteborg", "göteborg", "gothenburg", "goteborgs kommun", "göteborgs kommun"},
    }
}
APP_VERSION = "0.12.0"

QUALITY_METHOD_VERSION = "v0.12 survey, academic and admission UI with live address geocoding"
MISSING_VALUE_BASELINE = 6.5

QUALITY_COMPONENTS = [
    {
        "key": "f0Satisfaction",
        "label": "F0 parent satisfaction",
        "weight": 20,
        "description": "Most relevant signal for families choosing förskoleklass.",
    },
    {
        "key": "safety",
        "label": "Safety / trygghet",
        "weight": 20,
        "description": "How safe pupils/guardians report the school environment to be.",
    },
    {
        "key": "studyPeace",
        "label": "Study peace / studiero",
        "weight": 15,
        "description": "Classroom calm and ability to work without disruption.",
    },
    {
        "key": "support",
        "label": "Support / stöd",
        "weight": 10,
        "description": "Whether pupils feel they get help and support when needed.",
    },
    {
        "key": "studentSatisfaction",
        "label": "Student satisfaction",
        "weight": 10,
        "description": "Older-pupil view, usually grade 5 or grade 8 depending on available data.",
    },
    {
        "key": "parentSatisfaction",
        "label": "Parent satisfaction",
        "weight": 10,
        "description": "Guardian view of the school beyond F0 where available.",
    },
    {
        "key": "academicScore",
        "label": "Academic signal",
        "weight": 10,
        "description": "Normalised signal from merit values, national tests or verified academic indicators.",
    },
]
DATA_CONFIDENCE_WEIGHT = 5
TOTAL_COMPONENT_WEIGHT = sum(item["weight"] for item in QUALITY_COMPONENTS)
TOTAL_QUALITY_WEIGHT = TOTAL_COMPONENT_WEIGHT + DATA_CONFIDENCE_WEIGHT

SCHOOL_FIELDS = [
    "slug", "name", "type", "grades", "area", "address", "lat", "lng", "profile", "sources"
]

METRIC_FIELDS = [
    "qualityScore", "admissionScore", "admissionNote", "f0Satisfaction", "safety", "studyPeace",
    "support", "studentSatisfaction", "parentSatisfaction", "academicSignal", "academicScore",
    "decisionNote", "lastVerified", "verificationNote"
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
                school_unit_id INTEGER,
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
                academicScore REAL,
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS geocode_cache (
                cache_key TEXT PRIMARY KEY,
                query_text TEXT NOT NULL,
                city_key TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Lightweight migration for databases created by previous MVP versions.
        school_columns = {row["name"] for row in conn.execute("PRAGMA table_info(schools)").fetchall()}
        if "school_unit_id" not in school_columns:
            conn.execute("ALTER TABLE schools ADD COLUMN school_unit_id INTEGER")
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(school_year_metrics)").fetchall()}
        if "academicScore" not in columns:
            conn.execute("ALTER TABLE school_year_metrics ADD COLUMN academicScore REAL")
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
                INSERT INTO schools (slug, name, type, grades, area, address, lat, lng, profile, school_unit_id, sources_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    type=excluded.type,
                    grades=excluded.grades,
                    area=excluded.area,
                    address=excluded.address,
                    lat=excluded.lat,
                    lng=excluded.lng,
                    profile=excluded.profile,
                    school_unit_id=excluded.school_unit_id,
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
                    item.get("schoolUnitId"),
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


def coerce_score(value: Any) -> float | None:
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score < 0:
        return 0.0
    if score > 10:
        # Accept accidental 0-100 imports and normalise to 0-10.
        if score <= 100:
            return score / 10
        return 10.0
    return score


def calculate_quality(metric: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    components: list[dict[str, Any]] = []
    available_weight = 0.0
    weighted_points = 0.0
    missing_keys: list[str] = []

    for component in QUALITY_COMPONENTS:
        key = component["key"]
        weight = float(component["weight"])
        raw_value = metric[key] if isinstance(metric, sqlite3.Row) else metric.get(key)
        score_0_to_10 = coerce_score(raw_value)
        is_missing = score_0_to_10 is None
        used_score = MISSING_VALUE_BASELINE if is_missing else score_0_to_10
        contribution = (used_score / 10) * weight
        weighted_points += contribution
        if is_missing:
            missing_keys.append(key)
        else:
            available_weight += weight
        components.append(
            {
                "key": key,
                "label": component["label"],
                "weight": weight,
                "value": round(score_0_to_10, 1) if score_0_to_10 is not None else None,
                "usedValue": round(used_score, 1),
                "status": "missing-neutral-baseline" if is_missing else "available",
                "contribution": round(contribution, 2),
                "description": component["description"],
            }
        )

    completeness = available_weight / TOTAL_COMPONENT_WEIGHT if TOTAL_COMPONENT_WEIGHT else 0
    confidence_component = completeness * DATA_CONFIDENCE_WEIGHT
    final_score = weighted_points + confidence_component
    final_score = max(0, min(100, final_score))

    if completeness >= 0.85:
        confidence_label = "High"
    elif completeness >= 0.55:
        confidence_label = "Medium"
    else:
        confidence_label = "Low"

    return {
        "qualityScore": round(final_score),
        "qualityScoreExact": round(final_score, 2),
        "qualityMethodVersion": QUALITY_METHOD_VERSION,
        "dataCompletenessPct": round(completeness * 100),
        "dataConfidenceLabel": confidence_label,
        "missingQualityFields": missing_keys,
        "qualityBreakdown": components,
        "qualityFormula": {
            "weights": [
                {"key": item["key"], "label": item["label"], "weight": item["weight"]}
                for item in QUALITY_COMPONENTS
            ],
            "dataConfidenceWeight": DATA_CONFIDENCE_WEIGHT,
            "missingValueBaseline": MISSING_VALUE_BASELINE,
            "note": "Missing quality metrics use a neutral 6.5/10 placeholder and reduce the data-confidence component. Admission realism is not included in this score.",
        },
    }


def resolve_year_param(conn: sqlite3.Connection, year_param: str | None) -> tuple[int | None, str]:
    """Resolve the UI/API year mode into the current imported data year.

    Default mode is `current`: use the newest year that exists in the database.
    This avoids asking for a future year before official data has been imported.
    When a newer official import appears later, `current` automatically moves to that year.
    """
    clean = (year_param or "current").strip().lower()
    row = conn.execute("SELECT MAX(year) AS year FROM school_year_metrics").fetchone()
    latest_year = int(row["year"]) if row and row["year"] is not None else None
    if clean in {"current", "latest", "auto"}:
        return latest_year, clean
    try:
        return int(clean), "explicit"
    except ValueError:
        raise HTTPException(status_code=400, detail="year must be 'current', 'latest', 'auto' or a four-digit year")


def metric_row_for(conn: sqlite3.Connection, slug: str, year_param: str | None) -> tuple[sqlite3.Row | None, bool, int | None]:
    requested_year, _mode = resolve_year_param(conn, year_param)
    if requested_year is None:
        return None, False, None

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
    return any_year, bool(any_year), requested_year


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

    # Keep the old seeded/manual value visible for auditing, but do not use it as the displayed quality score.
    result["editorialQualityScore"] = metric["qualityScore"]
    computed = calculate_quality(metric)
    result.update(computed)
    return result



def normalize_location_text(value: str | None) -> str:
    clean = (value or "").strip().lower()
    clean = clean.replace("å", "a").replace("ä", "a").replace("ö", "o")
    clean = re.sub(r"[^a-z0-9]+", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


def geocode_cache_key(query: str, city_key: str) -> str:
    return f"{city_key}:{normalize_location_text(query)}"


def result_locality_values(result: dict[str, Any]) -> list[str]:
    address = result.get("address") or {}
    fields = [
        "municipality", "city", "town", "village", "borough", "city_district",
        "suburb", "county", "state_district"
    ]
    return [str(address.get(field)) for field in fields if address.get(field)]


def result_matches_city(result: dict[str, Any], city_key: str) -> bool:
    config = CITY_CONFIG.get(city_key)
    if not config:
        return False
    aliases = {normalize_location_text(value) for value in config["aliases"]}
    locality_values = {normalize_location_text(value) for value in result_locality_values(result)}
    for locality in locality_values:
        if locality in aliases:
            return True
        if any(alias and alias in locality for alias in aliases):
            return True
    return False


def detected_municipality(result: dict[str, Any]) -> str | None:
    address = result.get("address") or {}
    return (
        address.get("municipality")
        or address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
    )


def fetch_geocode(query: str, city_key: str) -> dict[str, Any]:
    clean_query = query.strip()
    if not clean_query:
        raise HTTPException(status_code=400, detail="Enter an address or postal code")
    if city_key not in CITY_CONFIG:
        raise HTTPException(status_code=400, detail="The selected city dataset is not available yet")

    key = geocode_cache_key(clean_query, city_key)
    with db() as conn:
        cached = conn.execute(
            "SELECT result_json FROM geocode_cache WHERE cache_key=?", (key,)
        ).fetchone()
        if cached:
            payload = json.loads(cached["result_json"])
            payload["cached"] = True
            return payload

    search_query = clean_query
    if "sweden" not in normalize_location_text(search_query) and "sverige" not in normalize_location_text(search_query):
        search_query = f"{search_query}, Sweden"
    params: dict[str, Any] = {
        "q": search_query,
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 5,
        "countrycodes": "se",
        "accept-language": "sv,en",
    }
    if GEOCODER_EMAIL:
        params["email"] = GEOCODER_EMAIL
    headers = {
        "User-Agent": GEOCODER_USER_AGENT,
        "Accept": "application/json",
    }
    try:
        response = requests.get(GEOCODER_URL, params=params, headers=headers, timeout=12)
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail="Address lookup is temporarily unavailable. Try again shortly.",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Address provider returned an invalid response") from exc

    if not isinstance(results, list) or not results:
        payload = {
            "found": False,
            "insideSelectedCity": False,
            "selectedCity": CITY_CONFIG[city_key]["label"],
            "query": clean_query,
            "message": "No Swedish address or postal code matched the search.",
            "provider": "OpenStreetMap Nominatim",
        }
    else:
        inside = [result for result in results if result_matches_city(result, city_key)]
        chosen = inside[0] if inside else results[0]
        address = chosen.get("address") or {}
        try:
            lat = float(chosen["lat"])
            lng = float(chosen["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Address provider did not return coordinates") from exc
        inside_selected = result_matches_city(chosen, city_key)
        municipality = detected_municipality(chosen)
        message = (
            f"Address matched within {CITY_CONFIG[city_key]['label']}."
            if inside_selected
            else f"This address appears to be in {municipality or 'another municipality'}, not {CITY_CONFIG[city_key]['label']}."
        )
        payload = {
            "found": True,
            "insideSelectedCity": inside_selected,
            "selectedCity": CITY_CONFIG[city_key]["label"],
            "query": clean_query,
            "displayName": chosen.get("display_name") or clean_query,
            "lat": lat,
            "lng": lng,
            "postalCode": address.get("postcode"),
            "municipality": municipality,
            "resultType": chosen.get("type"),
            "message": message,
            "provider": "OpenStreetMap Nominatim",
        }

    with db() as conn:
        conn.execute(
            """
            INSERT INTO geocode_cache (cache_key, query_text, city_key, result_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                result_json=excluded.result_json,
                created_at=excluded.created_at
            """,
            (key, clean_query, city_key, json.dumps(payload, ensure_ascii=False), now_iso()),
        )
        conn.commit()
    payload["cached"] = False
    return payload


def get_available_years(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute("SELECT DISTINCT year FROM school_year_metrics ORDER BY year DESC").fetchall()
    return [int(row["year"]) for row in rows]


app = FastAPI(title="Sweden School Guide", version=APP_VERSION)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.on_event("startup")
def _startup() -> None:
    bootstrap_database()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": APP_VERSION, "time": now_iso()}


@app.get("/api/geocode")
def geocode_api(
    q: str = Query(min_length=3, max_length=220),
    city: str = Query(default="goteborg"),
) -> dict[str, Any]:
    return fetch_geocode(q, city.strip().lower())


@app.get("/api/methodology")
def methodology() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "qualityMethodVersion": QUALITY_METHOD_VERSION,
        "qualityFormula": calculate_quality({})["qualityFormula"],
        "admissionNote": "Admission realism is separate from quality. Municipal values can use Göteborg placement statistics; private-school values should be based on queue rules and verified local knowledge.",
        "yearFallback": "Default year mode is current: the API uses the newest imported official data year and falls back per school only when that current-year record is missing.",
        "recommendedSources": [
            "Skolinspektionen Skolenkäten Excel files for survey ratings: F0 guardians, grundskola guardians, pupils grade 5/8 and teachers.",
            "Skolverket/Utbildningsguiden and national statistics for school-unit facts, teacher ratios and academic results.",
            "Göteborg Stad placement statistics for municipal admission realism.",
            "Individual fristående school pages for queue and admission rules."
        ],
    }


@app.get("/api/metadata")
def metadata() -> dict[str, Any]:
    with db() as conn:
        available_years = get_available_years(conn)
        latest_year = max(available_years) if available_years else None
        count = conn.execute("SELECT COUNT(*) AS n FROM schools").fetchone()["n"]
        last_import = conn.execute("SELECT * FROM import_log ORDER BY id DESC LIMIT 1").fetchone()
    return {
        "version": APP_VERSION,
        "schoolCount": count,
        "defaultYearMode": DEFAULT_YEAR_MODE,
        "currentDataYear": latest_year,
        "latestAvailableYear": latest_year,
        "availableYears": available_years,
        "lastImport": dict(last_import) if last_import else None,
        "qualityMethodVersion": QUALITY_METHOD_VERSION,
        "qualityFormula": calculate_quality({})["qualityFormula"],
        "updateMode": "Default mode uses the current imported data year. If a current-year record is missing for a school, the API falls back to the latest prior verified year for that school.",
        "geocoding": {"provider": "OpenStreetMap Nominatim", "supportedCities": ["goteborg"], "cache": True},
    }


@app.get("/api/schools")
def schools_api(year: str = Query(default=DEFAULT_YEAR_MODE)) -> dict[str, Any]:
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
        current_year = max(available_years) if available_years else None
        resolved_year, year_mode = resolve_year_param(conn, year)
    return {
        "requestedYear": year,
        "resolvedYear": resolved_year,
        "yearMode": year_mode,
        "currentDataYear": current_year,
        "availableYears": available_years,
        "fallbackCount": fallback_count,
        "count": len(items),
        "schools": items,
    }


@app.get("/api/schools/{slug}")
def school_api(slug: str, year: str = Query(default=DEFAULT_YEAR_MODE)) -> dict[str, Any]:
    with db() as conn:
        school = conn.execute("SELECT * FROM schools WHERE slug=?", (slug,)).fetchone()
        if not school:
            raise HTTPException(status_code=404, detail="School not found")
        metric, fallback, requested_year = metric_row_for(conn, slug, year)
        if not metric:
            raise HTTPException(status_code=404, detail="No metrics found for this school")
        history = conn.execute(
            "SELECT year, qualityScore, admissionScore, f0Satisfaction, safety, studyPeace, support, studentSatisfaction, parentSatisfaction, academicScore, lastVerified FROM school_year_metrics WHERE slug=? ORDER BY year DESC",
            (slug,),
        ).fetchall()
    item = combine_school_and_metric(school, metric, fallback, requested_year)
    history_items = []
    for row in history:
        history_dict = dict(row)
        computed = calculate_quality(history_dict)
        history_dict["editorialQualityScore"] = history_dict.pop("qualityScore")
        history_dict["qualityScore"] = computed["qualityScore"]
        history_dict["dataCompletenessPct"] = computed["dataCompletenessPct"]
        history_items.append(history_dict)
    item["history"] = history_items
    return item


@app.post("/api/admin/import/skolenkaten")
def import_skolenkaten_api(request: Request, year: int = Query(default=2026), apply: bool = Query(default=True)) -> JSONResponse:
    """Download and import Skolinspektionen Skolenkäten survey Excel files.

    This endpoint updates the running SQLite database and writes a JSON import file under
    data/imports/ so the same import can be replayed at startup. In production, protect it
    with ADMIN_TOKEN and call it after Skolinspektionen publishes a new annual result.
    """
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token:
        supplied = request.headers.get("x-admin-token")
        if supplied != admin_token:
            raise HTTPException(status_code=401, detail="Invalid admin token")
    try:
        cache_dir = DATA_DIR / "source_cache" / "skolenkaten"
        baseline_path = DATA_DIR / f"schools-{year}.json"
        if not baseline_path.exists():
            baseline_path = BASELINE_FILE
        baseline = load_baseline(baseline_path)
        source_files = ensure_source_files(year, cache_dir, use_network=True)
        records, import_metadata = build_skolenkaten_import(baseline, source_files, year=year)
        output_path = DATA_DIR / "imports" / f"schools-{year}-skolenkaten.json"
        write_import_json(records, output_path, import_metadata)
        imported = upsert_schools(records, output_path.name) if apply else 0
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({
        "ok": True,
        "applied": apply,
        "imported": imported,
        "output": str(output_path.relative_to(ROOT)),
        "metadata": import_metadata,
        "time": now_iso(),
    })


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
    return JSONResponse({"ok": True, "imported": count, "time": now_iso(), "qualityMethodVersion": QUALITY_METHOD_VERSION})
