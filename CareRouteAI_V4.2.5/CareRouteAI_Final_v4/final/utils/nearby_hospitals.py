"""
nearby_hospitals.py — CareRouteAI
Fetches real nearby hospitals from OpenStreetMap Overpass API
using the patient's GPS coordinates. No API key required.
"""
import math
import urllib.request
import urllib.parse
import json
import logging

logger = logging.getLogger(__name__)

# ── Overpass query ─────────────────────────────────────────────────────────
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# OSM tags that map to medical specialties
# Used to boost score when a hospital's OSM tags match the needed specialty
_SPECIALTY_OSM_TAGS = {
    "cardiology":      ["cardiology", "cardiac", "heart"],
    "orthopedics":     ["orthopedic", "orthopaedic", "bone"],
    "neurology":       ["neurology", "neuro", "brain"],
    "pediatrics":      ["pediatric", "paediatric", "children", "child"],
    "gynecology":      ["gynecology", "gynaecology", "maternity", "obstetric"],
    "ophthalmology":   ["eye", "ophthalmology", "ophthalmic"],
    "psychiatry":      ["psychiatry", "mental health", "psychiatric"],
    "dermatology":     ["dermatology", "skin"],
    "gastroenterology":["gastro", "digestive"],
    "pulmonology":     ["pulmonology", "respiratory", "lung", "chest"],
    "urology":         ["urology", "urological"],
    "endocrinology":   ["endocrinology", "diabetes", "endocrine"],
    "ent":             ["ent", "ear", "nose", "throat", "otolaryngology"],
    "dentistry":       ["dental", "dentist", "teeth"],
    "oncology":        ["oncology", "cancer"],
}


def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lng / 2) ** 2)
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


def _fetch_overpass(lat: float, lng: float, radius_m: int) -> list[dict]:
    """
    Query Overpass for hospitals/clinics within radius_m metres of lat/lng.
    Returns raw OSM elements.
    """
    query = f"""
    [out:json][timeout:10];
    (
      node["amenity"="hospital"](around:{radius_m},{lat},{lng});
      way["amenity"="hospital"](around:{radius_m},{lat},{lng});
      node["amenity"="clinic"](around:{radius_m},{lat},{lng});
      way["amenity"="clinic"](around:{radius_m},{lat},{lng});
      node["healthcare"="hospital"](around:{radius_m},{lat},{lng});
      way["healthcare"="hospital"](around:{radius_m},{lat},{lng});
      node["healthcare"="clinic"](around:{radius_m},{lat},{lng});
    );
    out center tags;
    """
    data = urllib.parse.urlencode({"data": query}).encode()
    req  = urllib.request.Request(
        _OVERPASS_URL, data=data,
        headers={"User-Agent": "CareRouteAI/1.0 (student project)"},
    )
    with urllib.request.urlopen(req, timeout=12) as resp:
        return json.loads(resp.read())["elements"]


def _element_to_clinic(el: dict, patient_lat: float, patient_lng: float,
                        idx: int) -> dict | None:
    """Convert a raw OSM element to a clinic dict the app understands."""
    tags = el.get("tags", {})
    name = tags.get("name") or tags.get("name:en")
    if not name:
        return None  # unnamed facility — skip

    # Coordinates: nodes have lat/lon directly; ways have a "center"
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lng = el.get("lon") or el.get("center", {}).get("lon")
    if not lat or not lng:
        return None

    address_parts = [
        tags.get("addr:housenumber", ""),
        tags.get("addr:street", ""),
        tags.get("addr:suburb", "") or tags.get("addr:city", ""),
    ]
    address = ", ".join(p for p in address_parts if p) or tags.get("addr:full", "")

    phone   = tags.get("phone") or tags.get("contact:phone") or ""
    website = tags.get("website") or tags.get("contact:website") or ""

    # Detect emergency capability from OSM tags
    emergency = (
        tags.get("emergency") == "yes"
        or tags.get("emergency:phone") is not None
        or "emergency" in tags.get("healthcare:speciality", "").lower()
        or el.get("tags", {}).get("amenity") == "hospital"  # hospitals assumed capable
    )

    # Collect all specialty hints from OSM tags
    specialty_hints = " ".join([
        tags.get("healthcare:speciality", ""),
        tags.get("medical_system:western", ""),
        tags.get("description", ""),
        name,
    ]).lower()

    return {
        "id":              f"osm_{el['id']}",
        "name":            name,
        "address":         address,
        "lat":             round(float(lat), 6),
        "lng":             round(float(lng), 6),
        "distance_km":     _haversine(patient_lat, patient_lng, float(lat), float(lng)),
        "rating":          None,          # OSM has no rating
        "phone":           phone,
        "website":         website,
        "open":            tags.get("opening_hours", ""),
        "emergency":       emergency,
        "specialties":     ["generalPractice"],  # default; refined in scoring
        "specialty_hints": specialty_hints,
        "queue_length":    0,
        "est_wait_mins":   0,
        "source":          "osm",
    }


