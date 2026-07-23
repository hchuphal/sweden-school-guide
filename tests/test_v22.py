from __future__ import annotations

from app import main


def test_exact_name_merges_despite_coordinate_drift() -> None:
    tracked = {
        "name": "Jättestensskolan",
        "address": "Norrviksgatan 1, Göteborg",
        "lat": 57.7180,
        "lng": 11.8940,
        "distanceKm": 0.60,
        "mapDiscovered": False,
        "sources": [{"label": "Skolverket", "url": "https://example.test/official"}],
        "qualityScore": 73,
    }
    mapped = {
        "name": "Jättestensskolan",
        "address": "Norrviksgatan 1, 418 72 Göteborg, Sverige",
        "lat": 57.7230,
        "lng": 11.9010,
        "distanceKm": 0.30,
        "mapDiscovered": True,
        "sources": [{"label": "OpenStreetMap", "url": "https://example.test/osm"}],
        "qualityScore": None,
    }
    unique = main.deduplicate_nearby_candidates([mapped, tracked])
    assert len(unique) == 1
    assert unique[0]["mapDiscovered"] is False
    assert unique[0]["qualityScore"] == 73
    assert unique[0]["distanceKm"] == 0.30
    assert unique[0]["mapConfirmed"] is True


def test_same_street_identity_merges_similar_school_names() -> None:
    tracked = {
        "name": "Göteborgs Högre Samskola",
        "address": "Stampgatan 13, Göteborg",
        "lat": 57.70, "lng": 11.98, "mapDiscovered": False, "sources": [],
    }
    mapped = {
        "name": "Göteborgs Högre Samskola / Lilla Samskolan",
        "address": "Stampgatan 13, 411 01 Göteborg, Sverige",
        "lat": 57.71, "lng": 11.99, "mapDiscovered": True, "sources": [],
    }
    assert main._nearby_candidates_duplicate(tracked, mapped) is True


def test_different_branches_are_not_merged_by_fuzzy_name_alone() -> None:
    first = {
        "name": "Noblaskolan Lindholmen", "address": "Verkmästaregatan 7, Göteborg",
        "lat": 57.708, "lng": 11.938, "mapDiscovered": False,
    }
    second = {
        "name": "Noblaskolan Kviberg", "address": "Luftvärnsvägen 4, Göteborg",
        "lat": 57.733, "lng": 12.039, "mapDiscovered": True,
    }
    assert main._nearby_candidates_duplicate(first, second) is False


if __name__ == "__main__":
    test_exact_name_merges_despite_coordinate_drift()
    test_same_street_identity_merges_similar_school_names()
    test_different_branches_are_not_merged_by_fuzzy_name_alone()
    print("v0.22 tests passed")
