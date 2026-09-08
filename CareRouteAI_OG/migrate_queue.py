"""
One-time script: migrate data/queue_status.json -> appointments + queue_tokens
tables in careroute.db

Structure of queue_status.json:
  { "c1": { "clinic_id": "c1", "patients": [ {...} ], "next_token": N }, ... }

Run this ONCE from the final/ folder (after migrate_clinics.py):
    python migrate_queue.py
"""
import json
from datetime import datetime
from web.app import app
from models import db, Appointment, QueueToken, Hospital


def _parse_dt(value):
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.utcnow()


with open("data/queue_status.json", "r", encoding="utf-8") as f:
    queue_data = json.load(f)

with app.app_context():
    added, skipped = 0, 0

    for clinic_id, clinic_block in queue_data.items():
        # make sure this hospital actually exists in DB (from migrate_clinics.py)
        if not db.session.get(Hospital, clinic_id):
            print(f"WARNING: hospital {clinic_id} not found in DB, skipping its patients")
            continue

        for p in clinic_block.get("patients", []):
            # avoid duplicate re-import if script is run twice
            existing = (
                QueueToken.query.join(Appointment)
                .filter(Appointment.hospital_id == clinic_id)
                .filter(QueueToken.token_number == str(p.get("token")))
                .first()
            )
            if existing:
                skipped += 1
                continue

            appointment = Appointment(
            hospital_id=clinic_id,
            status=p.get("status", "waiting"),
            reason=p.get("specialty"),
            patient_name=p.get("name", ""),
            phone=p.get("phone", ""),
            gender=p.get("gender", "unspecified"),
            urgency=p.get("urgency", "NON-URGENT"),
            primary_concern=p.get("primary_concern", ""),
            key_symptoms_json=json.dumps(p.get("key_symptoms", [])),
            duration=p.get("duration", ""),
            doctor_summary=p.get("doctor_summary", ""),
            conversation_log_json=json.dumps(p.get("conversation_log", [])),
            created_at=_parse_dt(p.get("added_at")),
            )
            db.session.add(appointment)
            db.session.flush()  # get appointment_id before using it below

            token = QueueToken(
            appointment_id=appointment.appointment_id,
            token_number=str(p.get("token")),
            queue_status=p.get("status", "waiting"),
            estimated_wait_minutes=p.get("wait_mins"),
            issued_at=_parse_dt(p.get("added_at")),
            public_id=p.get("id"),
            )
            db.session.add(token)
            added += 1

    db.session.commit()
    print(f"Done. Added: {added}, Skipped (already existed): {skipped}")
