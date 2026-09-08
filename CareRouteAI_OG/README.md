# CareRoute AI 🏥

### Smart Healthcare Routing for India — AI Triage · Emergency Detection · Live Queue Management

CareRoute AI is an intelligent patient triage and clinic routing system built for the Indian healthcare context. Instead of a patient guessing which clinic to visit, CareRoute AI conducts a conversational symptom assessment, determines the right medical specialty and urgency level, and routes the patient to the nearest available clinic — with a live queue and an AI-generated doctor brief waiting when they arrive.

\---

## The Problem

Patients in India — particularly in urban areas like Thane/Mumbai — routinely visit the wrong clinic for their condition, wait unnecessarily long, or fail to recognise the urgency of their symptoms. This leads to delayed care for serious conditions and overcrowding at general practitioners for cases that needed a specialist.

\---

## What CareRoute AI Does

1. **Talks to the patient** — a warm, conversational AI triage chat collects symptoms naturally, asking one targeted question at a time
2. **Detects emergencies first** — before the AI processes anything, a rule-based engine checks for life-threatening conditions (heart attack, stroke, respiratory distress, poisoning, suicide risk) and immediately provides emergency numbers and instructions
3. **Grounds responses in a medical knowledge base** — a 154-condition RAG (Retrieval-Augmented Generation) knowledge base assembled from WHO/CDC-style source material plus the original curated entries anchors the AI's specialty and urgency judgment
4. **Routes to the right clinic** — ranks nearby clinics by specialty match, distance, current queue wait time, and rating
5. **Books and queues the patient** — issues a token, predicts wait time, and provides a live queue status page
6. **Prepares a doctor brief** — the admin dashboard shows the full AI triage conversation and structured findings to the doctor before the patient enters the room

\---

## Architecture

```
Patient Message
      │
      ▼
┌─────────────────────────────┐
│   Emergency Rules Engine    │  ← Rule-based, deterministic
│   (emergency\_rules.py)      │    Checks for 8 critical patterns
│                             │    before any LLM involvement
└──────────────┬──────────────┘
               │ No emergency
               ▼
┌─────────────────────────────┐
│   RAG Knowledge Base        │  ← 56-condition medical KB
│   (knowledge\_base.py)       │    Embedding-based retrieval
│   medical\_kb.json           │    Keyword fallback if API unavailable
└──────────────┬──────────────┘
               │ Top-3 relevant conditions
               ▼
┌─────────────────────────────┐
│   Gemini Triage (LLM)       │  ← RAG-grounded conversational triage
│   (symptom\_analyzer.py)     │    3–5 conversational turns
│                             │    Confidence-aware re-prompting
│                             │    Outputs: specialty, urgency (0–1),
│                             │    confidence, key symptoms, duration
└──────────────┬──────────────┘
               │ Triage result
               ▼
┌─────────────────────────────┐
│   Clinic Ranking Engine     │  ← Haversine distance scoring
│   (recommend.py)            │    Live queue wait prediction
│                             │    Specialty match + rating weighting
│                             │    LLM-generated reasoning per clinic
└──────────────┬──────────────┘
               │ Ranked recommendations
               ▼
┌─────────────────────────────┐
│   Queue + Booking           │  ← Token assignment
│   (clinic\_manager.py)       │    Live SSE queue updates
│                             │    Admin dashboard with doctor brief
└─────────────────────────────┘
```

\---

## Key Design Decisions

### Why rule-based emergency detection instead of asking the LLM?

LLMs are probabilistic — they can hallucinate, hedge, or miss edge cases. A "call an ambulance now" decision cannot depend on a model that might, on a bad day, output something different. The emergency rules engine uses deterministic pattern matching: if a patient describes chest pain with left arm numbness, the system *always* triggers a cardiac emergency alert and displays `108` — no model involved, no probability, no exception.

### Why RAG instead of a fine-tuned model or plain prompting?

Fine-tuning requires labelled medical data we don't have. Plain prompting gives generic global responses. RAG lets us inject India-specific, curated medical knowledge (dengue, malaria, typhoid, Tele-MANAS helpline) directly into each triage prompt, grounding the model's output in our specific domain without retraining.

### Why confidence-based re-prompting?

When a patient gives vague input ("I don't feel well"), Gemini may return a triage result with low confidence (< 0.6). Rather than routing on a weak guess, the system detects this and asks Gemini to pose one more targeted clarifying question instead of finalising. This is capped at one re-prompt to avoid frustrating the patient, and is bypassed entirely for EMERGENCY urgency — in a medical emergency, asking "are you sure?" before calling for help would be actively harmful.

### Why not a database for the queue?

The current JSON file queue store is an intentional scope decision for a prototype. It is thread-safe for single-process deployment via a `threading.Lock()`, which is sufficient for a demonstration. Production deployment would require replacing this with PostgreSQL or SQLite with proper transactions and a multi-worker-safe setup.

\---

## Features

|Feature|Description|
|-|-|
|🚨 Emergency Detection|Rule-based, pre-LLM check for 8 critical condition patterns including cardiac, stroke, respiratory, poisoning, and mental health crisis|
|🤖 AI Triage Chat|Conversational Gemini-powered symptom collection with RAG grounding|
|🎯 Confidence Re-prompting|Automatically asks a clarifying question when AI confidence < 0.6|
|🏥 Clinic Ranking|Distance (Haversine) + queue wait + specialty match + rating scoring|
|📋 Doctor Brief|Full triage conversation + structured AI findings shown to doctor in admin dashboard before patient arrives|
|🔄 Live Queue|Server-Sent Events real-time queue status with phone number lookup|
|👨‍⚕️ Admin Dashboard|Receptionist/doctor interface: call in next patient, manage queue, view patient brief|
|📍 Location Intelligence|Geocoded patient location via Nominatim, clinic coordinates validated for Thane area|
|🧠 154-Condition KB|Expanded RAG knowledge base with symptoms, warning signs, sources, treatment, and care-seeking guidance|
|🔒 Crisis Safety|Dedicated crisis detection path — does NOT cut off patient after displaying helpline numbers|

