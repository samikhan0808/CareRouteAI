"""
Recommendation Engine — CareRouteAI
LLM-based clinic ranking with distance intelligence + wait prediction.
"""
import json
import logging
import math
import google.generativeai as genai
from config import GOOGLE_API_KEY, GEMINI_MODEL, MAX_TOKENS_RANK
from clinics.clinic_manager import _load_clinics, _load_queues
from utils.gemini_timeout import request_options


logger = logging.getLogger(__name__)
genai.configure(api_key=GOOGLE_API_KEY)
_rank_model = genai.GenerativeModel(GEMINI_MODEL)


# ── Distance (Haversine formula) ─────────────────────────
def _haversine(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(d_lng / 2) ** 2)
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


# ── Wait prediction ──────────────────────────────────────
def predict_wait(clinic_id: str, specialty: str) -> int:
    queues  = _load_queues()
    clinics = {c["id"]: c for c in _load_clinics()}
    queue   = queues.get(clinic_id, {})
    clinic  = clinics.get(clinic_id, {})

    patients = queue.get("patients", [])
    n_waiting = len([p for p in patients if p.get("status") == "waiting"])
    in_consult = len([p for p in patients if p.get("status") == "in_consultation"])

    avg_mins = clinic.get("avg_consult_mins", 15)
    if isinstance(avg_mins, dict):
        avg_mins = avg_mins.get(specialty, 15)
    try:
        avg_mins = int(avg_mins) if avg_mins else 15
    except Exception as e:
        logger.warning(f"Invalid avg_consult_mins for clinic {clinic_id}: {avg_mins} ({e})")
        avg_mins = 15

    # Estimate wait: waiting patients take a full avg, those in consultation
    # occupy part of the slot (assume half remaining). This yields more
    # realistic non-zero waits when clinic is busy.
    est = n_waiting * avg_mins + int(in_consult * (avg_mins / 2))
    return int(est)


# ── Build candidate payload ──────────────────────────────
def _build_candidate(clinic: dict, triage: dict,
                     patient_lat: float, patient_lng: float) -> dict:
    queues    = _load_queues()
    q         = queues.get(clinic["id"], {})
    patients  = q.get("patients", [])
    specialty = triage.get("specialty", "generalPractice")

    return {
        "id":              clinic["id"],
        "name":            clinic["name"],
        "address":         clinic["address"],
        "lat": clinic["lat"],
        "lng": clinic["lng"],
        "distance_km":     _haversine(patient_lat, patient_lng,clinic["lat"], clinic["lng"]),
        "rating":          clinic["rating"],
        "specialties":     clinic["specialties"],
        "specialty_match": specialty in clinic["specialties"],
        "emergency":       clinic["emergency"],
        "queue_length":    len(patients),
        "in_consultation": sum(1 for p in patients if p.get("status") == "in_consultation"),
        "est_wait_mins":   predict_wait(clinic["id"], specialty),
        "open_hours":      clinic["open"],
        "phone":           clinic["phone"],
    }


