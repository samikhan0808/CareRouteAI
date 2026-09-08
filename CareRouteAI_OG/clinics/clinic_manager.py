"""
Clinic Manager — CareRouteAI (DATABASE VERSION)
Receptionist-facing queue CRUD + live status.

This replaces the old JSON-file version. Same function names, same
arguments, same return shapes as before — so app.py does NOT need to
change. Internally it now reads/writes SQLite via SQLAlchemy models.
"""
import json
import threading
import uuid
from datetime import datetime

from models import db, Hospital, HospitalLocation, Specialty, HospitalSpecialty, Appointment, QueueToken
from config import MAX_QUEUE_LENGTH

_QUEUE_LOCK = threading.Lock()


def _load_clinics() -> list[dict]:
    """Return clinics in the same shape the old clinics.json gave us."""
    clinics = []
    hospitals = Hospital.query.all()
    for h in hospitals:
        loc = HospitalLocation.query.filter_by(hospital_id=h.hospital_id).first()
        specialty_links = HospitalSpecialty.query.filter_by(hospital_id=h.hospital_id).all()
        specialty_names = []
        for link in specialty_links:
            spec = db.session.get(Specialty, link.specialty_id)
            if spec:
                specialty_names.append(spec.name)

        avg_consult_mins = {}
        if h.avg_consult_mins_json:
            try:
                avg_consult_mins = json.loads(h.avg_consult_mins_json)
            except (json.JSONDecodeError, TypeError):
                avg_consult_mins = {}

        clinics.append({
            "id": h.hospital_id,
            "name": h.name,
            "address": h.address,
            "lat": loc.latitude if loc else None,
            "lng": loc.longitude if loc else None,
            "specialties": specialty_names,
            "rating": h.rating,
            "phone": h.phone,
            "open": h.open_hours,
            "emergency": bool(h.emergency_available),
            "avg_consult_mins": avg_consult_mins,
        })
    return clinics


def _avg_consult_mins_for(clinic_id: str) -> int:
    hospital = db.session.get(Hospital, clinic_id)
    if not hospital or not hospital.avg_consult_mins_json:
        return 15
    try:
        vals = list(json.loads(hospital.avg_consult_mins_json).values())
        return int(sum(vals) / len(vals)) if vals else 15
    except (json.JSONDecodeError, TypeError, ZeroDivisionError):
        return 15


def _token_to_patient_dict(token: QueueToken, appt: Appointment) -> dict:
    try:
        key_symptoms = json.loads(appt.key_symptoms_json) if appt.key_symptoms_json else []
    except (json.JSONDecodeError, TypeError):
        key_symptoms = []
    try:
        conversation_log = json.loads(appt.conversation_log_json) if appt.conversation_log_json else []
    except (json.JSONDecodeError, TypeError):
        conversation_log = []

    token_num = token.token_number
    if token_num is not None and str(token_num).isdigit():
        token_num = int(token_num)

    return {
        "id": token.public_id,
        "name": appt.patient_name,
        "phone": appt.phone,
        "gender": appt.gender,
        "specialty": appt.reason,
        "token": token_num,
        "wait_mins": token.estimated_wait_minutes,
        "status": token.queue_status,
        "added_at": token.issued_at.isoformat() if token.issued_at else None,
        "doctor_summary": appt.doctor_summary,
        "conversation_log": conversation_log,
        "urgency": appt.urgency,
        "primary_concern": appt.primary_concern,
        "key_symptoms": key_symptoms,
        "duration": appt.duration,
    }


def _ensure_clinic(clinic_id: str) -> None:
    if not db.session.get(Hospital, clinic_id):
        raise ValueError(
            f"Clinic '{clinic_id}' not found in the database. "
            "Run migrate_clinics.py first so the hospitals table is populated."
        )


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
        _ensure_clinic(clinic_id)

        current_count = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.queue_status.in_(["waiting", "in_consultation"]))
            .count()
        )
        if current_count >= MAX_QUEUE_LENGTH:
            raise RuntimeError("Queue is full.")

        last_token_row = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .order_by(QueueToken.token_id.desc())
            .first()
        )
        next_token = 1
        if last_token_row and last_token_row.token_number and str(last_token_row.token_number).isdigit():
            next_token = int(last_token_row.token_number) + 1

        avg = _avg_consult_mins_for(clinic_id)
        safe_name = (name or "").strip()[:80]

        appt = Appointment(
            hospital_id=clinic_id,
            status="waiting",
            reason=specialty,
            patient_name=safe_name,
            phone=phone,
            gender=gender,
            urgency=urgency,
            primary_concern=primary_concern,
            key_symptoms_json=json.dumps(key_symptoms or []),
            duration=duration,
            doctor_summary=(doctor_summary or "").strip()[:1200],
            conversation_log_json=json.dumps(conversation_log or []),
            created_at=datetime.utcnow(),
        )
        db.session.add(appt)
        db.session.flush()

        token = QueueToken(
            appointment_id=appt.appointment_id,
            token_number=str(next_token),
            queue_status="waiting",
            estimated_wait_minutes=current_count * avg,
            issued_at=datetime.utcnow(),
            public_id=str(uuid.uuid4())[:8],
        )
        db.session.add(token)
        db.session.commit()

        return _token_to_patient_dict(token, appt)


