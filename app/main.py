from __future__ import annotations

import json
import os
import sqlite3
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.school_registry_importer import build_records as build_registry_records, fetch_school_units

from app.skolenkaten_importer import (
    SKOLENKATEN_URLS,
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
GEOCODER_USER_AGENT = os.getenv("GEOCODER_USER_AGENT", "SwedenSchoolGuide/0.19")
GEOCODER_EMAIL = os.getenv("GEOCODER_EMAIL", "").strip()
SCHOOL_REGISTRY_URL = os.getenv(
    "SCHOOL_REGISTRY_URL",
    "https://api.skolverket.se/skolenhetsregistret/v2/school-units",
)
AUTO_IMPORT_SCHOOL_REGISTRY = os.getenv("AUTO_IMPORT_SCHOOL_REGISTRY", "true").strip().lower() in {"1", "true", "yes", "on"}
AUTO_IMPORT_SKOLENKATEN = os.getenv("AUTO_IMPORT_SKOLENKATEN", "true").strip().lower() in {"1", "true", "yes", "on"}
REGISTRY_SYNC_RETRY_SECONDS = int(os.getenv("REGISTRY_SYNC_RETRY_SECONDS", "120"))
SURVEY_SYNC_RETRY_SECONDS = int(os.getenv("SURVEY_SYNC_RETRY_SECONDS", "300"))
GEOCODER_CACHE_VERSION = "v3-postcode-strict"
_REGISTRY_SYNC_LOCK = threading.Lock()
_SURVEY_SYNC_LOCK = threading.Lock()
_LAST_REGISTRY_SYNC_START = 0.0
_LAST_SURVEY_SYNC_START = 0.0
CITY_CONFIG = {
    "goteborg": {
        "label": "Göteborg",
        "municipality_codes": {"1480", "1481"},
        "municipality_aliases": {
            "goteborg", "göteborg", "gothenburg", "goteborgs kommun", "göteborgs kommun",
            "goteborgs stad", "göteborgs stad", "molndal", "mölndal", "molndals kommun", "mölndals kommun",
        },
        "search_aliases": {"goteborg", "göteborg", "gothenburg", "molndal", "mölndal"},
    },
    "stockholm": {
        "label": "Stockholm",
        "municipality_codes": {"0180"},
        "municipality_aliases": {"stockholm", "stockholms kommun", "stockholms stad"},
        "search_aliases": {"stockholm"},
    },
    "malmo": {
        "label": "Malmö",
        "municipality_codes": {"1280"},
        "municipality_aliases": {"malmo", "malmö", "malmo kommun", "malmö kommun", "malmo stad", "malmö stad"},
        "search_aliases": {"malmo", "malmö"},
    },
    "uppsala": {
        "label": "Uppsala",
        "municipality_codes": {"0380"},
        "municipality_aliases": {"uppsala", "uppsala kommun"},
        "search_aliases": {"uppsala"},
    },
}
APP_VERSION = "0.19.0"

QUALITY_METHOD_VERSION = "v0.19 city-isolated directory loading, two-year national survey matching and strict postcode geocoding"
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
    "slug", "name", "type", "grades", "area", "address", "lat", "lng", "profile", "sources",
    "cityKey", "municipality", "postalCode", "registrySource"
]

METRIC_FIELDS = [
    "qualityScore", "admissionScore", "admissionNote", "f0Satisfaction", "safety", "studyPeace",
    "support", "studentSatisfaction", "parentSatisfaction", "academicSignal", "academicScore",
    "decisionNote", "lastVerified", "verificationNote"
]

