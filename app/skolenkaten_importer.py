from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import requests
from openpyxl import load_workbook

SKOLENKATEN_SOURCE_PAGE = "https://www.skolinspektionen.se/skolenkaten/resultat-fran-skolenkaten/resultat-skolenkaten-2026/"

SKOLENKATEN_URLS: dict[int, dict[str, str]] = {
    2026: {
        "f0_guardians": "https://www.skolinspektionen.se/globalassets/02-beslut-rapporter-stat/statistik/statistik-skolenkaten/2026/skolenkaten-vardnadshavare-forskoleklass-2026.xlsx",
        "grade5_pupils": "https://www.skolinspektionen.se/globalassets/02-beslut-rapporter-stat/statistik/statistik-skolenkaten/2026/skolenkaten-elever-grundskola-ak-5-2026.xlsx",
        "grade8_pupils": "https://www.skolinspektionen.se/globalassets/02-beslut-rapporter-stat/statistik/statistik-skolenkaten/2026/skolenkaten-elever-grundskola-ak-8-2026.xlsx",
        "school_guardians": "https://www.skolinspektionen.se/globalassets/02-beslut-rapporter-stat/statistik/statistik-skolenkaten/2026/skolenkaten-vardnadshavare-grundskola-ak-1-9-2026.xlsx",
    }
}

# Known Skolverket skolenhetskod values for schools that did not yet have a
# schoolUnitID URL in the MVP seed data.
KNOWN_SCHOOL_UNIT_IDS: dict[str, int] = {
    "jattestensskolan": 55124975,
    "herrgardsskolan": 62479729,
    "taubeskolan": 11099088,
    "lerlyckeskolan": 48653651,
    "bjurslattsskolan": 37407044,
    "brackeskolan": 29466561,
    "innovitaskolan-st-jorgen": 27265374,
    "fridaskolan-kvillebacken": 56328727,
    "ebba-petterssons-privatskola": 88247874,
    "goteborgs-hogre-samskola-lilla-samskolan": 67568030,
    # ISGR has separate F-6 and 7-9 units. For F0/F-6 survey import, use the F-6 unit.
    "isgr": 93758282,
    "montessoriskolan-casa": 76938537,
    "montessoriskolan-centrum": 73995321,
}

SCHOOL_UNIT_RE = re.compile(r"schoolUnitID=(\d+)", re.IGNORECASE)

# Excel columns are 1-indexed, matching the published Skolenkäten files.
# The sheets use rows 1-3 as headers and data starts below.
F0_COLUMNS = {
    "f0Satisfaction": 327,  # Hur nöjd är du med ditt barns skola? (Medelvärde)
    "safety": 168,          # Trygghet index
    "studyPeace": 141,      # Studiero index
    "support": 68,          # Stöd index
    "stimulation": 32,      # Stimulans index, kept in source details only for now
}
PUPIL_COLUMNS = {
    "studentSatisfaction": 362,  # Hur nöjd är du med din skola? (Medelvärde)
    "pupilSafety": 264,          # Trygghet index
    "pupilStudyPeace": 237,      # Studiero index
    "pupilSupport": 92,          # Stöd index
    "pupilStimulation": 43,      # Stimulans index
}
GUARDIAN_COLUMNS = {
    "parentSatisfaction": 327,   # Hur nöjd är du med ditt barns skola? (Medelvärde)
    "guardianSafety": 168,
    "guardianStudyPeace": 141,
    "guardianSupport": 68,
    "guardianStimulation": 32,
}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        clean = value.strip().replace(",", ".")
        if clean in {"", "-", "*", "**", "***"}:
            return None
        value = clean
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1)


def _to_int(value: Any) -> int | None:
    if value is None or value == "-":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _read_metric_file(path: Path, metric_columns: dict[str, int]) -> dict[int, dict[str, Any]]:
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    records: dict[int, dict[str, Any]] = {}
    for row in ws.iter_rows(min_row=4, values_only=True):
        school_unit_id = _to_int(row[3] if len(row) > 3 else None)
        school_name = row[4] if len(row) > 4 else None
        if not school_unit_id or not school_name:
            continue
        item: dict[str, Any] = {
            "schoolUnitId": school_unit_id,
            "sourceSchoolName": school_name,
            "sourceMunicipality": row[2] if len(row) > 2 else None,
            "groupSize": _to_int(row[5] if len(row) > 5 else None),
            "responseCount": _to_int(row[6] if len(row) > 6 else None),
            "responseRate": _to_float(row[7] if len(row) > 7 else None),
        }
        for key, col in metric_columns.items():
            item[key] = _to_float(row[col - 1] if len(row) >= col else None)
        records[school_unit_id] = item
    return records


