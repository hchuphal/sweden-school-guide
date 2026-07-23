from __future__ import annotations

import json
from pathlib import Path

from app import main


def test_swedish_linking_s_alias_merges() -> None:
    tracked = {
        "name": "Jättestensskolan",
        "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.71997, "lng": 11.90194,
        "distanceKm": 0.60, "mapDiscovered": False,
        "qualityScore": 73, "sources": [],
    }
    mapped = {
        "name": "Jättestenskolan",
        "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.71995, "lng": 11.90190,
        "distanceKm": 0.30, "mapDiscovered": True,
        "qualityScore": None, "sources": [],
    }
    assert main.school_names_similar(tracked["name"], mapped["name"]) is True
    unique = main.deduplicate_nearby_candidates([mapped, tracked])
    assert len(unique) == 1
    assert unique[0]["name"] == "Jättestensskolan"
    assert unique[0]["mapDiscovered"] is False
    assert unique[0]["qualityScore"] == 73
    assert unique[0]["distanceKm"] == 0.30


def test_merge_prefers_tracked_record_and_closer_map_distance() -> None:
    tracked = [{
        "name": "Jättestensskolan", "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.71997, "lng": 11.90194, "distanceKm": 0.60,
        "mapDiscovered": False, "qualityScore": 73, "sources": [],
    }]
    mapped = [{
        "name": "Jättestenskolan", "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.71995, "lng": 11.90190, "mapDiscovered": True, "sources": [],
    }]
    merged, added = main.merge_nearby_school_candidates(
        tracked, mapped, origin_lat=57.7220, origin_lng=11.9000, radius_km=10, requested_year=2026
    )
    assert added == 0
    assert len(merged) == 1
    assert merged[0]["name"] == "Jättestensskolan"
    assert merged[0]["qualityScore"] == 73
    assert merged[0]["mapConfirmed"] is True
    assert merged[0]["distanceKm"] < 0.60


def test_different_nobla_branches_remain_separate() -> None:
    first = {
        "name": "Noblaskolan Lindholmen", "address": "Verkmästaregatan 7, Göteborg",
        "lat": 57.708, "lng": 11.938, "mapDiscovered": False,
    }
    second = {
        "name": "Noblaskolan Kviberg", "address": "Luftvärnsvägen 4, Göteborg",
        "lat": 57.733, "lng": 12.039, "mapDiscovered": True,
    }
    assert main._nearby_candidates_duplicate(first, second) is False


def test_bundled_jattesten_coordinate_matches_osm_feature() -> None:
    data_path = Path(__file__).parents[1] / "data" / "schools-2026.json"
    schools = json.loads(data_path.read_text(encoding="utf-8"))
    school = next(item for item in schools if item.get("slug") == "jattestensskolan")
    assert school["lat"] == 57.71997
    assert school["lng"] == 11.90194


if __name__ == "__main__":
    test_swedish_linking_s_alias_merges()
    test_merge_prefers_tracked_record_and_closer_map_distance()
    test_different_nobla_branches_remain_separate()
    test_bundled_jattesten_coordinate_matches_osm_feature()
    print("v0.23 tests passed")
