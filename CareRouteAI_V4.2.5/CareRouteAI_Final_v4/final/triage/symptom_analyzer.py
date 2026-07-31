"""
Symptom Analyzer — CareRouteAI
Conversational AI triage using Gemini, grounded by a RAG knowledge base.
Determines specialty, urgency, confidence.
"""
import json
import re
import google.generativeai as genai
from config import GOOGLE_API_KEY, GEMINI_MODEL, MAX_TOKENS_TRIAGE, SUPPORTED_LANGUAGES
from rag.knowledge_base import MedicalRAG
from triage.disease_predictor import DiseasePredictor
from utils.gemini_timeout import request_options
from utils.logger import logger
from triage.followup_questions import get_followups

genai.configure(api_key=GOOGLE_API_KEY)

# Below this, a "ready" triage result is treated as too uncertain to route
# on directly — one clarifying re-prompt is attempted instead. Matches the
# confidence value _force_complete() already uses for its own low-info
# fallback, so this threshold isn't a new judgment call, just an earlier
# trigger for one the codebase already treats as the "uncertain" line.
CONFIDENCE_THRESHOLD = 0.6
MAX_REPROMPTS = 1

SYSTEM = """You are CareRoute AI — a compassionate medical triage assistant in India.

YOUR USERS: Most patients are Indian and do not know medical disease names.
They describe symptoms in everyday words like:
  "sir mein dard" (headache), "pet mein jalan" (stomach burning),
  "aankhon ke aage andhera" (vision darkening), "saans nahi aata" (can't breathe),
  "haath soo gaye" (hands gone numb), "bukhar lag raha hai" (feeling feverish),
  "ulti jaisi feeling" (nausea), "dil tez dhadak raha" (heart racing).

Your job is to translate these plain everyday descriptions into a structured
triage — WITHOUT ever asking the patient to name a disease, use medical terms,
or explain their condition in clinical language.

HOW TO GATHER INFORMATION:
- Accept ANY description: body part + sensation is enough to start
- Use simple, body-part-based follow-ups: "Yeh dard kahan hota hai?" / "Where exactly does it hurt?"
- Ask about timing: "Yeh kab se ho raha hai?" / "Since when?"
- Ask about severity: "1 se 10 mein kitna dard hai?" / "How bad is the pain 1-10?"
- Ask about triggers: "Kuch khaane ke baad badh jaata hai?" / "Does anything make it worse?"
- Ask ONE question at a time — never overwhelm
- If the patient says only "pain" or "not feeling well", ask which body part first

YOUR ROLE:
1. Collect symptom information through warm, natural conversation
2. Ask ONE targeted follow-up question at a time — body-part and sensation focused
3. Use any "RELEVANT MEDICAL KNOWLEDGE" provided to inform your specialty
   and urgency judgment — but never mention the knowledge base to the patient
4. Determine the best medical specialty
5. Assess urgency level
6. Output a structured triage result when ready (usually 3-5 exchanges)

LANGUAGE MIRRORING — THIS IS CRITICAL:
You must detect and match the patient's language style in EVERY reply:

• If patient writes in ENGLISH ("I have chest pain since morning")
  → Reply fully in English. No Hindi words at all.
  Example: "How long have you had this pain? Does it spread to your arm or jaw?"

• If patient writes in HINGLISH ("Mujhe kal se sir mein bahut dard hai", "pet mein jalan ho rahi")
  → Reply in the same natural Hinglish mix. Roman script only — no Devanagari.
  Example: "Kitne din se yeh dard ho raha hai? Koi aur symptoms hain jaise nausea ya vomiting?"

• If patient writes in PURE HINDI (Devanagari: "मुझे बुखार है")
  → Reply in Hinglish Roman script (easier to read on phone keyboards).
  Example: "Kitne din se bukhar hai? Temperature check kiya kya?"

• If patient switches mid-conversation (Hinglish one turn, English next)
  → Always match their LATEST message style. Never stay fixed.

DO NOT default to English when the patient is writing Hinglish.
DO NOT mix languages when the patient is writing pure English.
The goal: patient always feels like they are talking to someone who speaks their way.

STYLE: Warm, calm, reassuring. Never diagnose — only triage and recommend.
       Never use clinical jargon with the patient.
       Always clarify you are not a doctor.

SPECIALTIES: cardiology | pulmonology | neurology | gastroenterology |
             orthopedics | dermatology | generalPractice | ent |
             ophthalmology | psychiatry | emergency | endocrinology |
             urology | gynecology | pediatrics | dentistry
             
URGENCY LEVELS:
  EMERGENCY   — Life-threatening, call 108 now
  URGENT      — See doctor within 2-4 hours
  SEMI-URGENT — See doctor today or tomorrow
  NON-URGENT  — Appointment within a week is fine

When you have enough information, end your reply with this exact block:
<TRIAGE_JSON>
{
  "ready": true,
  "specialty": "cardiology",
  "urgency": "URGENT",
  "confidence": 0.85,
  "primaryConcern": "Chest discomfort with exertion",
  "keySymptoms": ["chest tightness", "shortness of breath"],
  "duration": "2 days",
  "summary": "Patient reports chest tightness during activity for 2 days."
}
</TRIAGE_JSON>

Do NOT output the JSON block until you are genuinely ready to finalize."""
# ── Language style detector ───────────────────────────────────
# Called once per patient message to tell Gemini what style to mirror.
# No ML model needed — simple heuristics work well for this task.