# ── LLM ranking (Gemini) ─────────────────────────────────
def rank_clinics(triage: dict,
                 patient_lat: float = 19.2183,
                 patient_lng: float = 72.9781,
                 top_n: int = 5) -> dict:
    """
    Use Gemini to rank clinics for a given triage result.
    Returns { rankings: [...], routing_reason: str }
    """
    clinics    = _load_clinics()
    candidates = [_build_candidate(c, triage, patient_lat, patient_lng)
                  for c in clinics]
    # ── Pre-filter: send only the most relevant candidates to Gemini ──────
    # Sorting priority:
    #   1. Specialty match first (exact match gets priority)
    #   2. Emergency capability if urgency is EMERGENCY/URGENT
    #   3. Distance (closest first)
    # This keeps the Gemini prompt lean and the output within token budget.
    urgency = triage.get("urgency", "NON-URGENT")
    specialty = triage.get("specialty", "generalPractice")

    def _prefilter_score(c: dict) -> tuple:
        spec_match  = 0 if c["specialty_match"] else 1        # 0 = match (sort first)
        emerg_ok    = 0 if (c["emergency"] and urgency in {"EMERGENCY", "URGENT"}) else 1
        distance    = c["distance_km"] or 999
        return (spec_match, emerg_ok, distance)

    candidates = sorted(candidates, key=_prefilter_score)[:15]

    prompt = f"""You are a medical routing AI ranking clinics for a patient.

PATIENT TRIAGE:
  Primary Concern : {triage.get('primaryConcern', 'Unknown')}
  Specialty Needed: {triage.get('specialty', 'generalPractice')}
  Urgency         : {triage.get('urgency', 'NON-URGENT')}
  Confidence      : {int(triage.get('confidence', 0.8) * 100)}%
  Key Symptoms    : {', '.join(triage.get('keySymptoms', []))}

CLINIC CANDIDATES:
{json.dumps(candidates, indent=2)}

RANKING CRITERIA (weight in order):
1. Specialty match with patient's need
2. Urgency — for EMERGENCY/URGENT, emergency capability & short wait are critical
3. Distance from patient
4. Current queue wait time
5. Rating
Return ONLY valid JSON — no markdown fences, no other text:
{{
  "rankings": [
    {{
      "clinic_id": "c1",
      "score": 95,
      "reason": "Only 1.2 km away, cardiac specialist available, emergency capable, 15-min wait",
      "wait_prediction_mins": 15
    }}
  ],
  "routing_reason": "One-line summary of why top clinic was chosen"
}}"""

    try:
        response = _rank_model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=MAX_TOKENS_RANK,
                temperature=0.3,
            ),
            request_options=request_options(),
        )
        raw = response.text.strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        result = json.loads(raw)
        valid_ids = {c["id"] for c in candidates}
        rankings = [r for r in result.get("rankings", []) if r.get("clinic_id") in valid_ids]
        if rankings:
            result["rankings"] = rankings
            return result
        logger.warning("Gemini returned no valid rankings; using fallback ranking.")
    except Exception as e:
        logger.warning(f"Error occurred while ranking clinics: {e}")

    # ── Fallback: score by specialty match + composite clinic quality ──
    specialty = triage.get("specialty", "generalPractice")
    urgency = triage.get("urgency", "NON-URGENT")

    def score_clinic(c: dict) -> int:
        score = 30

        # Specialty match — most important signal
        if c["specialty_match"]:
            score += 35
        else:
            score -= 10   # penalise mismatch but don't disqualify

        # Emergency capability
        if urgency == "EMERGENCY":
            score += 20 if c["emergency"] else -25
        elif urgency == "URGENT":
            score += 10 if c["emergency"] else -5

        # Distance — graduated penalty up to 25km
        dist = float(c["distance_km"] or 0)
        if dist <= 3:
            score += 20
        elif dist <= 7:
            score += 14
        elif dist <= 15:
            score += 7
        elif dist <= 25:
            score += 2
        else:
            score -= 5

        # Wait time — graduated penalty
        wait = int(c["est_wait_mins"] or 0)
        if wait == 0:
            score += 10
        elif wait <= 15:
            score += 8
        elif wait <= 30:
            score += 4
        elif wait <= 60:
            score += 0
        else:
            score -= 5

        # Rating (0–5 scale → 0–10 points)
        score += min(10, int((c.get("rating") or 0) * 2))

        return max(0, min(100, score))

    scored = sorted(candidates, key=lambda c: (-score_clinic(c), c["distance_km"]))[:top_n]
    return {
        "rankings": [
            {
                "clinic_id": c["id"],
                "score": score_clinic(c),
                "lat": c["lat"],
                "lng": c["lng"],
                "reason":               (
                    f"{'Specialty match. ' if c['specialty_match'] else ''}"
                    f"{c['distance_km']} km away, ~{c['est_wait_mins']} min wait, "
                    f"rating {c['rating']}"
                    f"{', emergency capable' if c['emergency'] else ''}"
                    + (f", predicted condition: {triage.get('predicted_condition')}" if triage.get('predicted_condition') else "")
                ).strip(', '),
                "wait_prediction_mins": c["est_wait_mins"],
            }
            for c in scored
        ],
        "routing_reason": "Fallback ranking uses specialty, distance, wait time, emergency capability, and rating.",
    }