from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="schoolguide-v18-test-"))
os.environ["SCHOOLGUIDE_DB_PATH"] = str(TMP / "test.sqlite")
os.environ["AUTO_IMPORT_SCHOOL_REGISTRY"] = "false"
os.environ["AUTO_IMPORT_SKOLENKATEN"] = "false"
sys.path.insert(0, str(ROOT))

from app import main  # noqa: E402
from app.school_registry_importer import next_link  # noqa: E402
from app.skolenkaten_importer import build_skolenkaten_import  # noqa: E402


def make_xlsx(path: Path, columns: dict[int, object]) -> None:
    wb = Workbook()
    ws = wb.active
    for col, value in columns.items():
        ws.cell(row=4, column=col, value=value)
    wb.save(path)


def test_survey_name_matching() -> None:
    files = {}
    common = {3: "Stockholm", 4: 12345678, 5: "Gamla Enskede skola", 6: 40, 7: 25, 8: 62.5}
    specs = {
        "f0_guardians": {**common, 327: 7.8, 168: 8.1, 141: 6.9, 68: 7.3},
        "grade5_pupils": {**common, 362: 7.1, 264: 8.0, 237: 6.5, 92: 7.4},
        "grade8_pupils": common,
        "school_guardians": {**common, 327: 7.6, 168: 7.9, 141: 6.8, 68: 7.2},
    }
    for key, values in specs.items():
        path = TMP / f"{key}.xlsx"
        make_xlsx(path, values)
        files[key] = path
    baseline = [{
        "slug": "stockholm-gamla-enskede-skola",
        "name": "Gamla Enskede skola",
        "type": "Municipal",
        "grades": "F–6",
        "area": "Enskede-Årsta-Vantör",
        "address": "Stockholmsvägen 30, Stockholm",
        "cityKey": "stockholm",
        "municipality": "Stockholms stad",
        "registrySource": "Official city school directory fallback",
        "sources": [],
        "dataYear": 2026,
    }]
    records, meta = build_skolenkaten_import(baseline, files, year=2026)
    record = records[0]
    assert record["schoolUnitId"] == 12345678
    assert record["f0Satisfaction"] == 7.8
    assert record["studentSatisfaction"] == 7.1
    assert record["parentSatisfaction"] == 7.6
    assert meta["matchedByNameAndMunicipality"] == 1


def test_substantive_year_fallback() -> None:
    main.init_db()
    main.upsert_schools([{
        "slug": "test-school", "name": "Test School", "type": "Municipal", "grades": "F–6",
        "area": "Stockholm", "address": "Test 1", "cityKey": "stockholm", "municipality": "Stockholm",
        "registrySource": "test", "sources": [], "dataYear": 2026, "lastVerified": "2026-01-01",
    }], "empty-2026")
    main.upsert_schools([{
        "slug": "test-school", "name": "Test School", "type": "Municipal", "grades": "F–6",
        "area": "Stockholm", "address": "Test 1", "cityKey": "stockholm", "municipality": "Stockholm",
        "registrySource": "test", "sources": [], "dataYear": 2025, "safety": 7.5,
    }], "rated-2025")
    with main.db() as conn:
        metric, fallback, requested = main.metric_row_for(conn, "test-school", "current")
    assert metric is not None and metric["year"] == 2025
    assert fallback is True and requested == 2026


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)
    def json(self):
        return self._payload


def test_strict_postcode_and_city_switch() -> None:
    stockholm_wrong = {
        "place_id": 1, "lat": "59.338", "lon": "18.073", "importance": 0.9,
        "display_name": "Kungliga biblioteket, Stockholm, 111 42, Sverige", "type": "library",
        "address": {"postcode": "111 42", "municipality": "Stockholms kommun", "city": "Stockholm"},
    }
    goteborg_correct = {
        "place_id": 2, "lat": "57.690", "lon": "11.990", "importance": 0.3,
        "display_name": "412 48 Göteborg, Sverige", "type": "postcode",
        "address": {"postcode": "412 48", "municipality": "Göteborgs Stad", "city": "Göteborg"},
    }
    def fake_get(url, params=None, headers=None, timeout=None):
        if params and params.get("postalcode") == "41248":
            return FakeResponse([goteborg_correct])
        return FakeResponse([stockholm_wrong, goteborg_correct])
    with patch.object(main.requests, "get", side_effect=fake_get):
        result = main.fetch_geocode("41248", "stockholm")
    assert result["found"] is True
    assert result["postalCode"].replace(" ", "") == "41248"
    assert result["matchedCityKey"] == "goteborg"
    assert result["insideSelectedCity"] is False


def test_pagination_metadata() -> None:
    payload = {"content": [{"id": 1}], "page": {"number": 0, "totalPages": 3}}
    assert next_link(payload, "https://example.test/items?size=100") == "https://example.test/items?size=100&page=1"
    final = {"content": [], "page": {"number": 2, "totalPages": 3}}
    assert next_link(final, "https://example.test/items?page=2&size=100") is None


if __name__ == "__main__":
    test_survey_name_matching()
    test_substantive_year_fallback()
    test_strict_postcode_and_city_switch()
    test_pagination_metadata()
    print("v0.18 tests passed")