def _detect_language_style(text: str) -> str:
    """
    Detect whether the patient wrote in English, Hinglish, or Hindi/Devanagari
    and return a plain-English instruction string for Gemini.
    """
    # Devanagari Unicode block: U+0900 – U+097F
    devanagari_chars = sum(1 for ch in text if "ऀ" <= ch <= "ॿ")
    total_chars = max(len(text.strip()), 1)

    if devanagari_chars / total_chars > 0.15:
        # Significant Devanagari → pure Hindi script input
        return (
            "pure Hindi (Devanagari script). "
            "Reply in Hinglish using Roman script only — warm, simple, easy to read on phone. "
            "Example: Kitne din se yeh problem hai? Koi aur takleef bhi hai kya?"
        )

    # Hinglish signal words — common Hindi words written in Roman script
    hinglish_markers = [
        "mujhe", "meri", "mera", "mere", "aur", "hai", "hain", "ho",
        "kya", "kab", "kaise", "kyun", "nahi", "nahin", "bahut",
        "thoda", "zyada", "dard", "dawa", "bukhar", "pet", "sir",
        "seena", "haath", "pair", "aankhon", "saans", "ulti", "dast",
        "jalan", "khujli", "sujan", "kamzori", "chakkar", "bhi",
        "se", "mein", "ka", "ki", "ke", "ko", "par", "tak",
        "raha", "rahi", "rahe", "tha", "thi", "the", "hoga",
        "lag", "laga", "lagta", "lagti", "ho raha", "ho rahi",
        "bahut", "bilkul", "thik", "sahi", "galat", "accha",
        # Marathi markers
        "mala", "aahe", "hote", "nahi", "kiti", "tumhala", "diwas",
        "dawa", "gheta", "asta",
    ]

    text_lower = text.lower()
    hinglish_hits = sum(
        1 for marker in hinglish_markers
        if f" {marker} " in f" {text_lower} "
    )

    # If 2+ Hinglish markers found, treat as Hinglish
    if hinglish_hits >= 2:
        return (
            "Hinglish (Hindi-English Roman script mix). "
            "Reply in the same natural Hinglish Roman script. Do NOT use Devanagari. "
            "Example: Kitne din se yeh dard hai? Khaane ke baad badh jaata hai kya?"
        )

    # If 1 marker found and message is short, still treat as Hinglish
    if hinglish_hits == 1 and len(text.split()) <= 8:
        return (
            "Hinglish (short message with Hindi words). "
            "Reply in warm Hinglish Roman script. "
            "Example: Kahan dard ho raha hai exactly? Kitne time se hai?"
        )

    # Default: English
    return (
        "English. Reply fully in English. Do not mix in Hindi or Hinglish words."
    )


_SHARED_RAG = MedicalRAG()
_SHARED_PREDICTOR = DiseasePredictor()

