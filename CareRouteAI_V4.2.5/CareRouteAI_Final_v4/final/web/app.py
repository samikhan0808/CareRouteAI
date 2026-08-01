"""
CareRouteAI — Flask Web Application
Serves patient triage UI + admin dashboard over HTTP.

Run: python web/app.py
"""
import os
import sys
import json
import time
from functools import wraps
from flask import (Flask, Response, render_template, request, jsonify,
                   session, redirect, url_for, flash, stream_with_context)

# Add project root to path so `triage.`, `clinics.`, `rag.`, `utils.` imports work
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import FLASK_SECRET, ADMIN_PASSWORD, DEFAULT_LANGUAGE, ANALYTICS_FILE
from triage.emergency_rules import check_emergency
from triage.symptom_analyzer import SymptomAnalyzer
from clinics.recommend import rank_clinics, predict_wait
from utils.nearby_hospitals import get_nearby_hospitals
from clinics.clinic_manager import (
    add_patient, remove_patient, mark_complete, call_next,
    all_queues_summary, get_queue, _load_clinics
)
from utils.helpers import (
    geocode_address,
    notify_appointment,
    notify_emergency,
    clinic_location_warning,
)
from utils.helpers import format_wait, haversine_km
from utils.pdf_report import generate_triage_pdf
from utils.analytics import (
    record_triage_event,
    record_booking_event,
    load_analytics_summary,
)
from utils.validators import validate_phone
from utils.logger import logger
from utils.validators import validate_phone


app = Flask(__name__)
app.secret_key = FLASK_SECRET

# In-memory triage sessions keyed by session id (per browser tab).
# Capped at 500 entries to prevent unbounded memory growth on long-running servers.
_analyzers: dict[str, SymptomAnalyzer] = {}
_MAX_ANALYZERS = 500

def _cleanup_analyzers():
    """Evict oldest entries when the dict exceeds the cap."""
    if len(_analyzers) > _MAX_ANALYZERS:
        # Remove the oldest 20% of entries
        evict = list(_analyzers.keys())[:int(_MAX_ANALYZERS * 0.2)]
        for k in evict:
            _analyzers.pop(k, None)


# ── Auth helper ──────────────────────────────────────────
def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated


# ══════════════════════════════════════════════════════════
# PATIENT ROUTES
# ══════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/triage")
def triage_page():
    return render_template("triage.html")


@app.route("/results")
def results_page():
    return render_template("results.html")


@app.route("/api/triage/start", methods=["POST"])
def triage_start():
    data = request.get_json(force=True)
    name = (data.get("name") or "Patient").strip()
    sid  = os.urandom(8).hex()
    gender = (data.get("gender") or "unspecified").strip()
    language = data.get("language", DEFAULT_LANGUAGE)
    session["patient_name"]     = name
    session["patient_gender"]   = gender
    session["patient_phone"]    = data.get("phone", "")
    session["patient_city"]     = data.get("city", "Thane")
    session["patient_language"] = language
    session["patient_lat"]      = data.get("patient_lat")
    session["patient_lng"]      = data.get("patient_lng")
    session["triage_sid"]       = sid

    analyzer = SymptomAnalyzer(language=language)
    _cleanup_analyzers()
    _analyzers[sid] = analyzer

    greeting = analyzer.greet(name)
    return jsonify({"message": greeting, "sid": sid})


