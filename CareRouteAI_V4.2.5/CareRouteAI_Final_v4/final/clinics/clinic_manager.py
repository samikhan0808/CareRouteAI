"""
Clinic Manager — CareRouteAI
Receptionist-facing queue CRUD + live status.
"""
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from config import QUEUE_FILE, CLINICS_FILE, MAX_QUEUE_LENGTH

_QUEUE_PATH   = Path(QUEUE_FILE)
_CLINICS_PATH = Path(CLINICS_FILE)

# Guards every load→mutate→save cycle on the queue file. Without this,
# two concurrent requests (e.g. two patients booking at once, or a patient
# booking while admin marks someone complete) can each load a stale copy
# and overwrite each other's change when they save.
#
# NOTE: this only protects against concurrency *within one process*. If
# CareRouteAI is ever run with multiple worker processes (gunicorn -w N,
# multiple `flask run` instances, etc.) this lock does nothing across
# processes — the queue file needs an OS-level file lock (e.g. `filelock`
# package or `fcntl.flock`) or, better, a real datastore (SQLite/Postgres)
# instead of a shared JSON file. Treat this lock as a stopgap for local/
# single-process use, not a production concurrency fix.
_QUEUE_LOCK = threading.Lock()


# ── Helpers ──────────────────────────────────────────────
def _load_clinics() -> list[dict]:
    return json.loads(_CLINICS_PATH.read_text())


def _init_queues_from_clinics() -> dict:
    """Build a fresh queue dict from clinics.json."""
    clinics = _load_clinics()
    queues  = {}
    for c in clinics:
        avg = 15
        if isinstance(c.get("avg_consult_mins"), dict):
            vals = list(c["avg_consult_mins"].values())
            avg  = int(sum(vals) / len(vals)) if vals else 15
        queues[c["id"]] = {
            "clinic_id":       c["id"],
            "patients":        [],
            "avg_consult_mins": avg,
            "next_token":      1,
        }
    return queues


def _load_queues() -> dict:
    """Load queue file; auto-seed from clinics.json if missing or empty."""
    if _QUEUE_PATH.exists():
        try:
            data = json.loads(_QUEUE_PATH.read_text())
            if data:            # non-empty dict → use it
                return data
        except json.JSONDecodeError:
            pass

    # File missing, empty, or corrupt → seed from clinics
    queues = _init_queues_from_clinics()
    _save_queues(queues)
    return queues


def _save_queues(queues: dict) -> None:
    _QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_PATH.write_text(json.dumps(queues, indent=2, default=str))


def _ensure_clinic(queues: dict, clinic_id: str) -> dict:
    """
    If a clinic_id is absent from the loaded queues (e.g. queue file was
    partially written), seed it from clinics.json on the fly.
    """
    if clinic_id not in queues:
        clinics = {c["id"]: c for c in _load_clinics()}
        if clinic_id not in clinics:
            raise ValueError(
                f"Clinic '{clinic_id}' not found in {CLINICS_FILE}. "
                "Check that clinics.json contains this id."
            )
        c   = clinics[clinic_id]
        avg = 15
        if isinstance(c.get("avg_consult_mins"), dict):
            vals = list(c["avg_consult_mins"].values())
            avg  = int(sum(vals) / len(vals)) if vals else 15
        queues[clinic_id] = {
            "clinic_id":        clinic_id,
            "patients":         [],
            "avg_consult_mins": avg,
            "next_token":       1,
        }
    return queues