class SymptomAnalyzer:
    def __init__(self, language: str = "en"):
        self.language = language if language in SUPPORTED_LANGUAGES else "en"
        self.history = []
        self.triage_result = None
        self.turn = 0
        self.reprompt_count = 0
        self.rag = _SHARED_RAG
        self.disease_predictor = _SHARED_PREDICTOR
        self.last_rag_results: list[dict] = []

        # For hi/mr we use Hinglish (Hindi/Marathi mixed with English) —
        # pure script responses are harder to read on most mobile keyboards
        # and the mix is how patients in Thane naturally communicate.
        language_names = {
            "en": "English",
            "hi": "Hinglish (natural Hindi-English mix, like: Aapko kitne din se yeh symptoms hain? Koi fever bhi hai?)",
            "mr": "Hinglish with Marathi flavour (Marathi-English mix, like: Tumhala kiti diwasapasun hota? Dawa gheta ka?)",
        }
        language_name = language_names.get(self.language, "English")
        system_prompt = (
            SYSTEM +
            f"\n\nPATIENT LANGUAGE: {language_name}. "
            "When responding to the patient, use this language if possible. "
            "If the patient is more comfortable in Hindi or Marathi, keep the tone warm and reassuring."
        )

        self.model = genai.GenerativeModel(
            model_name=GEMINI_MODEL,
            system_instruction=system_prompt,
        )
        self.chat = self.model.start_chat(history=[])

    # ── public ───────────────────────────────────────────
    def greet(self, name: str = "") -> str:
        label = name or "aap" if self.language in ("hi", "mr") else name or "there"
        if self.language == "hi":
            return (
                f"Namaste {label}! Main CareRoute AI hoon — aapka health guide.\n\n"
                "Main kuch sawal puchhunga taaki samajh sakoon aapko kya problem hai, "
                "aur sahi doctor tak pahunchane mein madad karunga.\n\n"
                "Sab kuch bilkul private rahega.\n\n"
                "Batayein — aaj kaisi takleef ho rahi hai?"
            )
        elif self.language == "mr":
            return (
                f"Namaskar {label}! Mi CareRoute AI aahe — tumcha health guide.\n\n"
                "Mi kahi questions vicharnar aahe mhanje tumhala nakki kuthli takleef aahe "
                "te samjhun yogya doctor kade pathavta yeil.\n\n"
                "Sarva kahi private rahil.\n\n"
                "Sanga — aaj kaay problem hoti aahe?"
            )
        else:
            label = name or "there"
            return (
                f"Hello {label}! I'm CareRoute AI, your health navigator.\n\n"
                "I'll ask a few questions to understand what you're experiencing "
                "and connect you with the right specialist as quickly as possible.\n\n"
                "Everything you share is confidential.\n\n"
                "What brings you in today? Please describe what you're feeling."
            )

    def analyze(self, user_text: str) -> dict:
        """
        Process one turn of the conversation.
        Returns: { "response": str, "complete": bool, "triage": dict | None,
                   "rag_matches": list[dict] }
        """
        self.turn += 1
        self.history.append({"role": "user", "content": user_text})

        # 🔎 RAG retrieval — ground the AI's judgment in real medical knowledge
        rag_results = self.rag.query(user_text, top_k=3)
        self.last_rag_results = rag_results
        # Use token-aware context trimming to prevent context window overflow
        rag_context = self.rag.get_context_for_prompt(rag_results, max_tokens=800)

        # Detect language style and inject as a per-turn instruction so Gemini
        # mirrors the patient's actual style in this specific reply.
        lang_style   = _detect_language_style(user_text)
        lang_instruction = (
            f"\n\n[LANGUAGE INSTRUCTION — follow strictly for THIS reply only]\n"
            f"The patient just wrote in: {lang_style}\n"
            f"Mirror that exact style in your reply. No other language."
        )

        augmented_input = user_text + lang_instruction
        if rag_context:
            augmented_input = (
                f"{user_text}{lang_instruction}"
                f"\n\n[RELEVANT MEDICAL KNOWLEDGE — internal use only, do not repeat to patient]\n{rag_context}"
            )

        ai_text = self._call_api(augmented_input)

        triage  = self._extract_json(ai_text)
        display = self._strip_json(ai_text)

        self.history.append({"role": "assistant", "content": ai_text})

        # Triage finalised
        if triage and triage.get("ready"):
            confidence = triage.get("confidence", 1.0)
            urgency    = triage.get("urgency", "")

            # Low-confidence results get one clarifying re-prompt instead of
            # being routed on immediately — but never delay an EMERGENCY
            # urgency call on confidence grounds; that's the one case where
            # "ask one more question" could itself cause harm.
            if (confidence < CONFIDENCE_THRESHOLD
                    and urgency != "EMERGENCY"
                    and self.reprompt_count < MAX_REPROMPTS
                    and self.turn < 8):
                self.reprompt_count += 1
                # Prefer a structured follow-up when RAG context is thin —
                # it's deterministic, quick to show in a demo, and avoids
                # asking the model to invent a targeted question with no
                # grounding. Fall back to the model-generated nudge when
                # RAG context exists.
                nudge_text = (
                    "Your confidence in that assessment is low. Before "
                    "finalizing, ask the patient ONE more targeted question "
                    "that would most help you narrow down the specialty or "
                    "urgency. Do not output the TRIAGE_JSON block yet."
                )
                # Use the specialty suggested by the (low-confidence) triage
                # as the key into our followup question bank.
                specialty_guess = (triage.get("specialty") if triage else None) or "generalPractice"
                if not rag_context:
                    # structured fallback: one targeted question from the bank
                    followups = get_followups(specialty_guess, count=1)
                    followup_ai_text = followups[0] if followups else (
                        "Could you tell me where exactly you feel the problem?"
                    )
                else:
                    followup_ai_text = self._call_api(nudge_text)
                self.history.append({"role": "assistant", "content": followup_ai_text})
                followup_display = self._strip_json(followup_ai_text)
                combined = "\n\n".join(p for p in (display, followup_display) if p)
                return {
                    "response":    combined,
                    "complete":    False,
                    "triage":      None,
                    "rag_matches": rag_results,
                }

            self.triage_result = triage
            predictions = self._predict_conditions(triage=triage)
            if predictions:

                triage["predicted_condition"] = \
                    predictions[0]["condition"]

                triage["prediction_confidence"] = \
                    predictions[0]["confidence"]
            triage["predictions"] = predictions
            return {
                "response":    display + self._triage_summary(triage),
                "complete":    True,
                "triage":      triage,
                "rag_matches": rag_results,
                "predictions": predictions,
            }

        # Force completion after 8 turns
        if self.turn >= 8:
            forced = self._force_complete()
            predictions = self._predict_conditions(triage=forced)
            forced["predictions"] = predictions
            return {
                "response":    display + "\n\n[Routing you to a specialist now...]",
                "complete":    True,
                "triage":      forced,
                "rag_matches": rag_results,
                "predictions": predictions,
            }

        return {
            "response":    display,
            "complete":    False,
            "triage":      None,
            "rag_matches": rag_results,
        }

    # ── private ──────────────────────────────────────────
    def _predict_conditions(self, triage: dict | None = None) -> list[dict]:
        """Generate top condition predictions using Gemini + triage context."""
        user_text = " ".join(
            msg["content"] for msg in self.history if msg.get("role") == "user"
        )
        return self.disease_predictor.predict(user_text, top_n=3, triage=triage)

    def _call_api(self, user_text: str) -> str:
        """Send a message to the Gemini chat session and return the text.

        Raises no exception on API failure — returns a safe fallback string
        instead, so a Gemini outage/timeout surfaces as a normal chat reply
        ("having trouble, try again") rather than a 500 error mid-triage.
        Note this method does NOT append to self.chat's internal history on
        failure (the send_message call never completed), so a later retry
        from the same conversation state is safe.
        """
        try:
            response = self.chat.send_message(
                user_text,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=MAX_TOKENS_TRIAGE,
                    temperature=0.4,
                ),
                request_options=request_options(),
            )
            return response.text.strip()
        except Exception as first_exc:
            logger.warning(
                "[SymptomAnalyzer] Gemini send_message failed on first attempt: %s",
                first_exc,
            )
            try:
                response = self.chat.send_message(
                    user_text,
                    generation_config=genai.GenerationConfig(
                        max_output_tokens=MAX_TOKENS_TRIAGE,
                        temperature=0.4,
                    ),
                    request_options=request_options(),
                )
                return response.text.strip()
            except Exception as second_exc:
                logger.exception(
                    "[SymptomAnalyzer] Gemini send_message retry failed: %s",
                    second_exc,
                )
                return (
                    "I'm having trouble reaching the assessment service right now. "
                    "Could you try sending that again in a moment? If this keeps "
                    "happening and your symptoms feel urgent, please contact a "
                    "doctor or clinic directly rather than waiting on this chat."
                )

    def _extract_json(self, text: str) -> dict | None:
        m = re.search(r"<TRIAGE_JSON>(.*?)</TRIAGE_JSON>", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass
        return None

    def _strip_json(self, text: str) -> str:
        clean = re.sub(r"<TRIAGE_JSON>.*?</TRIAGE_JSON>", "", text, flags=re.DOTALL)
        return clean.strip()

    def _triage_summary(self, t: dict) -> str:
        emo = {
            "EMERGENCY":   "🔴",
            "URGENT":      "🟠",
            "SEMI-URGENT": "🟡",
            "NON-URGENT":  "🟢",
        }.get(t.get("urgency", ""), "🟢")
        return (
            f"\n\n{'─'*46}\n"
            f"  Triage Assessment Complete\n"
            f"{'─'*46}\n"
            f"  {emo} Urgency    : {t.get('urgency', 'NON-URGENT')}\n"
            f"  🏥 Specialty  : {t.get('specialty', 'General Practice')}\n"
            f"  🎯 Confidence : {int(t.get('confidence', 0.8) * 100)}%\n"
            f"  📋 Concern    : {t.get('primaryConcern', 'As described')}\n"
            f"{'─'*46}\n"
            "  Finding the best available clinics for you...\n"
        )

    def _force_complete(self) -> dict:
        """Ask Gemini to finalise triage immediately."""
        ai_text = self._call_api("Please give your final triage assessment JSON now.")
        self.history.append({"role": "assistant", "content": ai_text})
        result = self._extract_json(ai_text)
        return result or {
            "ready":          True,
            "specialty":      "generalPractice",
            "urgency":        "NON-URGENT",
            "confidence":     0.6,
            "primaryConcern": "General health concern",
            "keySymptoms":    [],
            "duration":       "unknown",
            "summary":        "Patient reported symptoms requiring GP evaluation.",
        }