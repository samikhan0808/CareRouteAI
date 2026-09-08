"""
db_history.py — CareRouteAI
Persists each triage conversation to the database: symptom_sessions,
symptom_answers, ai_predictions, prediction_scores, emergency_alerts.

Called from web/app.py at the points where each of those events happens
during a triage conversation. No login/auth system exists yet, so
patient_id is left NULL everywhere — a session is identified by its own
session_id, which web/app.py stores in the Flask session
(session["db_session_id"]).

Every function swallows and logs its own errors instead of raising, so a
DB hiccup never breaks the actual triage flow for the patient.
"""
from datetime import datetime
from collections import Counter

from models import db, SymptomSession, SymptomAnswer, AIPrediction, PredictionScore, EmergencyAlert
from utils.logger import logger


def start_session(name: str, phone: str, language: str) -> int | None:
    """Create a symptom_sessions row for a new triage conversation.
    Returns the new session_id, or None if the write failed."""
    try:
        row = SymptomSession(
            patient_name=name,
            patient_phone=phone,
            language=language,
            status="in_progress",
            created_at=datetime.utcnow(),
        )
        db.session.add(row)
        db.session.commit()
        return row.session_id
    except Exception as e:
        logger.warning(f"db_history.start_session failed: {e}")
        db.session.rollback()
        return None


def log_answer(session_id: int | None, question: str, answer: str, order: int) -> None:
    """Record one conversation turn (the question the bot asked, and the
    patient's answer to it)."""
    if not session_id:
        return
    try:
        db.session.add(SymptomAnswer(
            session_id=session_id,
            question=question,
            answer=answer,
            question_order=order,
        ))
        db.session.commit()
    except Exception as e:
        logger.warning(f"db_history.log_answer failed: {e}")
        db.session.rollback()


def complete_session(session_id: int | None, symptoms_text: str, duration: str,
                      triage: dict, predictions: list) -> None:
    """Mark the session complete and store the final AI prediction plus its
    top-3 candidate conditions."""
    if not session_id:
        return
    try:
        session_row = SymptomSession.query.get(session_id)
        if session_row:
            session_row.symptoms = symptoms_text
            session_row.duration = duration
            session_row.status = "completed"
            session_row.completed_at = datetime.utcnow()

        prediction = AIPrediction(
            session_id=session_id,
            predicted_condition=triage.get("predicted_condition") or triage.get("primaryConcern", ""),
            confidence_score=triage.get("confidence"),
            severity=triage.get("urgency"),
            recommendation=triage.get("summary", ""),
            model_version="gemini",
        )
        db.session.add(prediction)
        db.session.flush()  # assigns prediction.prediction_id before commit

        for rank, p in enumerate((predictions or [])[:3], start=1):
            db.session.add(PredictionScore(
                prediction_id=prediction.prediction_id,
                condition=p.get("condition", ""),
                probability_score=p.get("confidence"),
                rank=rank,
            ))

        db.session.commit()
    except Exception as e:
        logger.warning(f"db_history.complete_session failed: {e}")
        db.session.rollback()


def log_emergency(session_id: int | None, emergency_type: str, urgency_level: str,
                   trigger_reason: str, action_taken: str) -> None:
    """Record an emergency-rules trigger."""
    try:
        db.session.add(EmergencyAlert(
            session_id=session_id,
            emergency_type=emergency_type,
            urgency_level=urgency_level,
            trigger_reason=trigger_reason,
            action_taken=action_taken,
        ))
        db.session.commit()
    except Exception as e:
        logger.warning(f"db_history.log_emergency failed: {e}")
        db.session.rollback()

def get_health_report_data(phone: str) -> dict:
    """Fetch all past triage sessions + predictions for a phone number.
    Used to build the multi-visit AI Health Report Card PDF."""
    if not phone:
        return {"total_visits": 0, "visits": [], "condition_frequency": {}, "recurring": []}

    sessions = (
        SymptomSession.query
        .filter_by(patient_phone=phone)
        .order_by(SymptomSession.created_at.desc())
        .all()
    )

    visits = []
    condition_counter = Counter()

    for s in sessions:
        prediction = (
            AIPrediction.query
            .filter_by(session_id=s.session_id)
            .order_by(AIPrediction.created_at.desc())
            .first()
        )
        condition  = prediction.predicted_condition if prediction else "Not completed"
        confidence = int((prediction.confidence_score or 0) * 100) if prediction else 0
        severity   = prediction.severity if prediction else "N/A"

        if prediction and condition:
            condition_counter[condition] += 1

        visits.append({
            "date":       s.created_at.strftime("%d %b %Y") if s.created_at else "N/A",
            "symptoms":   (s.symptoms or "Not specified")[:60],
            "condition":  condition,
            "confidence": confidence,
            "severity":   severity,
            "status":     s.status,
        })

    recurring = [cond for cond, count in condition_counter.items() if count >= 2]

    return {
        "total_visits":        len(sessions),
        "first_visit":         sessions[-1].created_at.strftime("%d %b %Y") if sessions else "N/A",
        "last_visit":          sessions[0].created_at.strftime("%d %b %Y") if sessions else "N/A",
        "visits":              visits,
        "condition_frequency": dict(condition_counter),
        "recurring":           recurring,
    }
