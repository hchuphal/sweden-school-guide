from app.main import _parse_overpass_schools, is_relevant_ground_school


def test_sports_hall_is_not_a_school():
    tags = {
        "name": "Sjumilahallen",
        "building": "school",
        "leisure": "sports_hall",
    }
    assert is_relevant_ground_school("Sjumilahallen", tags) is False


def test_actual_school_is_retained():
    tags = {
        "name": "Sjumilaskolan",
        "amenity": "school",
        "building": "school",
        "school:grades": "F-6",
        "addr:street": "Friskväderstorget",
        "addr:housenumber": "13",
    }
    assert is_relevant_ground_school("Sjumilaskolan", tags) is True


def test_overpass_payload_filters_campus_facility():
    payload = {
        "elements": [
            {
                "type": "way",
                "id": 412990445,
                "center": {"lat": 57.72603, "lon": 11.89271},
                "tags": {
                    "name": "Sjumilahallen",
                    "building": "school",
                    "leisure": "sports_hall",
                },
            },
            {
                "type": "way",
                "id": 12345,
                "center": {"lat": 57.7249, "lon": 11.8951},
                "tags": {
                    "name": "Sjumilaskolan",
                    "amenity": "school",
                    "building": "school",
                    "school:grades": "F-6",
                    "addr:street": "Friskväderstorget",
                    "addr:housenumber": "13",
                },
            },
        ]
    }
    schools = _parse_overpass_schools(payload, "goteborg")
    assert [school["name"] for school in schools] == ["Sjumilaskolan"]


if __name__ == "__main__":
    test_sports_hall_is_not_a_school()
    test_actual_school_is_retained()
    test_overpass_payload_filters_campus_facility()
    print("v0.25 tests passed")