@app.route("/api/triage/message", methods=["POST"])
def triage_message():
    data = request.get_json(force=True)
    phone = data.get("phone", "")

    sid  = session.get("triage_sid") or data.get("sid", "")
    text = (data.get("message") or "").strip()

    if not text:
        return jsonify({"error": "Empty message"}), 400

    # 1️⃣ Emergency rules check — runs before anything else
    alert = check_emergency(text)
    if alert:
        phone = session.get("patient_phone", "")
        if phone:
            notify_emergency(phone, alert.condition, alert.severity)
        return jsonify({
            "type":           "emergency",
            "condition":      alert.condition,
            "severity":       alert.severity,
            "message":        alert.message,
            "action":         alert.action,
            "call_ambulance": alert.call_ambulance,
        })

    # 2️⃣ Get or recreate analyzer for this session
    analyzer = _analyzers.get(sid)
    if not analyzer:
        analyzer = SymptomAnalyzer(language=session.get("patient_language", DEFAULT_LANGUAGE))
        _analyzers[sid] = analyzer

    result = analyzer.analyze(text)

    if result["complete"]:
        triage = result["triage"] or {}
        session["triage_result"] = triage

        # Prefer explicit browser coords, then stored session coords, then city geocode, then Thane centroid
        lat, lng = None, None
        req_lat = request.json.get("patient_lat")
        req_lng = request.json.get("patient_lng")
        if req_lat is not None and req_lng is not None:
            try:
                lat, lng = float(req_lat), float(req_lng)
            except (ValueError, TypeError):
                lat, lng = None, None
        if not (lat and lng):
            stored_lat = session.get("patient_lat")
            stored_lng = session.get("patient_lng")
            if stored_lat is not None and stored_lng is not None:
                try:
                    lat, lng = float(stored_lat), float(stored_lng)
                except (ValueError, TypeError):
                    lat, lng = None, None
        if not (lat and lng):
            city = session.get("patient_city", "Thane")
            geocoded = geocode_address(city)
            lat, lng = geocoded if geocoded else (19.2350, 72.9600)
        session["patient_lat"] = lat
        session["patient_lng"] = lng
        # ── Live nearby hospital search (OpenStreetMap, no API key) ───────
        # GPS -> OpenStreetMap -> nearby hospitals -> for each one, check
        # whether it's actually one of our integrated clinics (matched by
        # proximity to a clinics.json entry). Integrated ones get merged
        # with their real queue data so booking works normally; the rest
        # stay as informational-only (map/call/directions/walk-in).
        ranked          = []
        routing_reason  = ""
        has_gps         = bool(req_lat and req_lng)   # True = real device GPS
        INTEGRATION_MATCH_KM = 0.15   # ~150m = same building

        if has_gps:
            nearby = get_nearby_hospitals(
                patient_lat=lat,
                patient_lng=lng,
                specialty=triage.get("specialty", "generalPractice"),
                urgency=triage.get("urgency", "NON-URGENT"),
                max_results=15,
                radius_m=5000,
            )
            curated_clinics = _load_clinics()

            for h in nearby:
                match = next(
                    (c for c in curated_clinics
                     if haversine_km(h["lat"], h["lng"], c["lat"], c["lng"]) <= INTEGRATION_MATCH_KM),
                    None,
                )
                if match:
                    # Integrated: use OUR clinic id + live queue data so
                    # booking/queue/token flows work exactly as before.
                    q = get_queue(match["id"])
                    wait_mins = predict_wait(match["id"], triage.get("specialty", "generalPractice"))
                    ranked.append({
                        **match,
                        "distance_km":         h["distance_km"],
                        "score":               h.get("score", 0),
                        "reason":              h.get("reason", ""),
                        "wait_prediction":     int(wait_mins or 0),
                        "wait_prediction_fmt": format_wait(int(wait_mins or 0)),
                        "queue_length":        len(q.get("patients", [])),
                        "is_integrated":       True,
                    })
                else:
                    # Not integrated: informational only, no live queue exists for it.
                    h["wait_prediction"]     = 0
                    h["wait_prediction_fmt"] = "Walk-in"
                    h["is_integrated"]       = False
                    ranked.append(h)

            n_integrated = sum(1 for r in ranked if r.get("is_integrated"))
            routing_reason = (
                f"Showing {len(ranked)} real hospitals near your location "
                f"({n_integrated} with live queue tracking)."
                if ranked else
                "No hospitals found nearby. Showing from our registered list."
            )

        # Fallback: use registered clinic list when no GPS or OSM returned nothing
        if not ranked:
            recs = rank_clinics(triage, patient_lat=lat, patient_lng=lng)
            if not isinstance(recs, dict):
                recs = {"rankings": [], "routing_reason": "No recommendations available."}
            routing_reason = recs.get("routing_reason", "")
            clinics_map = {c["id"]: c for c in _load_clinics()}
            for r in recs.get("rankings", []):
                c = clinics_map.get(r["clinic_id"], {})
                if not c:
                    continue
                q = get_queue(r["clinic_id"])
                try:
                    dist = round(haversine_km(lat, lng, c.get("lat"), c.get("lng")), 2)
                except Exception:
                    dist = None
                wait_mins = r.get("wait_prediction_mins") or c.get("est_wait_mins", 0)
                ranked.append({
                    **c,
                    "score":               r.get("score", 0),
                    "reason":              r.get("reason", ""),
                    "wait_prediction":     int(wait_mins or 0),
                    "wait_prediction_fmt": format_wait(int(wait_mins or 0)),
                    "distance_km":         dist,
                    "queue_length":        len(q.get("patients", [])),
                    "is_integrated":       True,   # everything in the curated fallback IS integrated

                })

        location_warnings = []
        for c in _load_clinics():
            warning = clinic_location_warning(c)
            if warning:
                location_warnings.append(warning)

        rag_matches = result.get("rag_matches", [])
        rag_context = "\n".join(
            f"{m['condition']} ({m['specialty']})" for m in rag_matches
        )

        language = session.get("patient_language", DEFAULT_LANGUAGE)
        record_triage_event(
            triage.get("specialty", "generalPractice"),
            triage.get("urgency", "NON-URGENT"),
            language,
            result.get("predictions", []),
        )
        return jsonify({
            "type":              "complete",
            "response":          result["response"],
            "triage":            triage,
            "rag_context":       rag_context,
            "clinics":           ranked,
            "location_warnings": location_warnings,
            "review_url":        url_for("review_page"),
            "routing_reason":    routing_reason,
        })

    return jsonify({"type": "message", "response": result["response"]})


