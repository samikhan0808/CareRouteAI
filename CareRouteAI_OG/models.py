"""
CareRouteAI — Database Models (SQLAlchemy)
Based on: CareRouteAI Database Design & Data Storage Documentation
20 core tables covering auth, patients, AI assessment, predictions,
emergency detection, RAG, hospitals, specialties, recommendations,
doctors, appointments, queue, reports, notifications, translations.
"""
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()


# ── 1. AUTH & USERS ──────────────────────────────────────────────
class User(db.Model):
    __tablename__ = "users"
    user_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    email = db.Column(db.String(120), unique=True)
    phone = db.Column(db.String(20))
    password_hash = db.Column(db.String(255))
    role = db.Column(db.String(20))  # patient, doctor, receptionist, admin
    language = db.Column(db.String(10), default="en")
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── 2. PATIENTS ───────────────────────────────────────────────────
class Patient(db.Model):
    __tablename__ = "patients"
    patient_id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"))
    age = db.Column(db.Integer)
    gender = db.Column(db.String(20))
    blood_group = db.Column(db.String(10))
    address = db.Column(db.String(300))
    emergency_contact = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── 3-4. AI SYMPTOM ASSESSMENT ───────────────────────────────────
class SymptomSession(db.Model):
    __tablename__ = "symptom_sessions"
    session_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    patient_name = db.Column(db.String(100))
    patient_phone = db.Column(db.String(20))
    symptoms = db.Column(db.Text)
    
    
    duration = db.Column(db.String(50))
    language = db.Column(db.String(10))
    status = db.Column(db.String(20), default="in_progress")  # in_progress, completed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)


class SymptomAnswer(db.Model):
    __tablename__ = "symptom_answers"
    answer_id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("symptom_sessions.session_id"))
    question = db.Column(db.Text)
    answer = db.Column(db.Text)
    question_order = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── 5-6. PREDICTIONS ──────────────────────────────────────────────
class AIPrediction(db.Model):
    __tablename__ = "ai_predictions"
    prediction_id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey("symptom_sessions.session_id"))
    predicted_condition = db.Column(db.String(150))
    confidence_score = db.Column(db.Float)
    severity = db.Column(db.String(20))
    recommendation = db.Column(db.Text)
    model_version = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PredictionScore(db.Model):
    __tablename__ = "prediction_scores"
    score_id = db.Column(db.Integer, primary_key=True)
    prediction_id = db.Column(db.Integer, db.ForeignKey("ai_predictions.prediction_id"))
    condition = db.Column(db.String(150))
    probability_score = db.Column(db.Float)
    rank = db.Column(db.Integer)


# ── 7. EMERGENCY ──────────────────────────────────────────────────
class EmergencyAlert(db.Model):
    __tablename__ = "emergency_alerts"
    alert_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    session_id = db.Column(db.Integer, db.ForeignKey("symptom_sessions.session_id"))
    emergency_type = db.Column(db.String(100))
    urgency_level = db.Column(db.String(20))
    trigger_reason = db.Column(db.String(200))
    action_taken = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── 8-9. MEDICAL RAG ──────────────────────────────────────────────
class RagDocument(db.Model):
    __tablename__ = "rag_documents"
    document_id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200))
    source = db.Column(db.String(200))
    category = db.Column(db.String(100))
    file_path = db.Column(db.String(300))
    version = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MedicalKnowledge(db.Model):
    __tablename__ = "medical_knowledge"
    knowledge_id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey("rag_documents.document_id"))
    condition = db.Column(db.String(150))
    symptoms = db.Column(db.Text)
    causes = db.Column(db.Text)
    precautions = db.Column(db.Text)
    general_information = db.Column(db.Text)
    emergency_signs = db.Column(db.Text)


# ── 10-14. HOSPITALS, LOCATION, SPECIALTY, RECOMMENDATION ────────
class Hospital(db.Model):
    __tablename__ = "hospitals"
    hospital_id = db.Column(db.String(50), primary_key=True)  # matches clinics.json "id"
    name = db.Column(db.String(150))
    address = db.Column(db.String(300))
    phone = db.Column(db.String(20))
    rating = db.Column(db.Float)
    emergency_available = db.Column(db.Boolean, default=False)
    status = db.Column(db.String(20), default="active")
    open_hours = db.Column(db.String(50))
    avg_consult_mins_json = db.Column(db.Text)


