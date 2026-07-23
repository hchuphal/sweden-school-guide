from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from app import main


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


def test_apartment_suffix_is_removed_before_geocoding() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        reset_database(tmp)
        candidate = {
            "place_id": 1,
            "lat": "57.7217",
            "lon": "11.8991",
            "display_name": "6A, Långströmsgatan, Jättesten, Göteborg, 418 70, Sverige",
            "importance": 0.8,
            "type": "house",
            "address": {
                "road": "Långströmsgatan",
                "house_number": "6A",
                "postcode": "418 70",
                "city": "Göteborg",
                "municipality": "Göteborgs Stad",
            },
        }
        requested_queries: list[str] = []

        def fake_get(url, **kwargs):
            params = kwargs.get("params") or {}
            if params.get("q"):
                requested_queries.append(str(params["q"]))
            return FakeResponse([candidate])

        with patch.object(main.requests, "get", side_effect=fake_get):
            result = main.fetch_geocode("Långströmsgatan 6A lgh1201", "goteborg")

        assert result["found"] is True
        assert result["query"] == "Långströmsgatan 6A lgh1201"
        assert result["geocodedQuery"] == "Långströmsgatan 6A"
        assert "Apartment or unit details were ignored" in result["queryWarning"]
        assert requested_queries
        assert all("lgh1201" not in query.lower() for query in requested_queries)


def test_final_dedupe_keeps_tracked_school_and_shortest_distance() -> None:
    tracked = {
        "name": "Jättestensskolan",
        "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.7219,
        "lng": 11.8988,
        "distanceKm": 0.12,
        "mapDiscovered": False,
        "sources": [{"label": "Skolverket", "url": "https://example.test/official"}],
        "qualityScore": 73,
    }
    mapped = {
        "name": "Jättestensskolan",
        "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.7222,
        "lng": 11.8992,
        "distanceKm": 0.08,
        "mapDiscovered": True,
        "sources": [{"label": "OpenStreetMap", "url": "https://example.test/osm"}],
        "qualityScore": None,
    }

    unique = main.deduplicate_nearby_candidates([mapped, tracked])
    assert len(unique) == 1
    assert unique[0]["mapDiscovered"] is False
    assert unique[0]["mapConfirmed"] is True
    assert unique[0]["distanceKm"] == 0.08
    assert {source["url"] for source in unique[0]["sources"]} == {
        "https://example.test/official",
        "https://example.test/osm",
    }


def test_unit_suffix_variants() -> None:
    cases = {
        "Långströmsgatan 6A lgh1201": "Långströmsgatan 6A",
        "Långströmsgatan 6A, lägenhet 1201": "Långströmsgatan 6A",
        "Långströmsgatan 6A apt. 1201": "Långströmsgatan 6A",
        "Långströmsgatan 6A vån 3": "Långströmsgatan 6A",
    }
    for query, expected in cases.items():
        simplified, warning = main.strip_non_geocodable_unit_suffix(query)
        assert simplified == expected
        assert warning


if __name__ == "__main__":
    test_apartment_suffix_is_removed_before_geocoding()
    test_final_dedupe_keeps_tracked_school_and_shortest_distance()
    test_unit_suffix_variants()
    print("v0.21 tests passed")