# ── Receptionist functions ───────────────────────────────
def add_patient(clinic_id: str, name: str, phone: str = "",
                gender: str = "unspecified",
                specialty: str = "generalPractice",
                doctor_summary: str = "",
                urgency: str = "NON-URGENT",
                primary_concern: str = "",
                key_symptoms: list = None,
                duration: str = "",
                conversation_log: list = None) -> dict:
    """Add a patient to the queue. Returns the new patient record."""
    with _QUEUE_LOCK:
        queues = _load_queues()
        queues = _ensure_clinic(queues, clinic_id)   # ← auto-seed if needed

        queue = queues[clinic_id]
        if len(queue["patients"]) >= MAX_QUEUE_LENGTH:
            raise RuntimeError("Queue is full.")

        # Token must be unique for the lifetime of the queue, not just among
        # patients currently present — using last-in-list + 1 reuses numbers
        # once any patient (not just the very last one) is removed, which lets
        # two different people be called by the same token at the front desk.
        # Track a running counter per clinic instead.
        next_token = queue.get("next_token")
        if next_token is None:
            # Back-compat for queues created before this counter existed.
            next_token = max((p["token"] for p in queue["patients"]), default=0) + 1
        queue["next_token"] = next_token + 1

        avg  = queue.get("avg_consult_mins", 15)
        safe_name = (name or "").strip()[:80]   # cap length; defense-in-depth alongside client-side escaping
        patient = {
            "id":             str(uuid.uuid4())[:8],
            "name":           safe_name,
            "phone":          phone,
            "gender":         gender,
            "specialty":      specialty,
            "token":          next_token,
            "wait_mins":      len(queue["patients"]) * avg,
            "status":         "waiting",
            "added_at":       datetime.now().isoformat(),
            "doctor_summary": (doctor_summary or "").strip()[:1200],
            "conversation_log": conversation_log or [],
            "urgency": urgency,
            "primary_concern": primary_concern,
            "key_symptoms": key_symptoms or [],
            "duration": duration,
        }
        queue["patients"].append(patient)
        _save_queues(queues)
        return patient


def remove_patient(clinic_id: str, patient_id: str) -> bool:
    """Remove a patient by id. Returns True if found and removed."""
    with _QUEUE_LOCK:
        queues = _load_queues()
        queues = _ensure_clinic(queues, clinic_id)
        before = len(queues[clinic_id]["patients"])
        queues[clinic_id]["patients"] = [
            p for p in queues[clinic_id]["patients"] if p["id"] != patient_id
        ]
        removed = len(queues[clinic_id]["patients"]) < before
        if removed:
            _save_queues(queues)
        return removed


def mark_complete(clinic_id: str, patient_id: str) -> bool:
    """Remove the completed patient and advance the queue. Returns True if
    `patient_id` was found and removed, False if it wasn't in the queue."""
    with _QUEUE_LOCK:
        queues   = _load_queues()
        queues   = _ensure_clinic(queues, clinic_id)
        patients = queues[clinic_id]["patients"]
        before_ids = {p["id"] for p in patients}
        found = patient_id in before_ids

        remaining = [p for p in patients if p["id"] != patient_id]
        queues[clinic_id]["patients"] = remaining
        # Only auto-advance the next patient if nobody is already mid-consultation —
        # otherwise completing one patient could silently bump a second patient
        # into "in_consultation" while the room is still occupied.
        already_in_consult = any(p["status"] == "in_consultation" for p in remaining)
        if remaining and not already_in_consult:
            remaining[0]["status"] = "in_consultation"

        _save_queues(queues)
        return found


def call_next(clinic_id: str) -> dict | None:
    """Move the next waiting patient to in_consultation."""
    with _QUEUE_LOCK:
        queues = _load_queues()
        queues = _ensure_clinic(queues, clinic_id)
        for p in queues[clinic_id]["patients"]:
            if p["status"] == "waiting":
                p["status"] = "in_consultation"
                _save_queues(queues)
                return p
        return None


def get_queue(clinic_id: str) -> dict:
    """Return full queue data for one clinic."""
    with _QUEUE_LOCK:
        queues = _load_queues()
        queues = _ensure_clinic(queues, clinic_id)
        return queues[clinic_id]


def get_patient(clinic_id: str, patient_id: str) -> dict | None:
    """Return a single patient record from a clinic queue."""
    with _QUEUE_LOCK:
        queues = _load_queues()
        queues = _ensure_clinic(queues, clinic_id)
        return next((p for p in queues[clinic_id]["patients"] if p["id"] == patient_id), None)


def all_queues_summary() -> list[dict]:
    """Summary list of all clinic queues."""
    with _QUEUE_LOCK:
        queues  = _load_queues()
        clinics = {c["id"]: c for c in _load_clinics()}
        return [
            {
                "clinic_id":      cid,
                "clinic_name":    clinics.get(cid, {}).get("name", cid),
                "queue_length":   len(q["patients"]),
                "waiting_count":  sum(1 for p in q["patients"]
                                      if p.get("status") == "waiting"),
                "in_consult":     sum(1 for p in q["patients"]
                                      if p.get("status") == "in_consultation"),
                "avg_wait_mins":  q.get("avg_consult_mins", 15),
                "total_wait_min": len(q["patients"]) * q.get("avg_consult_mins", 15),
            }
            for cid, q in queues.items()
        ]