def _download(url: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 0:
        return path
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


def ensure_source_files(year: int, cache_dir: Path, *, use_network: bool = True) -> dict[str, Path]:
    urls = SKOLENKATEN_URLS.get(year)
    if not urls:
        raise ValueError(f"No Skolenkäten source URLs configured for {year}")
    files: dict[str, Path] = {}
    for key, url in urls.items():
        filename = url.rsplit("/", 1)[-1]
        local_path = cache_dir / str(year) / filename
        if use_network:
            _download(url, local_path)
        elif not local_path.exists():
            raise FileNotFoundError(f"Missing cached source file: {local_path}")
        files[key] = local_path
    return files


def extract_school_unit_id(record: dict[str, Any]) -> int | None:
    if record.get("schoolUnitId"):
        return _to_int(record.get("schoolUnitId"))
    for source in record.get("sources", []) or []:
        match = SCHOOL_UNIT_RE.search(source.get("url", ""))
        if match:
            return int(match.group(1))
    return KNOWN_SCHOOL_UNIT_IDS.get(record.get("slug", ""))


def build_skolenkaten_import(
    baseline_records: list[dict[str, Any]],
    source_files: dict[str, Path],
    *,
    year: int,
    verified_label: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    f0 = _read_metric_file(source_files["f0_guardians"], F0_COLUMNS)
    grade5 = _read_metric_file(source_files["grade5_pupils"], PUPIL_COLUMNS)
    grade8 = _read_metric_file(source_files["grade8_pupils"], PUPIL_COLUMNS)
    guardians = _read_metric_file(source_files["school_guardians"], GUARDIAN_COLUMNS)

    updated: list[dict[str, Any]] = []
    matched_count = 0
    unmatched: list[str] = []
    verified = verified_label or f"Skolenkäten {year}, imported from official Excel files"

    for source_record in baseline_records:
        record = deepcopy(source_record)
        record["dataYear"] = int(year)
        school_unit_id = extract_school_unit_id(record)
        record["schoolUnitId"] = school_unit_id
        if not school_unit_id:
            unmatched.append(record.get("name") or record.get("slug") or "Unknown school")
            updated.append(record)
            continue

        f0_row = f0.get(school_unit_id)
        g5_row = grade5.get(school_unit_id)
        g8_row = grade8.get(school_unit_id)
        guardian_row = guardians.get(school_unit_id)
        has_match = any([f0_row, g5_row, g8_row, guardian_row])

        # F0 guardian data is most relevant for förskoleklass. If a specific cell is
        # suppressed by Skolenkäten, keep it as None rather than guessing.
        if f0_row:
            for field in ["f0Satisfaction", "safety", "studyPeace", "support"]:
                record[field] = f0_row.get(field)

        # Use pupil grade 5 as the default student view for F0/F-6 decisions;
        # fall back to grade 8 only where grade 5 is not available.
        pupil_row = g5_row or g8_row
        if pupil_row:
            record["studentSatisfaction"] = pupil_row.get("studentSatisfaction")
            # Fill missing core fields from pupil data if F0 guardian data is missing.
            if record.get("safety") is None:
                record["safety"] = pupil_row.get("pupilSafety")
            if record.get("studyPeace") is None:
                record["studyPeace"] = pupil_row.get("pupilStudyPeace")
            if record.get("support") is None:
                record["support"] = pupil_row.get("pupilSupport")

        if guardian_row:
            record["parentSatisfaction"] = guardian_row.get("parentSatisfaction")
            # Fill missing core fields from general guardian data if needed.
            if record.get("safety") is None:
                record["safety"] = guardian_row.get("guardianSafety")
            if record.get("studyPeace") is None:
                record["studyPeace"] = guardian_row.get("guardianStudyPeace")
            if record.get("support") is None:
                record["support"] = guardian_row.get("guardianSupport")

        if has_match:
            matched_count += 1
            record["lastVerified"] = f"{year}-05-28"
            existing_note = record.get("verificationNote") or ""
            record["verificationNote"] = (
                f"{verified}. Survey fields imported by skolenhetskod {school_unit_id}. "
                f"Academic and admission fields remain from their existing sources. "
                f"{existing_note}"
            ).strip()
            sources = record.setdefault("sources", [])
            if not any("skolinspektionen.se/skolenkaten" in (src.get("url", "")) for src in sources):
                sources.append({"label": f"Skolinspektionen Skolenkäten {year}", "url": SKOLENKATEN_SOURCE_PAGE})
            record["surveyImportStatus"] = "matched"
            record["surveySourceDetails"] = {
                "schoolUnitId": school_unit_id,
                "f0Guardians": _source_summary(f0_row),
                "grade5Pupils": _source_summary(g5_row),
                "grade8Pupils": _source_summary(g8_row),
                "schoolGuardians": _source_summary(guardian_row),
            }
        else:
            record["surveyImportStatus"] = "not-found-in-skolenkaten-files"
            unmatched.append(record.get("name") or record.get("slug") or str(school_unit_id))

        updated.append(record)

    metadata = {
        "year": year,
        "sourcePage": SKOLENKATEN_SOURCE_PAGE,
        "matchedSchools": matched_count,
        "totalSchools": len(baseline_records),
        "unmatchedSchools": unmatched,
        "sourceFiles": {key: str(path) for key, path in source_files.items()},
        "note": "Skolenkäten survey fields were imported where skolenhetskod matched. Missing/suppressed cells are kept as null. Academic scores and admission realism are not changed by this importer.",
    }
    return updated, metadata


def _source_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "sourceSchoolName": row.get("sourceSchoolName"),
        "groupSize": row.get("groupSize"),
        "responseCount": row.get("responseCount"),
        "responseRate": row.get("responseRate"),
    }


def load_baseline(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict) and "schools" in payload:
        return payload["schools"]
    if isinstance(payload, list):
        return payload
    raise ValueError("Baseline must be a list or object with a schools array")


def write_import_json(records: list[dict[str, Any]], output_path: Path, metadata: dict[str, Any]) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"metadata": metadata, "schools": records}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path
