from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from app import main

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.response = self

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(response=self)

    def json(self):
        return self._payload


def reset_database(tmp: str) -> None:
    main.DB_PATH = Path(tmp) / "test.sqlite"
    main.init_db()


def test_postcode_only_uses_safe_centroid_and_switches_city() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reset_database(tmp)

        def fake_get(url, **kwargs):
            if "photon" in url:
                return FakeResponse({"features": []})
            return FakeResponse([])

        with patch.object(main.requests, "get", side_effect=fake_get):
            result = main.fetch_geocode("412 48", "stockholm")
        assert result["found"] is True
        assert result["matchedCityKey"] == "goteborg"
        assert result["approximate"] is True
        assert result["postalCode"] == "412 48"
        assert abs(result["lat"] - 57.682976) < 0.00001


def test_full_address_accepts_street_match_with_postcode_warning() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reset_database(tmp)
        candidate = {
            "place_id": 1,
            "lat": "57.682938",
            "lon": "12.010335",
            "display_name": "Gräddgatan 5, Kallebäck, Göteborg, 412 75, Sverige",
            "importance": 0.7,
            "type": "house",
            "address": {
                "road": "Gräddgatan",
                "house_number": "5",
                "postcode": "412 75",
                "city": "Göteborg",
                "municipality": "Göteborgs Stad",
            },
        }
        with patch.object(main.requests, "get", return_value=FakeResponse([candidate])):
            result = main.fetch_geocode("Gräddgatan 5, 412 48 Göteborg", "goteborg")
        assert result["found"] is True
        assert result["matchedCityKey"] == "goteborg"
        assert "412 48" in result["postcodeWarning"]
        assert "412 75" in result["postcodeWarning"]
        assert result["approximate"] is False



def test_full_address_postcode_overrides_wrong_selected_city() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reset_database(tmp)
        stockholm_decoy = {
            "place_id": 10, "lat": "59.33", "lon": "18.06",
            "display_name": "Gräddgatan 5, Stockholm, 111 42, Sverige",
            "importance": 0.9, "type": "house",
            "address": {"road": "Gräddgatan", "house_number": "5", "postcode": "111 42", "city": "Stockholm", "municipality": "Stockholms stad"},
        }
        goteborg_match = {
            "place_id": 11, "lat": "57.682938", "lon": "12.010335",
            "display_name": "Gräddgatan 5, Göteborg, 412 75, Sverige",
            "importance": 0.5, "type": "house",
            "address": {"road": "Gräddgatan", "house_number": "5", "postcode": "412 75", "city": "Göteborg", "municipality": "Göteborgs Stad"},
        }
        with patch.object(main.requests, "get", return_value=FakeResponse([stockholm_decoy, goteborg_match])):
            result = main.fetch_geocode("Gräddgatan 5, 412 48", "stockholm")
        assert result["found"] is True
        assert result["matchedCityKey"] == "goteborg"
        assert abs(result["lat"] - 57.682938) < 0.00001

def test_nearby_merges_tracked_and_map_discovered_schools() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reset_database(tmp)
        baseline_path = ROOT / "data/imports/schools-2026-goteborg-independent-baseline.json"
        records = json.loads(baseline_path.read_text(encoding="utf-8"))
        main.upsert_schools(records, baseline_path.name)

        overpass = {
            "elements": [
                {
                    "type": "way",
                    "id": 100,
                    "center": {"lat": 57.679672, "lon": 12.011733},
                    "tags": {
                        "amenity": "school",
                        "name": "Fridaskolan i Kallebäck",
                        "addr:street": "Kallebäcks Torggata",
                        "addr:housenumber": "32",
                        "addr:postcode": "412 77",
                        "addr:city": "Göteborg",
                        "school:grades": "F-9",
                        "operator:type": "private",
                    },
                },
                {
                    "type": "node",
                    "id": 200,
                    "lat": 57.6840,
                    "lon": 12.0100,
                    "tags": {
                        "amenity": "school",
                        "name": "Example municipal school",
                        "addr:city": "Göteborg",
                        "school:grades": "F-6",
                        "operator:type": "public",
                    },
                },
            ]
        }
        geocode = {
            "found": True,
            "lat": 57.682938,
            "lng": 12.010335,
            "displayName": "Gräddgatan 5, Göteborg",
            "matchedCityKey": "goteborg",
            "postalCode": "412 48",
        }
        with patch.object(main, "fetch_geocode", return_value=geocode), patch.object(
            main.requests, "post", return_value=FakeResponse(overpass)
        ):
            result = main.nearby_api(
                q="Gräddgatan 5", city="goteborg", year="current", limit=12, radius_km=30
            )
        names = [school["name"] for school in result["schools"]]
        assert "Fridaskolan Kallebäck" in names
        assert "Example municipal school" in names
        assert names.count("Fridaskolan Kallebäck") == 1
        frida = next(school for school in result["schools"] if school["name"] == "Fridaskolan Kallebäck")
        assert frida["distanceKm"] < 0.5
        assert frida.get("mapConfirmed") is True
        municipal = next(school for school in result["schools"] if school["name"] == "Example municipal school")
        assert municipal["mapDiscovered"] is True
        assert municipal["type"] == "Municipal"


def test_fridaskolan_official_fallback_record() -> None:
    records = json.loads(
        (ROOT / "data/imports/schools-2026-goteborg-independent-baseline.json").read_text(encoding="utf-8")
    )
    record = next(item for item in records if item["slug"] == "fridaskolan-kalleback")
    assert record["schoolUnitId"] == 71240644
    assert record["grades"] == "F–9"
    assert record["postalCode"] == "412 77"
    assert record["f0Satisfaction"] == 6.8
    assert record["lat"] and record["lng"]


if __name__ == "__main__":
    test_postcode_only_uses_safe_centroid_and_switches_city()
    test_full_address_accepts_street_match_with_postcode_warning()
    test_full_address_postcode_overrides_wrong_selected_city()
    test_nearby_merges_tracked_and_map_discovered_schools()
    test_fridaskolan_official_fallback_record()
    print("v0.20 tests passed")
