from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_bundled_city_records_are_isolated() -> None:
    records = json.loads((ROOT / "data/imports/schools-2026-city-baselines.json").read_text())
    expected = {"stockholm", "malmo", "uppsala"}
    assert expected.issubset({item["cityKey"] for item in records})
    for item in records:
        city = item["cityKey"]
        municipality = str(item.get("municipality") or "").lower()
        if city == "stockholm":
            assert "stockholm" in municipality
        elif city == "malmo":
            assert "malm" in municipality
        elif city == "uppsala":
            assert "uppsala" in municipality


def test_frontend_has_stale_city_response_guard() -> None:
    js = (ROOT / "static/app.js").read_text()
    assert "cityLoadEpoch" in js
    assert "requestEpoch !== cityLoadEpoch" in js
    assert "payload.city !== cityKey" in js
    assert "school.cityKey !== cityKey" in js
    assert "directoryStatus" in js


if __name__ == "__main__":
    test_bundled_city_records_are_isolated()
    test_frontend_has_stale_city_response_guard()
    print("v0.19 tests passed")
