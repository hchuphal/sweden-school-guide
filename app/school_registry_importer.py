from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests


def now_date() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def normalise(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return normalise(value) in {"true", "yes", "ja", "1", "active", "aktiv"}


def walk(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[tuple[str, ...], Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = (*path, str(key))
            yield child_path, child
            yield from walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, (*path, str(index)))


def first_by_keys(value: Any, keys: set[str]) -> Any:
    wanted = {normalise(key).replace(" ", "") for key in keys}
    for path, candidate in walk(value):
        if not path:
            continue
        key = normalise(path[-1]).replace(" ", "")
        if key in wanted and candidate not in (None, "", [], {}):
            return candidate
    return None


def all_strings(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for key, child in value.items():
            found.append(str(key))
            found.extend(all_strings(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(all_strings(child))
    return found


def extract_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "schoolUnits", "school_units", "items", "content", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_items(value)
            if nested:
                return nested
    embedded = payload.get("_embedded")
    if isinstance(embedded, dict):
        for value in embedded.values():
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    # Last-resort support for wrappers whose list key changes between API versions.
    candidates: list[list[dict[str, Any]]] = []
    def collect(value: Any) -> None:
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            candidates.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
    collect(payload)
    return max(candidates, key=len) if candidates else []


def _next_page_from_metadata(payload: dict[str, Any], current_url: str) -> str | None:
    """Build a next-page URL for common Spring/JSON-API pagination shapes."""
    page_blocks = [payload.get("page"), payload.get("meta"), payload.get("pagination")]
    for block in page_blocks:
        if not isinstance(block, dict):
            continue
        current = block.get("number", block.get("page", block.get("currentPage")))
        total = block.get("totalPages", block.get("total_pages", block.get("pageCount")))
        last = block.get("last")
        try:
            current_i = int(current)
            total_i = int(total) if total is not None else None
        except (TypeError, ValueError):
            continue
        if last is True or (total_i is not None and current_i + 1 >= total_i):
            return None
        parts = urlsplit(current_url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        page_param = "page"
        for candidate in ("page", "pageNumber", "page_number", "number"):
            if candidate in query:
                page_param = candidate
                break
        query[page_param] = str(current_i + 1)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return None


def next_link(payload: Any, current_url: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    candidates: list[Any] = [payload.get("next"), payload.get("nextPage")]
    links = payload.get("links") or payload.get("_links")
    if isinstance(links, dict):
        candidates.extend([links.get("next"), links.get("nextPage")])
    page = payload.get("page") or payload.get("meta") or payload.get("pagination")
    if isinstance(page, dict):
        candidates.extend([page.get("next"), page.get("nextPage")])
    for candidate in candidates:
        if isinstance(candidate, dict):
            candidate = candidate.get("href") or candidate.get("url")
        if isinstance(candidate, str) and candidate.strip():
            return urljoin(current_url, candidate.strip())
    return _next_page_from_metadata(payload, current_url)

def fetch_school_units(url: str, user_agent: str, max_pages: int = 100) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    headers = {"Accept": "application/json", "User-Agent": user_agent}
    all_items: list[dict[str, Any]] = []
    page_url: str | None = url
    pages = 0
    seen_urls: set[str] = set()
    while page_url and pages < max_pages and page_url not in seen_urls:
        seen_urls.add(page_url)
        response = requests.get(page_url, headers=headers, timeout=60)
        response.raise_for_status()
        payload = response.json()
        items = extract_items(payload)
        all_items.extend(items)
        pages += 1
        page_url = next_link(payload, page_url)
        # Some API responses return every record without pagination.
        if not page_url:
            break
    return all_items, {"pages": pages, "rawRecords": len(all_items), "source": url}


def attributes_of(item: dict[str, Any]) -> dict[str, Any]:
    attributes = item.get("attributes")
    if isinstance(attributes, dict):
        merged = dict(attributes)
        if item.get("id") is not None:
            merged.setdefault("id", item.get("id"))
        relationships = item.get("relationships")
        if isinstance(relationships, dict):
            merged.setdefault("relationships", relationships)
        return merged
    return item


def municipality_parts(attrs: dict[str, Any]) -> tuple[str | None, str | None]:
    code = first_by_keys(attrs, {"municipalityCode", "municipality_code", "kommunkod", "municipalityId"})
    name = first_by_keys(attrs, {"municipalityName", "municipality_name", "kommunnamn"})
    municipality = first_by_keys(attrs, {"municipality", "kommun"})
    if isinstance(municipality, dict):
        code = code or first_by_keys(municipality, {"code", "municipalityCode", "kommunkod", "id"})
        name = name or first_by_keys(municipality, {"name", "municipalityName", "kommunnamn", "label"})
    elif isinstance(municipality, str) and not name:
        name = municipality
    code_text = re.sub(r"\D", "", str(code or "")) or None
    if code_text and len(code_text) < 4:
        code_text = code_text.zfill(4)
    return code_text, str(name).strip() if name else None


def choose_address(attrs: dict[str, Any]) -> dict[str, Any]:
    address_candidates: list[dict[str, Any]] = []
    def visit(candidate: Any) -> None:
        if isinstance(candidate, dict):
            keys = {normalise(key).replace(" ", "") for key in candidate.keys()}
            if keys & {"postalcode", "postcode", "zip", "city", "locality", "addressline1", "streetaddress", "street"}:
                address_candidates.append(candidate)
            for child in candidate.values():
                visit(child)
        elif isinstance(candidate, list):
            for child in candidate:
                visit(child)
    visit(attrs)
    if not address_candidates:
        return {}
    def score(candidate: dict[str, Any]) -> tuple[int, int]:
        text = normalise(json.dumps(candidate, ensure_ascii=False))
        visiting = 2 if any(token in text for token in ("visiting", "besok", "belagenhet")) else 0
        useful = sum(1 for key in ("postalCode", "postcode", "city", "streetAddress", "addressLine1", "street") if first_by_keys(candidate, {key}))
        return visiting, useful
    return sorted(address_candidates, key=score, reverse=True)[0]


def address_text(address: dict[str, Any], fallback_city: str | None) -> tuple[str, str | None]:
    street = first_by_keys(address, {"streetAddress", "street", "addressLine1", "address", "deliveryAddress", "visitingAddress"})
    number = first_by_keys(address, {"streetNumber", "houseNumber"})
    postal = first_by_keys(address, {"postalCode", "postcode", "zipCode", "zip"})
    city = first_by_keys(address, {"city", "postTown", "locality", "postalTown"}) or fallback_city
    street_text = str(street or "").strip()
    if number and str(number) not in street_text:
        street_text = f"{street_text} {number}".strip()
    components = [part for part in (street_text, str(postal or "").strip(), str(city or "").strip()) if part]
    return ", ".join(components), str(postal).strip() if postal else None


def coordinates(attrs: dict[str, Any], address: dict[str, Any]) -> tuple[float | None, float | None]:
    for source in (address, attrs):
        lat = first_by_keys(source, {"latitude", "lat", "yCoordinate", "northing"})
        lng = first_by_keys(source, {"longitude", "lon", "lng", "xCoordinate", "easting"})
        try:
            if lat is not None and lng is not None:
                lat_f, lng_f = float(lat), float(lng)
                if -90 <= lat_f <= 90 and -180 <= lng_f <= 180:
                    return lat_f, lng_f
        except (TypeError, ValueError):
            pass
    return None, None


def school_unit_code(attrs: dict[str, Any]) -> str | None:
    value = first_by_keys(attrs, {"schoolUnitCode", "school_unit_code", "skolenhetskod", "code", "id"})
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or str(value).strip()


def school_name(attrs: dict[str, Any]) -> str | None:
    value = first_by_keys(attrs, {"schoolUnitName", "school_unit_name", "schoolName", "name", "skolenhetsnamn"})
    return str(value).strip() if value else None


def is_active(attrs: dict[str, Any]) -> bool:
    status = first_by_keys(attrs, {"status", "schoolUnitStatus", "school_unit_status", "statusCode"})
    if status is None:
        return True
    text = normalise(status)
    return not any(token in text for token in ("inactive", "ceased", "closed", "upphort", "nedlagd"))


def grade_profile(attrs: dict[str, Any]) -> tuple[bool, str]:
    flattened = [(normalise(" ".join(path)).replace(" ", ""), value) for path, value in walk(attrs)]
    strings = " ".join(normalise(value) for value in all_strings(attrs))
    has_gr = any(token in strings.split() for token in ("gr", "grundskola")) or any(
        key.endswith("schooltypesgr") or key.endswith("schooltypegr") or key == "gr" for key, _ in flattened
    )
    has_f = "forskoleklass" in strings or "preschool class" in strings or any(
        any(token in key for token in ("preschoolclass", "forskoleklass", "gradef", "yearf")) and truthy(value)
        for key, value in flattened
    )
    grades: list[int] = []
    for key, value in flattened:
        if not truthy(value):
            continue
        match = re.search(r"(?:grade|year|arskurs|ak)([1-9])$", key)
        if match:
            grades.append(int(match.group(1)))
    if not has_gr and grades:
        has_gr = True
    if not has_gr:
        return False, ""
    if grades:
        low, high = min(grades), max(grades)
        prefix = "F" if has_f else str(low)
        return True, f"{prefix}–{high}"
    return True, "F–9" if has_f else "Grundskola"


def school_type(attrs: dict[str, Any]) -> str:
    organizer = first_by_keys(attrs, {
        "organizerType", "principalOrganizerType", "ownerType", "huvudmannatyp",
        "organizer", "principalOrganizer", "owner",
    })
    text = normalise(" ".join(all_strings(organizer)) if isinstance(organizer, (dict, list)) else organizer)
    if any(token in text for token in ("enskild", "fristaende", "independent", "private")):
        return "Fristående"
    if any(token in text for token in ("kommunal", "municipal", "kommun")):
        return "Municipal"
    return "Unknown"


def city_key_for(code: str | None, name: str | None, city_config: dict[str, dict[str, Any]]) -> str | None:
    code = str(code or "")
    name_norm = normalise(name)
    for key, config in city_config.items():
        if code and code in {str(item) for item in config.get("municipality_codes", set())}:
            return key
        aliases = {normalise(item) for item in config.get("municipality_aliases", set())}
        if name_norm and (name_norm in aliases or any(alias and alias in name_norm for alias in aliases)):
            return key
    return None


def make_slug(city_key: str, code: str, name: str) -> str:
    clean_name = normalise(name).replace(" ", "-")[:50].strip("-")
    digest = hashlib.sha1(f"{city_key}:{code}:{name}".encode("utf-8")).hexdigest()[:6]
    return f"{city_key}-{clean_name or 'school'}-{code or digest}"[:100]


def build_records(items: list[dict[str, Any]], city_config: dict[str, dict[str, Any]], year: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    skipped = {"outside_regions": 0, "not_ground_school": 0, "inactive": 0, "missing_identity": 0}
    for item in items:
        attrs = attributes_of(item)
        code = school_unit_code(attrs)
        name = school_name(attrs)
        municipality_code, municipality_name = municipality_parts(attrs)
        city_key = city_key_for(municipality_code, municipality_name, city_config)
        if not city_key:
            skipped["outside_regions"] += 1
            continue
        if not is_active(attrs):
            skipped["inactive"] += 1
            continue
        is_ground, grades = grade_profile(attrs)
        if not is_ground:
            skipped["not_ground_school"] += 1
            continue
        if not code or not name:
            skipped["missing_identity"] += 1
            continue
        addr = choose_address(attrs)
        address, postal = address_text(addr, municipality_name)
        lat, lng = coordinates(attrs, addr)
        school_unit_id = int(code) if str(code).isdigit() else code
        records.append({
            "slug": make_slug(city_key, str(code), name),
            "name": name,
            "type": school_type(attrs),
            "grades": grades,
            "area": municipality_name or city_config[city_key]["label"],
            "address": address or municipality_name or city_config[city_key]["label"],
            "lat": lat,
            "lng": lng,
            "profile": "Official school registry record",
            "schoolUnitId": school_unit_id,
            "cityKey": city_key,
            "municipality": municipality_name,
            "postalCode": postal,
            "registrySource": "Skolverket school-unit register",
            "dataYear": year,
            "lastVerified": now_date(),
            "verificationNote": "School facts imported from Skolverket's daily school-unit register. Ratings and admission fields appear only when separately imported.",
            "sources": [{
                "label": "Skolverket / Utbildningsguiden",
                "url": f"https://utbildningsguiden.skolverket.se/skolenhet?schoolUnitID={code}",
            }],
        })
    metadata = {"matched": len(records), "skipped": skipped, "year": year}
    return records, metadata
