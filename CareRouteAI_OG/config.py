"""
CareRouteAI — Configuration
"""
import os
import secrets
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent

# ── Google Gemini ─────────────────────────────────────────
GOOGLE_API_KEY    = os.getenv("GOOGLE_API_KEY", "")
GEMINI_MODEL      = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_TOKENS_TRIAGE = 1000
MAX_TOKENS_RANK   = 2000
# Hard ceiling (seconds) for any single Gemini API call. Without this, the
# underlying gRPC client retries for minutes by default on a slow/unreachable
# endpoint, which hangs the Flask worker handling the request and never lets
# the fallback (keyword search / rule-based ranking) code paths run — even
# though those paths exist specifically to handle this case.
GEMINI_TIMEOUT_SECONDS = int(os.getenv("GEMINI_TIMEOUT_SECONDS", "25"))

# ── Location ──────────────────────────────────────────────
DEFAULT_CITY      = "Thane"
DEFAULT_LAT       = 19.2183
DEFAULT_LNG       = 72.9780

# ── Queue settings ────────────────────────────────────────
MAX_QUEUE_LENGTH  = 20
QUEUE_FILE        = str(PROJECT_ROOT / "data" / "queue_status.json")

# ── Clinic data ───────────────────────────────────────────
CLINICS_FILE      = str(PROJECT_ROOT / "clinics" / "clinics.json")

# ── RAG settings ──────────────────────────────────────────
RAG_KB_FILE       = str(PROJECT_ROOT / "rag" / "medical_kb.json")
RAG_TOP_K         = 3
RAG_SEMANTIC_WEIGHT = float(os.getenv("RAG_SEMANTIC_WEIGHT", "0.65"))
RAG_KEYWORD_WEIGHT  = float(os.getenv("RAG_KEYWORD_WEIGHT", "0.35"))

# ── Emergency numbers ─────────────────────────────────────
EMERGENCY_NUMBER  = "108"
POISON_CONTROL    = "1800-11-6117"
# Tele-MANAS: India's government-run, 24/7, toll-free mental health and
# suicide-prevention helpline (absorbed the earlier KIRAN helpline in 2022).
CRISIS_LINE        = "14416"
CRISIS_LINE_ALT    = "1-800-891-4416"
# ── Analytics / Internationalization ─────────────────────────────
ANALYTICS_FILE = str(PROJECT_ROOT / "data" / "analytics.json")
SUPPORTED_LANGUAGES = ["en", "hi", "mr"]
DEFAULT_LANGUAGE = os.getenv("DEFAULT_LANGUAGE", "en")
# ── Notification (SMS stub) ───────────────────────────────
SMS_ENABLED   = os.getenv("SMS_ENABLED", "False") == "True"
TWILIO_SID    = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN  = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM   = os.getenv("TWILIO_FROM_NUMBER", "")

# ── Database ───────────────────────────────

# Keep the default database in Flask's instance directory so starting the app
# from a different working directory cannot silently create a second database.
DEFAULT_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'instance' / 'careroute.db').as_posix()}"
DATABASE_URL = os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL

# ── Flask / Admin ─────────────────────────────────────────
FLASK_SECRET = os.getenv("FLASK_SECRET", "").strip() or secrets.token_hex(32)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
FLASK_ENV = os.getenv("FLASK_ENV", "development").strip().lower()
IS_PRODUCTION = FLASK_ENV == "production"
ADMIN_LOGIN_MAX_ATTEMPTS = int(os.getenv("ADMIN_LOGIN_MAX_ATTEMPTS", "5"))
ADMIN_LOGIN_WINDOW_SECONDS = int(os.getenv("ADMIN_LOGIN_WINDOW_SECONDS", "300"))

# Heuristic evidence weights, not calibrated medical probabilities.
CONFIDENCE_WEIGHTS = {
	"rag":             float(os.getenv("CONFIDENCE_WEIGHT_RAG", "0.30")),
	"ml":              float(os.getenv("CONFIDENCE_WEIGHT_ML", "0.25")),
	"symptom_match":   float(os.getenv("CONFIDENCE_WEIGHT_SYMPTOM_MATCH", "0.25")),
	"llm_assessment":  float(os.getenv("CONFIDENCE_WEIGHT_LLM", "0.20")),
}

CLINIC_RANKING_WEIGHTS = {
	"specialty_match": int(os.getenv("CLINIC_WEIGHT_SPECIALTY", "30")),
	"emergency_bonus": int(os.getenv("CLINIC_WEIGHT_EMERGENCY", "15")),
	"emergency_penalty": int(os.getenv("CLINIC_WEIGHT_EMERGENCY_PENALTY", "20")),
	"distance_max": int(os.getenv("CLINIC_WEIGHT_DISTANCE_MAX", "18")),
	"wait_max": int(os.getenv("CLINIC_WEIGHT_WAIT_MAX", "12")),
	"rating_max": int(os.getenv("CLINIC_WEIGHT_RATING_MAX", "10")),
	"base": int(os.getenv("CLINIC_WEIGHT_BASE", "40")),
}