SUBSTANTIVE_METRIC_FIELDS = [
    "admissionScore", "f0Satisfaction", "safety", "studyPeace", "support",
    "studentSatisfaction", "parentSatisfaction", "academicScore"
]
QUALITY_SIGNAL_FIELDS = [
    "f0Satisfaction", "safety", "studyPeace", "support",
    "studentSatisfaction", "parentSatisfaction", "academicScore"
]
SUBSTANTIVE_SQL = " OR ".join(f"{field} IS NOT NULL" for field in SUBSTANTIVE_METRIC_FIELDS)
QUALITY_SIGNAL_SQL = " OR ".join(f"{field} IS NOT NULL" for field in QUALITY_SIGNAL_FIELDS)


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
                city_key TEXT,
                municipality TEXT,
                postal_code TEXT,
                registry_source TEXT,
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS system_state (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Lightweight migration for databases created by previous MVP versions.
        school_columns = {row["name"] for row in conn.execute("PRAGMA table_info(schools)").fetchall()}
        if "school_unit_id" not in school_columns:
            conn.execute("ALTER TABLE schools ADD COLUMN school_unit_id INTEGER")
        for column_name, column_type in (
            ("city_key", "TEXT"),
            ("municipality", "TEXT"),
            ("postal_code", "TEXT"),
            ("registry_source", "TEXT"),
        ):
            if column_name not in school_columns:
                conn.execute(f"ALTER TABLE schools ADD COLUMN {column_name} {column_type}")
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
                INSERT INTO schools (slug, name, type, grades, area, address, lat, lng, profile, city_key, municipality, postal_code, registry_source, school_unit_id, sources_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name,
                    type=excluded.type,
                    grades=excluded.grades,
                    area=excluded.area,
                    address=excluded.address,
                    lat=excluded.lat,
                    lng=excluded.lng,
                    profile=excluded.profile,
                    city_key=excluded.city_key,
                    municipality=excluded.municipality,
                    postal_code=excluded.postal_code,
                    registry_source=excluded.registry_source,
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
                    item.get("cityKey") or "goteborg",
                    item.get("municipality") or ("Göteborg" if (item.get("cityKey") or "goteborg") == "goteborg" else None),
                    item.get("postalCode"),
                    item.get("registrySource") or "seeded",
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

    if available_weight == 0:
        return {
            "qualityScore": None,
            "qualityScoreExact": None,
            "qualityMethodVersion": QUALITY_METHOD_VERSION,
            "dataCompletenessPct": 0,
            "dataConfidenceLabel": "Low",
            "missingQualityFields": missing_keys,
            "qualityBreakdown": components,
            "qualityFormula": {
                "weights": [
                    {"key": item["key"], "label": item["label"], "weight": item["weight"]}
                    for item in QUALITY_COMPONENTS
                ],
                "dataConfidenceWeight": DATA_CONFIDENCE_WEIGHT,
                "missingValueBaseline": MISSING_VALUE_BASELINE,
                "note": "No quality score is shown until at least one quality metric is available. Admission realism is separate.",
            },
        }

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
    """Return the newest row that contains actual rating, academic or admission data.

    Earlier versions treated a row containing only a verification date as rating data.
    That blocked the intended 2025 fallback and produced cards labelled 2026 with 0% data.
    """
    requested_year, _mode = resolve_year_param(conn, year_param)
    if requested_year is None:
        return None, False, None

    exact = conn.execute(
        f"SELECT * FROM school_year_metrics WHERE slug=? AND year=? AND ({SUBSTANTIVE_SQL})",
        (slug, requested_year),
    ).fetchone()
    if exact:
        return exact, False, requested_year

    fallback = conn.execute(
        f"""
        SELECT * FROM school_year_metrics
        WHERE slug=? AND year < ? AND ({SUBSTANTIVE_SQL})
        ORDER BY year DESC
        LIMIT 1
        """,
        (slug, requested_year),
    ).fetchone()
    if fallback:
        return fallback, True, requested_year

    any_year = conn.execute(
        f"SELECT * FROM school_year_metrics WHERE slug=? AND ({SUBSTANTIVE_SQL}) ORDER BY year DESC LIMIT 1",
        (slug,),
    ).fetchone()
    return any_year, bool(any_year and requested_year and any_year["year"] != requested_year), requested_year


def combine_school_and_metric(school: sqlite3.Row, metric: sqlite3.Row, is_fallback: bool, requested_year: int | None) -> dict[str, Any]:
    result = dict(school)
    result["sources"] = json.loads(result.pop("sources_json") or "[]")
    result["cityKey"] = result.get("city_key")
    result["postalCode"] = result.get("postal_code")
    result["registrySource"] = result.get("registry_source")
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


def combine_school_without_metric(school: sqlite3.Row, requested_year: int | None) -> dict[str, Any]:
    """Return an official registry school even when ratings are not imported yet."""
    result = dict(school)
    result["sources"] = json.loads(result.pop("sources_json") or "[]")
    result["cityKey"] = result.get("city_key")
    result["postalCode"] = result.get("postal_code")
    result["registrySource"] = result.get("registry_source")
    for field in METRIC_FIELDS:
        result[field] = None
    result.update({
        "dataYear": None,
        "requestedDataYear": requested_year,
        "isFallback": False,
        "fallbackLabel": None,
        "editorialQualityScore": None,
        "qualityScore": None,
        "qualityBreakdown": [],
        "dataCompletenessPct": 0,
        "dataConfidenceLabel": "No rating data",
        "missingQualityFields": [item["key"] for item in QUALITY_COMPONENTS],
        "verificationNote": "Official school-registry record; detailed survey and academic data are not yet imported.",
        "decisionNote": "Official school-registry record. Open the school sources and verify current grades, profile and admission rules.",
        "admissionNote": "Admission data is not yet imported for this school.",
        "academicSignal": "Academic result data is not yet imported for this school.",
    })
    return result




def set_state(key: str, value: dict[str, Any]) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO system_state (key, value_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at
            """,
            (key, json.dumps(value, ensure_ascii=False), now_iso()),
        )
        conn.commit()


def get_state(key: str) -> dict[str, Any] | None:
    with db() as conn:
        row = conn.execute("SELECT value_json, updated_at FROM system_state WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    payload = json.loads(row["value_json"])
    payload["updatedAt"] = row["updated_at"]
    return payload


def existing_slug_for_registry(conn: sqlite3.Connection, item: dict[str, Any]) -> str:
    school_unit_id = item.get("schoolUnitId")
    if school_unit_id is not None:
        row = conn.execute("SELECT slug FROM schools WHERE school_unit_id=? LIMIT 1", (school_unit_id,)).fetchone()
        if row:
            return str(row["slug"])
    name_key = normalize_location_text(item.get("name"))
    city_key = item.get("cityKey")
    for row in conn.execute("SELECT slug, name FROM schools WHERE city_key=?", (city_key,)).fetchall():
        if normalize_location_text(row["name"]) == name_key:
            return str(row["slug"])
    return str(item["slug"])


def upsert_registry_schools(records: Iterable[dict[str, Any]], source_label: str, year: int) -> int:
    imported_at = now_iso()
    count = 0
    with db() as conn:
        for raw in records:
            item = dict(raw)
            item["slug"] = existing_slug_for_registry(conn, item)
            sources_json = json.dumps(item.get("sources", []), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO schools (slug, name, type, grades, area, address, lat, lng, profile, city_key, municipality, postal_code, registry_source, school_unit_id, sources_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    name=excluded.name, type=excluded.type, grades=excluded.grades, area=excluded.area,
                    address=excluded.address, lat=COALESCE(excluded.lat, schools.lat), lng=COALESCE(excluded.lng, schools.lng),
                    profile=CASE WHEN schools.registry_source='seeded' THEN schools.profile ELSE excluded.profile END,
                    city_key=excluded.city_key, municipality=excluded.municipality, postal_code=excluded.postal_code,
                    registry_source=excluded.registry_source, school_unit_id=excluded.school_unit_id,
                    sources_json=CASE WHEN schools.registry_source='seeded' THEN schools.sources_json ELSE excluded.sources_json END,
                    updated_at=excluded.updated_at
                """,
                (
                    item["slug"], item.get("name"), item.get("type"), item.get("grades"), item.get("area"),
                    item.get("address"), item.get("lat"), item.get("lng"), item.get("profile"), item.get("cityKey"),
                    item.get("municipality"), item.get("postalCode"), item.get("registrySource"), item.get("schoolUnitId"),
                    sources_json, imported_at,
                ),
            )
            # Registry-only schools receive a year row so they are visible immediately. Existing survey/academic rows are preserved.
            conn.execute(
                """
                INSERT INTO school_year_metrics (slug, year, lastVerified, verificationNote, imported_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(slug, year) DO UPDATE SET
                    lastVerified=COALESCE(school_year_metrics.lastVerified, excluded.lastVerified),
                    verificationNote=COALESCE(school_year_metrics.verificationNote, excluded.verificationNote)
                """,
                (item["slug"], year, item.get("lastVerified"), item.get("verificationNote"), imported_at),
            )
            count += 1
        conn.execute(
            "INSERT INTO import_log (source_label, record_count, imported_at) VALUES (?, ?, ?)",
            (source_label, count, imported_at),
        )
        conn.commit()
    return count


def database_baseline(year: int) -> list[dict[str, Any]]:
    """Build a survey-import baseline for every bundled or registry school.

    School-unit IDs are optional because the survey importer can also match
    conservatively by school name plus municipality.
    """
    with db() as conn:
        schools = conn.execute("SELECT * FROM schools ORDER BY city_key, name").fetchall()
        result: list[dict[str, Any]] = []
        for school in schools:
            metric = conn.execute(
                "SELECT * FROM school_year_metrics WHERE slug=? AND year=?",
                (school["slug"], year),
            ).fetchone()
            item = {
                "slug": school["slug"], "name": school["name"], "type": school["type"],
                "grades": school["grades"], "area": school["area"], "address": school["address"],
                "lat": school["lat"], "lng": school["lng"], "profile": school["profile"],
                "schoolUnitId": school["school_unit_id"], "cityKey": school["city_key"],
                "municipality": school["municipality"], "postalCode": school["postal_code"],
                "registrySource": school["registry_source"],
                "sources": json.loads(school["sources_json"] or "[]"), "dataYear": year,
            }
            if metric:
                for field in METRIC_FIELDS:
                    item[field] = metric[field]
            result.append(item)
    return result


def run_registry_sync(import_surveys: bool = False) -> dict[str, Any]:
    """Refresh school-unit facts for the four configured city datasets.

    Survey enrichment is deliberately a separate job. A registry outage must not
    prevent the 2025/2026 survey files from enriching the bundled city records.
    """
    year = datetime.now(timezone.utc).year
    set_state("registry_sync", {"status": "running", "startedAt": now_iso()})
    try:
        items, fetch_meta = fetch_school_units(SCHOOL_REGISTRY_URL, GEOCODER_USER_AGENT)
        records, parse_meta = build_registry_records(items, CITY_CONFIG, year)
        imported = upsert_registry_schools(records, "Skolverket school-unit register", year)
        result = {
            "status": "complete",
            "year": year,
            "registryImported": imported,
            "fetch": fetch_meta,
            "parse": parse_meta,
            "finishedAt": now_iso(),
        }
        set_state("registry_sync", result)
    except Exception as exc:
        result = {"status": "failed", "error": str(exc), "finishedAt": now_iso()}
        set_state("registry_sync", result)

    # Keep this compatibility flag for the existing admin endpoint, but run the
    # survey job independently so a registry failure cannot suppress it.
    if import_surveys and AUTO_IMPORT_SKOLENKATEN:
        result["surveySyncStarted"] = start_background_survey_if_needed(force=True)
    return result


def run_survey_sync(years: list[int] | None = None) -> dict[str, Any]:
    """Import the two-year national Skolenkäten cycle for all loaded schools."""
    configured_years = sorted(SKOLENKATEN_URLS.keys(), reverse=True)
    selected_years = [int(year) for year in (years or configured_years) if int(year) in SKOLENKATEN_URLS]
    if not selected_years:
        result = {"status": "failed", "error": "No supported Skolenkäten years were requested.", "finishedAt": now_iso()}
        set_state("survey_sync", result)
        return result

    set_state("survey_sync", {"status": "running", "years": selected_years, "startedAt": now_iso()})
    per_year: list[dict[str, Any]] = []
    total_applied = 0
    successful_years = 0
    for year in selected_years:
        try:
            cache_dir = DATA_DIR / "source_cache" / "skolenkaten"
            baseline = database_baseline(year)
            source_files = ensure_source_files(year, cache_dir, use_network=True)
            records, import_metadata = build_skolenkaten_import(baseline, source_files, year=year)
            output_path = IMPORT_DIR / f"schools-{year}-skolenkaten-four-cities.json"
            write_import_json(records, output_path, import_metadata)
            applied = upsert_schools(records, output_path.name)
            matched = int(import_metadata.get("matchedSchools") or 0)
            per_year.append({
                "year": year,
                "status": "complete",
                "appliedRecords": applied,
                "matchedSchools": matched,
                "matchedById": int(import_metadata.get("matchedById") or 0),
                "matchedByNameAndMunicipality": int(import_metadata.get("matchedByNameAndMunicipality") or 0),
                "output": str(output_path.relative_to(ROOT)),
            })
            total_applied += applied
            successful_years += 1
        except Exception as exc:
            per_year.append({"year": year, "status": "failed", "error": str(exc)})

    if successful_years == len(selected_years):
        status = "complete"
    elif successful_years:
        status = "partial"
    else:
        status = "failed"
    result = {
        "status": status,
        "years": selected_years,
        "appliedRecords": total_applied,
        "results": per_year,
        "error": None if successful_years else "All configured Skolenkäten downloads/imports failed.",
        "finishedAt": now_iso(),
    }
    set_state("survey_sync", result)
    return result


def start_background_survey_if_needed(force: bool = False) -> bool:
    global _LAST_SURVEY_SYNC_START
    if not AUTO_IMPORT_SKOLENKATEN:
        return False
    with _SURVEY_SYNC_LOCK:
        state = get_state("survey_sync") or {}
        if state.get("status") == "running":
            return False
        now_mono = time.monotonic()
        if now_mono - _LAST_SURVEY_SYNC_START < SURVEY_SYNC_RETRY_SECONDS:
            return False
        if not force and state.get("status") in {"complete", "partial"}:
            return False
        _LAST_SURVEY_SYNC_START = now_mono
        threading.Thread(target=run_survey_sync, daemon=True).start()
        return True


def _run_startup_sync_pipeline() -> None:
    """Run registry first for IDs/coordinates, then survey even if registry failed."""
    run_registry_sync(import_surveys=False)
    if AUTO_IMPORT_SKOLENKATEN:
        start_background_survey_if_needed(force=True)


def start_background_sync_if_needed(force: bool = False) -> bool:
    """Start one registry refresh pipeline with a retry cooldown."""
    global _LAST_REGISTRY_SYNC_START
    if not AUTO_IMPORT_SCHOOL_REGISTRY:
        if AUTO_IMPORT_SKOLENKATEN:
            return start_background_survey_if_needed(force=force)
        return False
    with _REGISTRY_SYNC_LOCK:
        state = get_state("registry_sync") or {}
        if state.get("status") == "running":
            return False
        now_mono = time.monotonic()
        if now_mono - _LAST_REGISTRY_SYNC_START < REGISTRY_SYNC_RETRY_SECONDS:
            return False
        if not force:
            with db() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) AS n FROM schools WHERE registry_source='Skolverket school-unit register'"
                ).fetchone()["n"]
            if count > 0 and (get_state("survey_sync") or {}).get("status") in {"complete", "partial"}:
                return False
        _LAST_REGISTRY_SYNC_START = now_mono
        threading.Thread(target=_run_startup_sync_pipeline, daemon=True).start()
        return True