@app.route("/api/book", methods=["POST"])
def book_appointment():
    data      = request.get_json(force=True)
    clinic_id = data.get("clinic_id")

    # ── OSM / External hospital — not in our system ──────────────────────
    # If the clinic_id starts with "osm_" it came from the live OpenStreetMap
    # search and is not in our registered list. Return all available contact
    # info so the frontend can show Call / Directions / Website / Walk-in.
    is_osm = str(clinic_id or "").startswith("osm_")
    if is_osm:
        osm_data = data.get("clinic_data", {})   # frontend sends full clinic object
        return jsonify({
            "success":      False,
            "external":     True,                 # key flag — frontend checks this
            "clinic":       osm_data.get("name", "This Hospital"),
            "address":      osm_data.get("address", ""),
            "phone":        osm_data.get("phone", ""),
            "website":      osm_data.get("website", ""),
            "lat":          osm_data.get("lat"),
            "lng":          osm_data.get("lng"),
            "distance_km":  osm_data.get("distance_km"),
            "message":      (
                "This hospital is not yet registered in our system. "
                "You can call ahead, get directions, or walk in directly."
            ),
        }), 200   # 200 not 400 — this is handled, not an error

    # ── Registered clinic — normal flow ──────────────────────────────────
    registered = {c["id"] for c in _load_clinics()}
    if clinic_id not in registered:
        return jsonify({"success": False, "error": "Invalid clinic"}), 400
    # ── Registered clinic — normal flow ──────────────────────────────────
    registered = {c["id"] for c in _load_clinics()}
    if clinic_id not in registered:
        return jsonify({"success": False, "error": "Invalid clinic"}), 400
    name      = session.get("patient_name") or data.get("name", "Patient")
    phone     = session.get("patient_phone") or data.get("phone", "")
    if phone and not validate_phone(phone):
        return jsonify({"error": "Invalid phone number"}), 400

    gender    = session.get("patient_gender") or data.get("gender") or "unspecified"
    gender    = session.get("patient_gender") or data.get("gender") or "unspecified"
    triage    = session.get("triage_result") or {}
    specialty = triage.get("specialty", "generalPractice")
    urgency = triage.get("urgency", "NON-URGENT")
    primary_concern = triage.get("primaryConcern", "Unknown")
    key_symptoms = triage.get("keySymptoms", []) or []
    duration = triage.get("duration", "unknown")
    doctor_summary = triage.get("summary") or (
        f"Gender: {gender}. "
        f"Urgency: {urgency}. "
        f"Specialty: {specialty}. "
        f"Primary concern: {primary_concern}. "
        f"Symptoms: {', '.join(key_symptoms) or 'None'}. "
        f"Duration: {duration}.")

    # Pull the full conversation the patient had with the AI.
    # _analyzers is keyed by triage_sid which was set when triage started.
    sid      = session.get("triage_sid", "")
    analyzer = _analyzers.get(sid)
    conversation_log = []
    if analyzer:
        for msg in analyzer.history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Strip the internal TRIAGE_JSON block — doctors don't need raw JSON
            if "<TRIAGE_JSON>" in content:
                content = content[:content.index("<TRIAGE_JSON>")].strip()
            if role and content:
                conversation_log.append({
                    "role":    "Patient" if role == "user" else "AI",
                    "content": content,
                })

    try:
        patient     = add_patient(
            clinic_id, name, phone,
            gender=gender,
            specialty=specialty,
            doctor_summary=doctor_summary,
            urgency=urgency,
            primary_concern=primary_concern,
            key_symptoms=key_symptoms,
            duration=duration,
            conversation_log=conversation_log
        )
        language = session.get("patient_language", DEFAULT_LANGUAGE)
        clinics_map = {c["id"]: c for c in _load_clinics()}
        clinic      = clinics_map.get(clinic_id, {})
        queue_url    = request.host_url.rstrip('/') + url_for("queue_page", clinic_id=clinic_id)
        booking_link = f"{queue_url}?patient_id={patient['id']}&token={patient['token']}"

        if phone:
            notify_appointment(name, phone, clinic.get("name", ""),
                               patient["token"], patient["wait_mins"],
                               booking_link=booking_link)

        record_booking_event(
            clinic_id,
            clinic.get("name", ""),
            specialty,
            urgency,
            language,
        )

        session["booking_info"] = {
            "clinic_name": clinic.get("name", ""),
            "address": clinic.get("address", ""),
            "phone": clinic.get("phone", ""),
            "token": patient["token"],
            "wait_mins": patient["wait_mins"],
            "queue_url": queue_url,
            "booking_link": booking_link,
        }

        return jsonify({
            "success":      True,
            "token":        patient["token"],
            "wait_mins":    patient["wait_mins"],
            "clinic":       clinic.get("name", ""),
            "address":      clinic.get("address", ""),
            "phone":        clinic.get("phone", ""),
            "patient_id":   patient["id"],
            "clinic_id":    clinic_id,
            "queue_url":    queue_url,
            "booking_link": booking_link,
            "report_url":   url_for("api_report", _external=True),
        })
    except Exception as e:
        logger.exception(e)
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/report")
def api_report():
    triage = session.get("triage_result")
    if not triage:
        return redirect(url_for("index"))

    patient_info = {
        "name": session.get("patient_name", "Patient"),
        "phone": session.get("patient_phone", ""),
        "gender": session.get("patient_gender", "unspecified"),
        "city": session.get("patient_city", "Thane"),
    }
    booking_info = session.get("booking_info")
    pdf_bytes = generate_triage_pdf(patient_info, triage, booking_info)
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={
            "Content-Disposition": "attachment; filename=CareRouteAI_Triage_Report.pdf"
        },
    )

