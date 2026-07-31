
"""
CareRouteAI — Main Entry Point
Run: python main.py

Architecture:
  Patient → AI Triage → Emergency Rules → RAG KB → Specialty + Confidence
         → Live Queue → Distance Intelligence → AI Wait Prediction
         → LLM Recommendation → Appointment Booking → Notifications
         → Admin Dashboard
"""
import sys
from triage.emergency_rules import check_emergency, format_alert
from triage.symptom_analyzer import SymptomAnalyzer
from clinics.recommend import rank_clinics
from clinics.clinic_manager import (
    add_patient, remove_patient, mark_complete,
    all_queues_summary, get_queue
)
from utils.helpers import geocode_address, notify_appointment, timestamp


BANNER = """
╔══════════════════════════════════════════════════════╗
║          CareRoute AI  —  Smart Healthcare Router    ║
║   AI Triage · Emergency Rules · Live Queues · Maps   ║
╚══════════════════════════════════════════════════════╝
"""

def patient_flow():

    print(BANNER)
    name  = input("Your name: ").strip() or "Patient"
    phone = input("Phone number: ").strip()
    city  = input("Your location / address (Enter for Thane): ").strip() or "Thane, India"

    geocoded = geocode_address(city)
    if geocoded:
        lat, lng = geocoded
        print(f"\n📍 Location resolved → {lat:.4f}, {lng:.4f}\n")
    else:
        lat, lng = 19.2183, 72.9780  # Safe fallback for Thane center
        print(f"\n⚠️  Location could not be verified. Using Thane center as default.\n")

    analyzer = SymptomAnalyzer()
    print(analyzer.greet(name))
    print()

    shown_rag_match = False
    while True:
        user_input = input("You: ").strip()
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "bye"}:
            print("Take care. Goodbye!")
            break

        # 1️⃣  Emergency rules check
        alert = check_emergency(user_input)
        if alert:
            print(format_alert(alert))
            if phone:
                from utils.helpers import notify_emergency
                notify_emergency(phone, alert.condition, alert.severity)
            if alert.severity != "CRISIS":
                # Physical emergencies: the right next step is calling for
                # help, not continuing this chat — end the session.
                break
            # Crisis disclosures: don't abruptly cut the person off right
            # after they've opened up. Resources are shown above; let them
            # keep talking if they want to.
            continue

        # 2️⃣  AI triage conversation (RAG-grounded)
        result = analyzer.analyze(user_input)
        print(f"\nCareRoute AI: {result['response']}\n")

        rag_matches = result.get("rag_matches") or []
        if rag_matches and not result["complete"] and not shown_rag_match:
            top = rag_matches[0]
            print(f"   [internal] closest KB match: {top['condition']} ({top['specialty']})\n")
            shown_rag_match = True

        if result["complete"]:
            triage = result["triage"]
            print("\n🔎  Running AI Recommendation Engine…\n")

            # 3️⃣  LLM Recommendation + Distance Intelligence
            recs = rank_clinics(triage, patient_lat=lat, patient_lng=lng)

            print("🏥  Top Recommended Clinics\n" + "─" * 46)
            from clinics.clinic_manager import _load_clinics
            clinic_map = {c["id"]: c for c in _load_clinics()}

            for i, r in enumerate(recs.get("rankings", [])[:5], 1):
                c = clinic_map.get(r["clinic_id"], {})
                print(f"\n  #{i}  {c.get('name','Unknown')}")
                print(f"      Score   : {r['score']}/100")
                print(f"      Reason  : {r['reason']}")
                print(f"      Wait    : ~{r['wait_prediction_mins']} min")
                print(f"      Address : {c.get('address','')}")
                print(f"      Phone   : {c.get('phone','')}")

            print(f"\n  Routing Logic: {recs.get('routing_reason','')}\n")

            # 4️⃣  Book appointment
            choice = input("Book appointment at clinic #? (1/2/3/4/5 or skip): ").strip()
            if choice in {"1", "2", "3", "4", "5"}:
                idx     = int(choice) - 1
                ranking = recs["rankings"][idx]
                clinic  = clinic_map[ranking["clinic_id"]]
                patient = add_patient(
                    clinic["id"], name, phone,
                    specialty=triage.get("specialty", "generalPractice")
                )
                print(f"\n✅  Booked at {clinic['name']}")
                print(f"   Token #{patient['token']}  ·  Est. wait ~{patient['wait_mins']} min")
                if phone:
                    notify_appointment(name, phone, clinic["name"],
                                       patient["token"], patient["wait_mins"])
                    print("   📱  WhatsApp notification sent.")
            break


def admin_flow():
    print(BANNER + "\n[ ADMIN / RECEPTIONIST DASHBOARD ]\n")
    while True:
        print("\n─ Queue Summary ─────────────────────────────")
        for s in all_queues_summary():
            print(f"  {s['clinic_name']:<35} {s['queue_length']} patients  "
                  f"~{s['total_wait_min']} min")

        print("\nOptions: (a)dd  (r)emove  (c)omplete  (q)uit")
        cmd = input("> ").strip().lower()

        if cmd == "q":
            break
        elif cmd == "a":
            clinic_id = input("Clinic ID: ").strip()
            name      = input("Patient name: ").strip()
            phone     = input("Phone: ").strip()
            p = add_patient(clinic_id, name, phone)
            print(f"Added {p['name']} — Token #{p['token']}, ~{p['wait_mins']} min wait")
        elif cmd == "r":
            clinic_id  = input("Clinic ID: ").strip()
            patient_id = input("Patient ID: ").strip()
            ok = remove_patient(clinic_id, patient_id)
            print("Removed." if ok else "Not found.")
        elif cmd == "c":
            clinic_id  = input("Clinic ID: ").strip()
            patient_id = input("Patient ID: ").strip()
            ok = mark_complete(clinic_id, patient_id)
            print("Consultation marked complete. Queue advanced." if ok else "Not found.")
        else:
            print("Unknown command.")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "patient"
    if mode == "admin":
        admin_flow()
    else:
        patient_flow()