def normalize_location_text(value: str | None) -> str:
    clean = (value or "").strip().lower()
    clean = clean.replace("å", "a").replace("ä", "a").replace("ö", "o")
    clean = re.sub(r"[^a-z0-9]+", " ", clean)
    return re.sub(r"\s+", " ", clean).strip()


POSTCODE_RE = re.compile(r"(?<!\d)(\d{3})\s?(\d{2})(?!\d)")


def extract_postcode_digits(value: str | None) -> str | None:
    match = POSTCODE_RE.search(value or "")
    return "".join(match.groups()) if match else None


def result_postcode_digits(result: dict[str, Any]) -> str | None:
    address = result.get("address") or {}
    return extract_postcode_digits(str(address.get("postcode") or ""))


def geocode_cache_key(query: str, city_key: str) -> str:
    return f"{GEOCODER_CACHE_VERSION}:{city_key}:{normalize_location_text(query)}"


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
    aliases = {normalize_location_text(value) for value in config.get("municipality_aliases", set())}
    locality_values = {normalize_location_text(value) for value in result_locality_values(result)}
    for locality in locality_values:
        if locality in aliases:
            return True
        if any(alias and (alias in locality or locality in alias) for alias in aliases):
            return True
    return False


def city_key_for_result(result: dict[str, Any]) -> str | None:
    for city_key in CITY_CONFIG:
        if result_matches_city(result, city_key):
            return city_key
    return None