def remove_patient(clinic_id: str, patient_id: str) -> bool:
    """Remove a patient by id. Returns True if found and removed."""
    with _QUEUE_LOCK:
        token = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.public_id == patient_id)
            .first()
        )
        if not token:
            return False
        db.session.delete(token)
        db.session.commit()
        return True


def mark_complete(clinic_id: str, patient_id: str) -> bool:
    """Mark patient completed and advance the queue. Returns True if found."""
    with _QUEUE_LOCK:
        target = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.public_id == patient_id)
            .first()
        )
        found = target is not None
        if not target:
            return False

        target.queue_status = "completed"
        target.completed_at = datetime.utcnow()

        remaining = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.queue_status.in_(["waiting", "in_consultation"]))
            .order_by(QueueToken.token_id.asc())
            .all()
        )
        already_in_consult = any(t.queue_status == "in_consultation" for t in remaining)
        if remaining and not already_in_consult:
            remaining[0].queue_status = "in_consultation"
            remaining[0].called_at = datetime.utcnow()

        db.session.commit()
        return True


def call_next(clinic_id: str) -> dict | None:
    """Move the next waiting patient to in_consultation."""
    with _QUEUE_LOCK:
        token = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.queue_status == "waiting")
            .order_by(QueueToken.token_id.asc())
            .first()
        )
        if not token:
            return None
        token.queue_status = "in_consultation"
        token.called_at = datetime.utcnow()
        db.session.commit()
        appt = db.session.get(Appointment, token.appointment_id)
        return _token_to_patient_dict(token, appt)


def get_queue(clinic_id: str) -> dict:
    """Return full queue data for one clinic (same shape as old JSON block)."""
    with _QUEUE_LOCK:
        _ensure_clinic(clinic_id)
        tokens = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.queue_status.in_(["waiting", "in_consultation"]))
            .order_by(QueueToken.token_id.asc())
            .all()
        )
        patients = []
        for t in tokens:
            appt = db.session.get(Appointment, t.appointment_id)
            patients.append(_token_to_patient_dict(t, appt))

        last_token_row = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .order_by(QueueToken.token_id.desc())
            .first()
        )
        next_token = 1
        if last_token_row and last_token_row.token_number and str(last_token_row.token_number).isdigit():
            next_token = int(last_token_row.token_number) + 1

        return {
            "clinic_id": clinic_id,
            "patients": patients,
            "avg_consult_mins": _avg_consult_mins_for(clinic_id),
            "next_token": next_token,
        }


def get_patient(clinic_id: str, patient_id: str) -> dict | None:
    """Return a single patient record from a clinic queue."""
    with _QUEUE_LOCK:
        token = (
            QueueToken.query.join(Appointment)
            .filter(Appointment.hospital_id == clinic_id)
            .filter(QueueToken.public_id == patient_id)
            .first()
        )
        if not token:
            return None
        appt = db.session.get(Appointment, token.appointment_id)
        return _token_to_patient_dict(token, appt)


def all_queues_summary() -> list[dict]:
    """Summary list of all clinic queues."""
    with _QUEUE_LOCK:
        hospitals = Hospital.query.all()
        summary = []
        for h in hospitals:
            tokens = (
                QueueToken.query.join(Appointment)
                .filter(Appointment.hospital_id == h.hospital_id)
                .filter(QueueToken.queue_status.in_(["waiting", "in_consultation"]))
                .all()
            )
            avg = _avg_consult_mins_for(h.hospital_id)
            summary.append({
                "clinic_id": h.hospital_id,
                "clinic_name": h.name,
                "queue_length": len(tokens),
                "waiting_count": sum(1 for t in tokens if t.queue_status == "waiting"),
                "in_consult": sum(1 for t in tokens if t.queue_status == "in_consultation"),
                "avg_wait_mins": avg,
                "total_wait_min": len(tokens) * avg,
            })
        return summary