@app.route("/queue/<clinic_id>")
def queue_page(clinic_id):
    return render_template("queue.html", clinic_id=clinic_id)


@app.route("/review")
def review_page():
    return render_template("review.html")


def _queue_status_payload(clinic_id, patient_id=None, token=None, phone=None):
    clinics = {c["id"]: c for c in _load_clinics()}
    clinic = clinics.get(clinic_id)
    if not clinic:
        return {"error": "Clinic not found"}

    queue = get_queue(clinic_id)
    patients = queue.get("patients", [])
    waiting = [p for p in patients if p.get("status") == "waiting"]
    in_consult = [p for p in patients if p.get("status") == "in_consultation"]

    avg_mins = clinic.get("avg_consult_mins", 15)
    if isinstance(avg_mins, dict):
        avg_mins = avg_mins.get("generalPractice", 15)

    patient = None
    lookup_method = None
    if patient_id:
        patient = next((p for p in patients if p.get("id") == patient_id), None)
        lookup_method = "patient_id"
    elif token:
        try:
            token_int = int(token)
            patient = next((p for p in patients if p.get("token") == token_int), None)
        except ValueError:
            patient = None
        lookup_method = "token"
    elif phone:
        normalized_phone = ''.join(ch for ch in (phone or '') if ch.isdigit())
        if normalized_phone.startswith('91') and len(normalized_phone) > 10:
            normalized_phone = normalized_phone[-10:]
        patient = next(
            (p for p in patients if ''.join(ch for ch in str(p.get('phone', '')) if ch.isdigit()).endswith(normalized_phone)),
            None
        )
        lookup_method = "phone"

    position = None
    people_ahead = None
    status = None
    if patient:
        status = patient.get("status")
        if status == "in_consultation":
            position = 0
            people_ahead = 0
        else:
            ordered = waiting
            idx = next((i for i, p in enumerate(ordered) if p.get("id") == patient.get("id")), None)
            if idx is not None:
                position = idx + 1
                people_ahead = idx

    estimated_personal_wait = 0
    if people_ahead is not None and status == "waiting":
        estimated_personal_wait = people_ahead * avg_mins

    return {
        "clinic_id":             clinic_id,
        "clinic_name":           clinic.get("name", ""),
        "address":               clinic.get("address", ""),
        "phone":                 clinic.get("phone", ""),
        "queue_length":          len(patients),
        "waiting_count":         len(waiting),
        "in_consult":            len(in_consult),
        "avg_wait_mins":         avg_mins,
        "total_wait_mins":       len(waiting) * avg_mins,
        "position":              position,
        "people_ahead":          people_ahead,
        "patient_status":        status,
        "patient_token":         patient.get("token") if patient else None,
        "patient_name":          patient.get("name") if patient else None,
        "patient_gender":        patient.get("gender") if patient else None,
        "patient_urgency":       patient.get("urgency") if patient else None,
        "patient_primary_concern": patient.get("primary_concern") if patient else None,
        "patient_key_symptoms":  patient.get("key_symptoms") if patient else [],
        "estimated_wait_mins":   estimated_personal_wait,
        "token":                 token,
        "lookup_method":         lookup_method,
        "booking_confirmed":     bool(patient),
        "booking_found":         bool(patient),
    }