class HospitalLocation(db.Model):
    __tablename__ = "hospital_locations"
    location_id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.String(50), db.ForeignKey("hospitals.hospital_id"))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)


class Specialty(db.Model):
    __tablename__ = "specialties"
    specialty_id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True)


class HospitalSpecialty(db.Model):
    __tablename__ = "hospital_specialties"
    id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.String(50), db.ForeignKey("hospitals.hospital_id"))
    specialty_id = db.Column(db.Integer, db.ForeignKey("specialties.specialty_id"))


class HospitalRecommendation(db.Model):
    __tablename__ = "hospital_recommendations"
    recommendation_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    session_id = db.Column(db.Integer, db.ForeignKey("symptom_sessions.session_id"))
    hospital_id = db.Column(db.String(50), db.ForeignKey("hospitals.hospital_id"))
    distance_km = db.Column(db.Float)
    specialty_match = db.Column(db.Boolean)
    wait_time = db.Column(db.Integer)
    rating = db.Column(db.Float)
    recommendation_score = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ── 15-17. DOCTORS, APPOINTMENTS, QUEUE ───────────────────────────
class Doctor(db.Model):
    __tablename__ = "doctors"
    doctor_id = db.Column(db.Integer, primary_key=True)
    hospital_id = db.Column(db.String(50), db.ForeignKey("hospitals.hospital_id"))
    user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"))
    name = db.Column(db.String(100))
    specialty_id = db.Column(db.Integer, db.ForeignKey("specialties.specialty_id"))
    availability = db.Column(db.String(100))
    consultation_fee = db.Column(db.Float)
    status = db.Column(db.String(20), default="active")


class Appointment(db.Model):
    __tablename__ = "appointments"
    appointment_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    hospital_id = db.Column(db.String(50), db.ForeignKey("hospitals.hospital_id"))
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.doctor_id"))
    session_id = db.Column(db.Integer, db.ForeignKey("symptom_sessions.session_id"))
    appointment_date = db.Column(db.Date)
    appointment_time = db.Column(db.Time)
    status = db.Column(db.String(20), default="pending")
    reason = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    patient_name = db.Column(db.String(100))
    phone = db.Column(db.String(20))
    gender = db.Column(db.String(20))
    urgency = db.Column(db.String(20))
    primary_concern = db.Column(db.String(200))
    key_symptoms_json = db.Column(db.Text)
    duration = db.Column(db.String(50))
    doctor_summary = db.Column(db.Text)
    conversation_log_json = db.Column(db.Text)


class QueueToken(db.Model):
    __tablename__ = "queue_tokens"
    token_id = db.Column(db.Integer, primary_key=True)
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.appointment_id"))
    token_number = db.Column(db.String(20))
    queue_status = db.Column(db.String(20), default="waiting")  # waiting, called, completed
    estimated_wait_minutes = db.Column(db.Integer)
    issued_at = db.Column(db.DateTime, default=datetime.utcnow)
    called_at = db.Column(db.DateTime)
    completed_at = db.Column(db.DateTime)
    public_id = db.Column(db.String(20), unique=True)


# ── 18-19. REPORTS & NOTIFICATIONS ────────────────────────────────
class Report(db.Model):
    __tablename__ = "reports"
    report_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    session_id = db.Column(db.Integer, db.ForeignKey("symptom_sessions.session_id"))
    report_type = db.Column(db.String(50))
    file_path = db.Column(db.String(300))
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)


class Notification(db.Model):
    __tablename__ = "notifications"
    notification_id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.patient_id"))
    appointment_id = db.Column(db.Integer, db.ForeignKey("appointments.appointment_id"))
    type = db.Column(db.String(20))  # whatsapp, sms
    message = db.Column(db.Text)
    provider = db.Column(db.String(50))
    status = db.Column(db.String(20))
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    error_message = db.Column(db.Text)


# ── 20. TRANSLATIONS ──────────────────────────────────────────────
class Translation(db.Model):
    __tablename__ = "translations"
    translation_id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(150))
    language = db.Column(db.String(10))
    translated_text = db.Column(db.Text)
