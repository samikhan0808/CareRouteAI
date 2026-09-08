"""
One-time script: migrate clinics/clinics.json -> hospitals, hospital_locations,
specialties, hospital_specialties tables in careroute.db

Run this ONCE from the final/ folder:
    python migrate_clinics.py
"""
import json
from web.app import app  # uses the same Flask app + DB config as your server
from models import db, Hospital, HospitalLocation, Specialty, HospitalSpecialty

with open("clinics/clinics.json", "r", encoding="utf-8") as f:
    clinics_data = json.load(f)

with app.app_context():
    added, skipped = 0, 0

    for c in clinics_data:
        # skip if this clinic id is already in the DB (safe to re-run)
        if db.session.get(Hospital, c["id"]):
            skipped += 1
            continue

        hospital = Hospital(
            hospital_id=c["id"],
            name=c.get("name"),
            address=c.get("address"),
            phone=c.get("phone"),
            rating=c.get("rating"),
            emergency_available=c.get("emergency", False),
            status="active",
            open_hours=c.get("open"),
            avg_consult_mins_json=json.dumps(c.get("avg_consult_mins", {})),
        )
        db.session.add(hospital)

        # location
        db.session.add(HospitalLocation(
            hospital_id=c["id"],
            latitude=c.get("lat"),
            longitude=c.get("lng"),
        ))

        # specialties (create Specialty row if it doesn't exist yet, then link)
        for spec_name in c.get("specialties", []):
            specialty = Specialty.query.filter_by(name=spec_name).first()
            if not specialty:
                specialty = Specialty(name=spec_name)
                db.session.add(specialty)
                db.session.flush()  # get specialty_id before using it below

            db.session.add(HospitalSpecialty(
                hospital_id=c["id"],
                specialty_id=specialty.specialty_id,
            ))

        added += 1

    db.session.commit()
    print(f"Done. Added: {added}, Skipped (already existed): {skipped}")