@app.route("/api/queue/<clinic_id>")
def api_queue_status(clinic_id):
    logger.info(f"Fetching queue status for clinic: {clinic_id}")
    payload = _queue_status_payload(
        clinic_id,
        patient_id=request.args.get("patient_id"),
        token=request.args.get("token"),
        phone=request.args.get("phone"),
    )
    if payload.get("error"):
        return jsonify(payload), 404
    return jsonify(payload)

@app.route("/api/queues")
def api_all_queues():
    summary = all_queues_summary()
    clinics = {c["id"]: c for c in _load_clinics()}
    detail = []
    for s in summary:
        c = clinics.get(s["clinic_id"], {})
        detail.append({
            **s,
            "address": c.get("address", ""),
            "phone": c.get("phone", ""),
            "emergency": c.get("emergency", False),
        })
    return jsonify(detail)

@app.route("/api/queue/stream/<clinic_id>")
def api_queue_stream(clinic_id):
    def event_stream():
        while True:
            payload = _queue_status_payload(
                clinic_id,
                patient_id=request.args.get("patient_id"),
                token=request.args.get("token"),
                phone=request.args.get("phone"),
            )
            yield f"data: {json.dumps(payload)}\n\n"
            time.sleep(5)
    return Response(stream_with_context(event_stream()), mimetype="text/event-stream")