def detected_municipality(result: dict[str, Any]) -> str | None:
    address = result.get("address") or {}
    return (
        address.get("municipality")
        or address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("county")
    )


def _geocode_result_rank(result: dict[str, Any], selected_city: str, requested_postcode: str | None) -> tuple[int, int, float]:
    exact_postcode = int(bool(requested_postcode and result_postcode_digits(result) == requested_postcode))
    selected_city_match = int(result_matches_city(result, selected_city))
    try:
        importance = float(result.get("importance") or 0)
    except (TypeError, ValueError):
        importance = 0.0
    return exact_postcode, selected_city_match, importance


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

    requested_postcode = extract_postcode_digits(clean_query)
    normalized_query = normalize_location_text(clean_query)
    city_label = CITY_CONFIG[city_key]["label"]
    has_country = "sweden" in normalized_query or "sverige" in normalized_query
    has_selected_city = any(
        alias in normalized_query
        for alias in {normalize_location_text(v) for v in CITY_CONFIG[city_key].get("search_aliases", set())}
    )

    base_params: dict[str, Any] = {
        "format": "jsonv2",
        "addressdetails": 1,
        "limit": 10,
        "countrycodes": "se",
        "accept-language": "sv,en",
    }
    if GEOCODER_EMAIL:
        base_params["email"] = GEOCODER_EMAIL

    request_params: list[dict[str, Any]] = []
    if requested_postcode:
        # Nominatim structured searches cannot be combined with q. This query
        # makes a postcode-only lookup deterministic and avoids unrelated POIs.
        request_params.append({**base_params, "postalcode": requested_postcode, "country": "Sweden"})
    if not has_selected_city:
        request_params.append({**base_params, "q": f"{clean_query}, {city_label}, Sweden"})
    request_params.append({
        **base_params,
        "q": clean_query if has_country else f"{clean_query}, Sweden",
    })

    headers = {"User-Agent": GEOCODER_USER_AGENT, "Accept": "application/json"}
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    provider_status: int | None = None
    try:
        for params in request_params:
            response = requests.get(GEOCODER_URL, params=params, headers=headers, timeout=15)
            provider_status = response.status_code
            response.raise_for_status()
            payload_results = response.json()
            if not isinstance(payload_results, list):
                continue
            for result in payload_results:
                if not isinstance(result, dict):
                    continue
                identity = (
                    str(result.get("place_id") or ""),
                    str(result.get("lat") or ""),
                    str(result.get("lon") or ""),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                results.append(result)
    except requests.Timeout as exc:
        raise HTTPException(status_code=504, detail="The address provider timed out. Please try again.") from exc
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else provider_status
        raise HTTPException(
            status_code=502,
            detail=f"The address provider rejected the request{f' (HTTP {status})' if status else ''}. Try again later.",
        ) from exc
    except requests.ConnectionError as exc:
        raise HTTPException(status_code=503, detail="The server could not reach the address provider. Please try again shortly.") from exc
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Address lookup is temporarily unavailable. Please try again shortly.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="The address provider returned an invalid response.") from exc

    postcode_mismatch = False
    eligible = results
    if requested_postcode:
        eligible = [result for result in results if result_postcode_digits(result) == requested_postcode]
        postcode_mismatch = bool(results and not eligible)

    if not eligible:
        message = (
            f"No Swedish location with postal code {requested_postcode[:3]} {requested_postcode[3:]} matched the search."
            if requested_postcode
            else "No Swedish address or postal code matched the search."
        )
        if postcode_mismatch:
            message += " Unrelated results with different postal codes were rejected."
        payload = {
            "found": False,
            "insideSelectedCity": False,
            "selectedCity": CITY_CONFIG[city_key]["label"],
            "selectedCityKey": city_key,
            "matchedCityKey": None,
            "query": clean_query,
            "requestedPostalCode": requested_postcode,
            "message": message,
            "provider": "OpenStreetMap Nominatim",
        }
    else:
        eligible.sort(key=lambda result: _geocode_result_rank(result, city_key, requested_postcode), reverse=True)
        chosen = eligible[0]
        address = chosen.get("address") or {}
        try:
            lat = float(chosen["lat"])
            lng = float(chosen["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=502, detail="Address provider did not return coordinates") from exc
        inside_selected = result_matches_city(chosen, city_key)
        matched_city_key = city_key_for_result(chosen)
        municipality = detected_municipality(chosen)
        message = (
            f"Address matched within the {CITY_CONFIG[city_key]['label']} dataset."
            if inside_selected
            else (
                f"Address matched the {CITY_CONFIG[matched_city_key]['label']} dataset."
                if matched_city_key
                else f"This address appears to be in {municipality or 'another municipality'}, outside the four loaded city datasets."
            )
        )
        payload = {
            "found": True,
            "insideSelectedCity": inside_selected,
            "selectedCity": CITY_CONFIG[city_key]["label"],
            "selectedCityKey": city_key,
            "matchedCityKey": matched_city_key,
            "matchedCity": CITY_CONFIG.get(matched_city_key, {}).get("label") if matched_city_key else None,
            "query": clean_query,
            "requestedPostalCode": requested_postcode,
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
    rows = conn.execute(
        f"SELECT DISTINCT year FROM school_year_metrics WHERE ({SUBSTANTIVE_SQL}) ORDER BY year DESC"
    ).fetchall()
    return [int(row["year"]) for row in rows]


app = FastAPI(title="Sweden School Guide", version=APP_VERSION)
app.mount("/assets", StaticFiles(directory=STATIC_DIR), name="assets")


@app.middleware("http")
async def no_cache_dynamic_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/assets/") or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


@app.on_event("startup")
def _startup() -> None:
    bootstrap_database()
    start_background_sync_if_needed()


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


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    from math import asin, cos, radians, sin, sqrt
    radius = 6371.0088
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return 2 * radius * asin(sqrt(a))


@app.get("/api/nearby")
def nearby_api(
    q: str = Query(min_length=3, max_length=220),
    city: str = Query(default="goteborg"),
    year: str = Query(default=DEFAULT_YEAR_MODE),
    limit: int = Query(default=12, ge=1, le=50),
    radius_km: float = Query(default=25.0, ge=1, le=100),
) -> dict[str, Any]:
    selected_city = city.strip().lower()
    if selected_city not in CITY_CONFIG:
        raise HTTPException(status_code=400, detail="Unsupported city dataset")
    geocode = fetch_geocode(q, selected_city)
    if not geocode.get("found"):
        return {"geocode": geocode, "count": 0, "schools": [], "message": geocode.get("message")}
    matched_city = geocode.get("matchedCityKey")
    if not matched_city:
        return {
            "geocode": geocode,
            "count": 0,
            "schools": [],
            "message": "The address was found, but it is outside the four loaded city datasets.",
        }
    origin_lat = float(geocode["lat"])
    origin_lng = float(geocode["lng"])
    with db() as conn:
        tracked_count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM schools WHERE city_key=?", (matched_city,)
        ).fetchone()["n"])
        coordinate_count = int(conn.execute(
            "SELECT COUNT(*) AS n FROM schools WHERE city_key=? AND lat IS NOT NULL AND lng IS NOT NULL",
            (matched_city,),
        ).fetchone()["n"])
        rows = conn.execute(
            "SELECT * FROM schools WHERE city_key=? AND lat IS NOT NULL AND lng IS NOT NULL",
            (matched_city,),
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        for school in rows:
            distance = haversine_km(origin_lat, origin_lng, float(school["lat"]), float(school["lng"]))
            if distance > radius_km:
                continue
            metric, fallback, requested_year = metric_row_for(conn, school["slug"], year)
            item = (
                combine_school_and_metric(school, metric, fallback, requested_year)
                if metric
                else combine_school_without_metric(school, requested_year)
            )
            item["distanceKm"] = round(distance, 2)
            candidates.append(item)
    candidates.sort(key=lambda item: (item["distanceKm"], -(item.get("qualityScore") or 0)))
    selected = candidates[:limit]
    registry_sync = get_state("registry_sync")
    if selected:
        message = f"Showing tracked schools in the {CITY_CONFIG[matched_city]['label']} dataset."
    elif coordinate_count == 0:
        message = (
            f"{tracked_count} tracked schools are loaded for {CITY_CONFIG[matched_city]['label']}, "
            "but none currently have coordinates. The official registry coordinate refresh has not succeeded yet."
        )
    else:
        message = (
            f"No tracked school with coordinates was found within {radius_km:g} km. "
            f"Coordinate coverage: {coordinate_count} of {tracked_count} loaded schools."
        )
    return {
        "geocode": geocode,
        "selectedCityKey": selected_city,
        "matchedCityKey": matched_city,
        "matchedCityLabel": CITY_CONFIG[matched_city]["label"],
        "autoSwitched": matched_city != selected_city,
        "radiusKm": radius_km,
        "trackedSchoolCount": tracked_count,
        "coordinateSchoolCount": coordinate_count,
        "registrySync": registry_sync,
        "count": len(selected),
        "schools": selected,
        "message": message,
    }


@app.get("/api/methodology")
def methodology() -> dict[str, Any]:
    return {
        "version": APP_VERSION,
        "qualityMethodVersion": QUALITY_METHOD_VERSION,
        "qualityFormula": calculate_quality({})["qualityFormula"],
        "admissionNote": "Admission realism is separate from quality. Municipal admission values require municipality-specific placement data; private-school values should be based on each school’s published admission rules.",
        "yearFallback": "Default year mode is current: the API uses the newest imported official data year and falls back per school only when that current-year record is missing.",
        "recommendedSources": [
            "Skolinspektionen Skolenkäten Excel files for survey ratings: F0 guardians, grundskola guardians, pupils grade 5/8 and teachers.",
            "Skolverket/Utbildningsguiden and national statistics for school-unit facts, teacher ratios and academic results.",
            "Municipality-specific placement statistics for municipal admission realism where published.",
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
        "geocoding": {"provider": "OpenStreetMap Nominatim", "supportedCities": list(CITY_CONFIG.keys()), "cache": True},
        "cities": [
            {"key": key, "label": config["label"], "municipalityCodes": sorted(config.get("municipality_codes", set()))}
            for key, config in CITY_CONFIG.items()
        ],
        "registrySync": get_state("registry_sync"),
        "surveySync": get_state("survey_sync"),
        "baselineMode": "Bundled baseline records make all four city directories available immediately. Registry sync adds official IDs and coordinates; the separate 2026+2025 survey sync adds published Skolenkäten values.",
    }


@app.get("/api/schools")
def schools_api(
    year: str = Query(default=DEFAULT_YEAR_MODE),
    city: str = Query(default="goteborg"),
) -> dict[str, Any]:
    city_key = city.strip().lower()
    if city_key not in CITY_CONFIG and city_key != "all":
        raise HTTPException(status_code=400, detail="Unsupported city dataset")

    registry_triggered = False
    with db() as conn:
        if city_key == "all":
            school_rows = conn.execute("SELECT * FROM schools ORDER BY name COLLATE NOCASE").fetchall()
        else:
            school_rows = conn.execute(
                "SELECT * FROM schools WHERE city_key=? ORDER BY name COLLATE NOCASE", (city_key,)
            ).fetchall()

    if city_key != "all" and not school_rows:
        registry_triggered = start_background_sync_if_needed(force=True)

    with db() as conn:
        if city_key == "all":
            school_rows = conn.execute("SELECT * FROM schools ORDER BY name COLLATE NOCASE").fetchall()
        else:
            school_rows = conn.execute(
                "SELECT * FROM schools WHERE city_key=? ORDER BY name COLLATE NOCASE", (city_key,)
            ).fetchall()
        items: list[dict[str, Any]] = []
        fallback_count = 0
        rated_count = 0
        quality_metric_count = 0
        registry_only_count = 0
        requested_year, _year_mode = resolve_year_param(conn, year)
        if city_key != "all":
            wrong_city_rows = [row for row in school_rows if row["city_key"] != city_key]
            if wrong_city_rows:
                raise HTTPException(status_code=500, detail="City isolation check failed for school directory")
        for school in school_rows:
            metric, fallback, requested_metric_year = metric_row_for(conn, school["slug"], year)
            if metric:
                if fallback:
                    fallback_count += 1
                rated_count += 1
                item = combine_school_and_metric(school, metric, fallback, requested_metric_year)
                if int(item.get("dataCompletenessPct") or 0) > 0:
                    quality_metric_count += 1
                items.append(item)
            else:
                registry_only_count += 1
                items.append(combine_school_without_metric(school, requested_year))
        available_years = get_available_years(conn)
        current_year = max(available_years) if available_years else None
        resolved_year, year_mode = resolve_year_param(conn, year)
        city_counts_rows = conn.execute(
            "SELECT city_key, COUNT(*) AS n FROM schools GROUP BY city_key"
        ).fetchall()
    city_counts = {row["city_key"]: int(row["n"]) for row in city_counts_rows if row["city_key"]}

    survey_triggered = False
    if items and quality_metric_count == 0:
        survey_triggered = start_background_survey_if_needed(force=True)

    return {
        "requestedYear": year,
        "city": city_key,
        "cityLabel": CITY_CONFIG.get(city_key, {}).get("label", "All cities"),
        "resolvedYear": resolved_year,
        "yearMode": year_mode,
        "currentDataYear": current_year,
        "availableYears": available_years,
        "fallbackCount": fallback_count,
        "ratedCount": rated_count,
        "qualityMetricCount": quality_metric_count,
        "registryOnlyCount": registry_only_count,
        "cityCounts": city_counts,
        "syncTriggered": registry_triggered,
        "surveySyncTriggered": survey_triggered,
        "registrySync": get_state("registry_sync"),
        "surveySync": get_state("survey_sync"),
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


@app.post("/api/admin/import/school-registry")
def import_school_registry_api(
    request: Request,
    surveys: bool = Query(default=True),
) -> JSONResponse:
    admin_token = os.getenv("ADMIN_TOKEN")
    if admin_token and request.headers.get("x-admin-token") != admin_token:
        raise HTTPException(status_code=401, detail="Invalid admin token")
    result = run_registry_sync(import_surveys=surveys)
    status = 200 if result.get("status") == "complete" else 502
    return JSONResponse(result, status_code=status)


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
        baseline = database_baseline(year)
        source_files = ensure_source_files(year, cache_dir, use_network=True)
        records, import_metadata = build_skolenkaten_import(baseline, source_files, year=year)
        output_path = DATA_DIR / "imports" / f"schools-{year}-skolenkaten-four-cities.json"
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
