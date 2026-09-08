"""
Analytics helper — CareRouteAI
Records triage and booking events, then computes simple dashboard summaries.
"""
import json
import logging
import os
import threading
from collections import Counter
from pathlib import Path
from datetime import datetime
from config import ANALYTICS_FILE

logger = logging.getLogger("carerouteai.analytics")
_ANALYTICS_LOCK = threading.Lock()


def _load_analytics() -> dict:
    path = Path(ANALYTICS_FILE)
    if not path.exists():
        data = {"triages": [], "bookings": []}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return data

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Analytics file at %s is corrupt: %s", path, exc)
        return {"triages": [], "bookings": []}


def _save_analytics(data: dict) -> None:
    path = Path(ANALYTICS_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp_path, path)


def _timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def record_triage_event(specialty: str, urgency: str, language: str, predictions: list[dict]) -> None:
    with _ANALYTICS_LOCK:
        analytics = _load_analytics()
        analytics["triages"].append({
            "timestamp": _timestamp(),
            "specialty": specialty,
            "urgency": urgency,
            "language": language,
            "predictions": predictions or [],
        })
        _save_analytics(analytics)


def record_booking_event(clinic_id: str, clinic_name: str, specialty: str, urgency: str, language: str) -> None:
    with _ANALYTICS_LOCK:
        analytics = _load_analytics()
        analytics["bookings"].append({
            "timestamp": _timestamp(),
            "clinic_id": clinic_id,
            "clinic_name": clinic_name,
            "specialty": specialty,
            "urgency": urgency,
            "language": language,
        })
        _save_analytics(analytics)


def load_analytics_summary() -> dict:
    analytics = _load_analytics()
    triages = analytics.get("triages", [])
    bookings = analytics.get("bookings", [])

    total_triages = len(triages)
    total_bookings = len(bookings)
    booking_rate = round((total_bookings / total_triages) * 100, 1) if total_triages else 0.0

    specialty_counts = Counter(t["specialty"] for t in triages if t.get("specialty"))
    urgency_counts = Counter(t["urgency"] for t in triages if t.get("urgency"))
    language_counts = Counter(t["language"] for t in triages if t.get("language"))

    condition_counts = Counter()
    for t in triages:
        for p in t.get("predictions", []):
            condition_counts[p.get("condition", "Unknown")] += 1

    return {
        "total_triages": total_triages,
        "total_bookings": total_bookings,
        "booking_rate": booking_rate,
        "top_specialties": specialty_counts.most_common(5),
        "urgency_distribution": urgency_counts,
        "language_usage": language_counts,
        "top_conditions": condition_counts.most_common(6),
        "recent_triages": triages[-5:],
    }