# ══════════════════════════════════════════════════════════
# ADMIN ROUTES
# ══════════════════════════════════════════════════════════

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Incorrect password.")
    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/admin")
@admin_required
def admin_dashboard():
    return render_template("admin.html")


@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    return render_template("analytics.html")


@app.route("/admin/analytics/download")
@admin_required
def admin_analytics_download():
    from pathlib import Path
    path = Path(ANALYTICS_FILE)
    if not path.exists():
        return jsonify({"error": "Analytics file not found."}), 404
    return Response(
        path.read_text(encoding="utf-8"),
        mimetype="application/json",
        headers={"Content-Disposition": "attachment; filename=analytics.json"},
    )


@app.route("/api/admin/analytics")
@admin_required
def api_admin_analytics():
    return jsonify(load_analytics_summary())


@app.route("/api/admin/queues")
@admin_required
def api_queues():
    summary = all_queues_summary()
    clinics = {c["id"]: c for c in _load_clinics()}
    detail  = []
    for s in summary:
        cid = s["clinic_id"]
        q   = get_queue(cid)
        detail.append({
            **s,
            "address":  clinics.get(cid, {}).get("address", ""),
            "phone":    clinics.get(cid, {}).get("phone", ""),
            "patients": q.get("patients", []),
        })
    return jsonify(detail)


@app.route("/api/admin/add_patient", methods=["POST"])
@admin_required
def api_add_patient():
    data = request.get_json(force=True)
    try:
        p = add_patient(
            data["clinic_id"], data["name"],
            data.get("phone", ""), data.get("specialty", "generalPractice")
        )
        return jsonify({"success": True, "patient": p})
    except Exception as e:
        logger.exception(e)
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/admin/remove_patient", methods=["POST"])
@admin_required
def api_remove_patient():
    data = request.get_json(force=True)
    ok   = remove_patient(data["clinic_id"], data["patient_id"])
    logger.info(f"Patient removed from clinic: {data['clinic_id']}, Patient ID: {data['patient_id']}")
    return jsonify({"success": ok})


@app.route("/api/admin/mark_complete", methods=["POST"])
@admin_required
def api_mark_complete():
    data = request.get_json(force=True)
    found = mark_complete(data["clinic_id"], data["patient_id"])
    logger.info(f"Marking patient as complete: {data['clinic_id']}, Patient ID: {data['patient_id']}")
    return jsonify({"success": found})


@app.route("/api/admin/call_next", methods=["POST"])
@admin_required
def api_call_next():
    data = request.get_json(force=True)
    patient = call_next(data["clinic_id"])
    logger.info(f"Calling next patient for clinic: {data['clinic_id']}")
    return jsonify({"success": patient is not None, "patient": patient})


if __name__ == "__main__":
    print("\nCareRouteAI Web Server starting...")
    print("   Patient portal : http://localhost:5000")
    print("   Admin dashboard: http://localhost:5000/admin")
    print(f"   Admin password : {ADMIN_PASSWORD}  (set ADMIN_PASSWORD in .env)\n")
    import os as _os
    debug_mode = _os.getenv("FLASK_DEBUG", "False") == "True"
    app.run(debug=debug_mode, host="0.0.0.0", use_reloader=False, port=5000)