def score_hospital(clinic: dict, specialty: str, urgency: str) -> int:
    """
    Pure Python scoring — no LLM needed.
    Returns 0–100.
    """
    score = 40  # base

    # Distance — most important for emergency
    dist = clinic["distance_km"]
    if dist <= 0.5:   score += 35
    elif dist <= 1.0: score += 30
    elif dist <= 2.0: score += 24
    elif dist <= 3.5: score += 18
    elif dist <= 6.0: score += 10
    elif dist <= 10:  score += 4
    else:             score -= 5

    # Emergency capability
    if urgency in ("EMERGENCY", "URGENT"):
        score += 15 if clinic["emergency"] else -20

    # Specialty match from OSM tags
    hints = clinic.get("specialty_hints", "")
    kw    = _SPECIALTY_OSM_TAGS.get(specialty, [])
    if any(k in hints for k in kw):
        score += 15

    # Prefer named hospitals over clinics
    if "hospital" in hints or "hospital" in clinic["name"].lower():
        score += 5

    return max(0, min(100, score))


def _build_reason(clinic: dict, specialty: str, urgency: str) -> str:
    parts = []
    dist = clinic["distance_km"]
    if dist < 1:
        parts.append(f"Only {int(dist * 1000)}m away")
    else:
        parts.append(f"{dist:.1f} km away")
    if clinic["emergency"]:
        parts.append("emergency capable")
    hints = clinic.get("specialty_hints", "")
    kw    = _SPECIALTY_OSM_TAGS.get(specialty, [])
    if any(k in hints for k in kw):
        parts.append(f"{specialty} services noted")
    return ", ".join(parts)


def get_nearby_hospitals(
    patient_lat: float,
    patient_lng: float,
    specialty: str = "generalPractice",
    urgency:   str = "NON-URGENT",
    max_results: int = 15,
    radius_m: int = 5000,          # start with 5 km
) -> list[dict]:
    """
    Main entry point.
    Returns up to max_results hospitals sorted by relevance score,
    expanding radius if too few results are found.
    """
    elements = []
    current_radius = radius_m

    # Auto-expand radius up to 15 km if fewer than 5 results found nearby
    for attempt_radius in [current_radius, 8000, 15000]:
        try:
            elements = _fetch_overpass(patient_lat, patient_lng, attempt_radius)
            logger.info(f"Overpass returned {len(elements)} elements at {attempt_radius}m radius")
            if len(elements) >= 5:
                break
        except Exception as e:
            logger.warning(f"Overpass fetch failed at {attempt_radius}m: {e}")
            break

    if not elements:
        return []

    # Convert and filter
    clinics = []
    seen_names = set()
    for el in elements:
        c = _element_to_clinic(el, patient_lat, patient_lng, len(clinics))
        if c is None:
            continue
        # Deduplicate by name (OSM sometimes has node + way for the same place)
        key = c["name"].lower().strip()
        if key in seen_names:
            continue
        seen_names.add(key)
        c["score"]  = score_hospital(c, specialty, urgency)
        c["reason"] = _build_reason(c, specialty, urgency)
        clinics.append(c)

    # Sort by score desc, distance asc as tiebreaker
    clinics.sort(key=lambda c: (-c["score"], c["distance_km"]))
    return clinics[:max_results]