\---

## Tech Stack

|Layer|Technology|
|-|-|
|Backend|Python 3.12, Flask 3.x|
|AI / LLM|Google Gemini 2.0 Flash (`google-generativeai`)|
|RAG|Custom embedding-based retrieval with keyword fallback|
|Geocoding|Geopy / Nominatim|
|Frontend|Vanilla JS, CSS custom properties, Server-Sent Events|
|Data store|JSON files (queue, clinics, KB)|
|Notifications|Twilio SMS (optional, disabled by default)|

\---

## Project Structure

```
CareRouteAI/
├── main.py                    # CLI entry point (patient + admin flows)
├── config.py                  # All configuration and environment loading
├── requirements.txt
│
├── triage/
│   ├── emergency\_rules.py     # Rule-based emergency/crisis detection
│   ├── symptom\_analyzer.py    # Gemini triage with RAG + confidence logic
│   └── followup\_questions.py  # Specialty-specific follow-up question bank
│
├── rag/
│   ├── knowledge\_base.py      # Embedding retrieval + keyword fallback
│   ├── medical\_kb.json        # Expanded 154-condition medical KB
│   ├── medical\_kb.legacy.json # Original 56-entry KB backup
│   ├── build\_kb\_from\_pdf.py  # Rebuild KB from source PDF
│   └── source\_pdf/            # WHO/CDC-style source material
│
├── clinics/
│   ├── clinic\_manager.py      # Queue management, patient records
│   ├── recommend.py           # Clinic ranking engine (distance + wait + LLM)
│   └── clinics.json           # Clinic data with coordinates and specialties
│
├── utils/
│   ├── helpers.py             # Geocoding, notifications, timestamps
│   └── gemini\_timeout.py      # API timeout configuration
│
├── web/
│   ├── app.py                 # Flask routes (19 endpoints)
│   └── templates/             # Triage chat, results, queue, review, admin
│
├── scripts/
│   └── geocode\_clinics.py     # One-off utility to fix clinic coordinates
│
└── data/
    └── queue\_status.json      # Live queue state (auto-created)
```

To rebuild the knowledge base after changing the source PDF:

```bash
python rag/build_kb_from_pdf.py rag/source_pdf/disease_knowledge_base.pdf \\
    --merge rag/medical_kb.legacy.json --out rag/medical_kb.json
```

\---

## Setup \& Running

### Prerequisites

* Python 3.10+
* A Google Gemini API key (free tier available at [aistudio.google.com](https://aistudio.google.com))

### 1\. Clone and install

```bash
git clone <your-repo-url>
cd CareRouteAI
pip install -r requirements.txt
```

Initialize the database and load the clinic data before the first run:

```bash
python -m flask --app web.app db upgrade
python migrate_clinics.py
python migrate_queue.py
```

### 2\. Configure environment

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

```env
GOOGLE\_API\_KEY=your\_gemini\_api\_key\_here
GEMINI\_MODEL=gemini-2.0-flash
FLASK\_SECRET=any-random-secret-string
ADMIN\_PASSWORD=your\_admin\_password
SMS\_ENABLED=false
```

### 3\. Run the web app

```bash
python web/app.py
# Open http://localhost:5000
```

Admin dashboard is at `http://localhost:5000/admin`

### 4\. CLI mode (optional)

```bash
python main.py patient   # patient triage flow in terminal
python main.py admin     # admin queue management in terminal
```

\---

## Emergency Numbers (India)

|Service|Number|
|-|-|
|Medical Emergency / Ambulance|108|
|Poison Control|1800-11-6117|
|Mental Health / Crisis (Tele-MANAS)|14416|

\---

## Known Limitations

* **Notification system**: The WhatsApp notification via `pywhatkit` requires a live WhatsApp Web browser session and will not work on a headless server. Twilio SMS is stubbed in config but disabled by default. For production, replace with a proper messaging API.
* **Queue persistence**: The JSON file queue resets if the server restarts. Not suitable for production without a proper database.
* **KB accuracy**: The 56-condition knowledge base was manually curated for demonstration purposes and has not been reviewed by a licensed medical professional. The system is a triage routing tool, not a diagnostic system.
* **Tokenizer approximation**: Gemini uses Google's internal SentencePiece tokenizer. Context budget estimates in the RAG layer use a public approximation (`cl100k\_base`) which may differ by \~5–10% from actual token counts.
* **Single-process only**: The `threading.Lock()` in `clinic\_manager.py` is not safe across multiple Flask workers. Deploy with a single worker (`flask run` or `gunicorn -w 1`) for the prototype.

\---

## Disclaimer

CareRoute AI is a triage routing assistant and is not a substitute for professional medical advice, diagnosis, or treatment. It does not diagnose conditions. Always consult a qualified medical professional for health concerns. In an emergency, call **108** immediately.

\---

## Author

* Sami Khan
Built as a Major Project demonstrating applied AI in healthcare routing for the Indian context.



## From Here it Will be a GROUP PROJECT

Sami Khan

Mubashshir Shaikh

Faaz Shaikh





