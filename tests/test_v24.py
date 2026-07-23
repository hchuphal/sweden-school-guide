import app.main as main


def base(name, address, lat, lng, map_discovered, distance, **extra):
    item = {
        "name": name,
        "address": address,
        "lat": lat,
        "lng": lng,
        "mapDiscovered": map_discovered,
        "distanceKm": distance,
        "sources": [],
    }
    item.update(extra)
    return item


def test_swedish_linking_s_duplicate_merges_and_keeps_tracked():
    mapped = base("Jättestenskolan", "Norrviksgatan 1, Göteborg", 57.71997, 11.90194, True, 0.3)
    tracked = base("Jättestensskolan", "Norrviksgatan 1, Göteborg", 57.7180, 11.8970, False, 0.6,
                   qualityScore=73, dataCompletenessPct=100, schoolUnitId="123")
    result = main.deduplicate_nearby_candidates([mapped, tracked])
    assert len(result) == 1
    assert result[0]["name"] == "Jättestensskolan"
    assert result[0]["mapDiscovered"] is False
    assert result[0]["distanceKm"] == 0.3
    assert result[0]["qualityScore"] == 73


def test_three_source_cluster_is_order_independent():
    records = [
        base("Jättestenskolan", "Norrviksgatan 1", 57.71997, 11.90194, True, 0.3),
        base("Jättestens skolan", "Norrviksgatan 1, Göteborg", 57.7200, 11.9020, True, 0.31),
        base("Jättestensskolan", "Norrviksgatan 1, Göteborg", 57.7180, 11.8970, False, 0.6,
             schoolUnitId="123", parentOverall=7.3, dataCompletenessPct=100),
    ]
    result = main.deduplicate_nearby_candidates(list(reversed(records)))
    assert len(result) == 1
    assert result[0]["schoolUnitId"] == "123"
    assert result[0]["deduplicatedRecordCount"] == 3


def test_distinct_chain_campuses_stay_separate():
    lindholmen = base("Noblaskolan Lindholmen", "Verkmästaregatan 7", 57.708, 11.944, False, 1.0)
    kviberg = base("Noblaskolan Kviberg", "Luftvärnsvägen 4", 57.738, 12.025, False, 6.0)
    result = main.deduplicate_nearby_candidates([lindholmen, kviberg])
    assert len(result) == 2


def test_same_generic_name_far_apart_stays_separate():
    one = base("Internationella Engelska Skolan", "Södra Hamngatan 1", 57.70, 11.97, True, 1.0)
    two = base("Internationella Engelska Skolan", "Långgatan 30", 57.76, 12.08, True, 10.0)
    result = main.deduplicate_nearby_candidates([one, two])
    assert len(result) == 2


if __name__ == "__main__":
    test_swedish_linking_s_duplicate_merges_and_keeps_tracked()
    test_three_source_cluster_is_order_independent()
    test_distinct_chain_campuses_stay_separate()
    test_same_generic_name_far_apart_stays_separate()
    print("v0.24 tests passed")
