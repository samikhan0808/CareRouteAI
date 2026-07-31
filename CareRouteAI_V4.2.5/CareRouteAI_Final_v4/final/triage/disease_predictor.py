"""
Disease Predictor — CareRouteAI
Uses Gemini to predict likely conditions from the already-completed triage JSON.

Why Gemini instead of TF-IDF + RandomForest?
  The original ML approach failed silently for Hinglish patients: the TF-IDF
  model was trained on English medical KB text ("chest tightness", "shortness
  of breath") but patients write "sir mein dard", "pet mein jalan", "saans
  nahi aata". Zero vocabulary overlap → the classifier predicted random
  conditions with false confidence.

  Gemini already understands the triage context (specialty, urgency, symptoms,
  primaryConcern) and reads both English and Hinglish. We pass it the completed
  triage JSON and ask for top-3 plausible conditions — grounded in what the AI
  already assessed, not a separate keyword search.
"""
import json
import re
from pathlib import Path

KB_PATH = Path(__file__).resolve().parents[1] / "rag" / "medical_kb.json"

# Specialties → conditions lookup built once from the KB at startup.
# Used as a fallback when Gemini is unavailable or returns bad JSON.
_KB_CACHE: dict[str, list[dict]] | None = None


def _get_kb_by_specialty() -> dict[str, list[dict]]:
    global _KB_CACHE
    if _KB_CACHE is not None:
        return _KB_CACHE
    try:
        entries = json.loads(KB_PATH.read_text(encoding="utf-8"))
    except Exception:
        entries = []
    result: dict[str, list[dict]] = {}
    for e in entries:
        spec = e.get("specialty", "generalPractice")
        result.setdefault(spec, []).append(e)
    _KB_CACHE = result
    return result


def _fallback_by_specialty(specialty: str, top_n: int = 3,
                            symptoms: list | None = None) -> list[dict]:
    """
    Return top-n conditions from the KB matching the given specialty.
    If symptom keywords are provided, score by overlap first so the
    fallback is at least roughly relevant rather than returning the
    first N entries blindly.
    """
    by_spec = _get_kb_by_specialty()
    candidates = by_spec.get(specialty, []) or by_spec.get("generalPractice", [])

    if symptoms:
        symptom_words = set(" ".join(symptoms).lower().split())
        def relevance(entry):
            kb_words = set(
                (entry.get("symptoms", "") + " " + entry.get("condition", "")).lower().split()
            )
            return len(symptom_words & kb_words)
        candidates = sorted(candidates, key=relevance, reverse=True)

    results = []
    for i, entry in enumerate(candidates[:top_n]):
        conf = max(0.40, 0.70 - i * 0.15)
        results.append({
            "condition":  entry.get("condition", "Unknown"),
            "specialty":  entry.get("specialty", specialty),
            "confidence": conf,
        })
    return results


class DiseasePredictor:
    """
    Predict likely conditions using Gemini, with a KB-based fallback.
    Called after triage is complete — receives the full triage dict.
    """

    def __init__(self, kb_path: Path | None = None):
        self.kb_path = kb_path or KB_PATH
        # Pre-warm the KB cache
        _get_kb_by_specialty()

    def predict(self, user_text: str, top_n: int = 3,
                triage: dict | None = None) -> list[dict]:
        """
        Predict top-n likely conditions.

        Preferred path: use Gemini with triage JSON as context.
        Fallback path:  use KB lookup by specialty when Gemini fails.

        Args:
            user_text:  All patient messages concatenated (kept for signature
                        compatibility but not used when triage is provided).
            top_n:      Number of conditions to return (max 3).
            triage:     Completed triage dict from SymptomAnalyzer.
        """
        # Gemini path — only if triage data is available
        if triage:
            try:
                result = self._predict_with_gemini(triage, top_n)
                if result:
                    return result
            except Exception as e:
                print(f"[DiseasePredictor] Gemini failed, using KB fallback: {e}")

        # Fallback: KB lookup by specialty + symptom scoring
        specialty = (triage or {}).get("specialty", "generalPractice")
        symptoms  = (triage or {}).get("keySymptoms", [])
        return _fallback_by_specialty(specialty, top_n, symptoms=symptoms)

    # ── Gemini prediction ─────────────────────────────────────────
    def _predict_with_gemini(self, triage: dict, top_n: int) -> list[dict]:
        import google.generativeai as genai
        from config import GOOGLE_API_KEY, GEMINI_MODEL
        from utils.gemini_timeout import request_options

        genai.configure(api_key=GOOGLE_API_KEY)

        specialty = triage.get("specialty", "generalPractice")
        urgency   = triage.get("urgency", "NON-URGENT")
        concern   = triage.get("primaryConcern", "")
        symptoms  = triage.get("keySymptoms", [])
        summary   = triage.get("summary", "")

        # Load KB entries for this specialty to give Gemini a bounded list
        by_spec   = _get_kb_by_specialty()
        candidates = by_spec.get(specialty, []) + by_spec.get("generalPractice", [])
        candidate_names = [e["condition"] for e in candidates[:20]]

        prompt = f"""You are a medical triage assistant. Based on the completed triage below,
predict the top {top_n} most likely conditions. Choose ONLY from the candidate list provided.

TRIAGE SUMMARY:
- Specialty   : {specialty}
- Urgency     : {urgency}
- Primary concern: {concern}
- Key symptoms: {', '.join(symptoms) if symptoms else summary}

CANDIDATE CONDITIONS (choose from these only):
{chr(10).join(f'  - {c}' for c in candidate_names)}

Respond ONLY with a JSON array. No explanation, no markdown, no extra text.
Each item must have exactly these keys: "condition", "specialty", "confidence" (0.0–1.0).
Confidence should reflect how well the triage evidence matches this condition.
Never exceed 0.85 — these are possibilities, not diagnoses.

Example format:
[
  {{"condition": "Migraine", "specialty": "neurology", "confidence": 0.78}},
  {{"condition": "Tension Headache", "specialty": "neurology", "confidence": 0.65}},
  {{"condition": "Hypertension (High Blood Pressure)", "specialty": "cardiology", "confidence": 0.45}}
]"""

        model = genai.GenerativeModel(model_name=GEMINI_MODEL)
        response = model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                max_output_tokens=1536,
                temperature=0.2,
            ),
            request_options=request_options(),
        )
        raw = response.text.strip()

        # Strip ALL markdown fence variants Gemini might produce.
        raw = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()

        # Extract the JSON array. If the response was cut off, salvage complete objects.
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1:
            raise ValueError(f"No JSON array found in Gemini response: {raw[:120]!r}")
        raw = raw[start:end + 1] if end != -1 and end > start else raw[start:]

        try:
            predictions = json.loads(raw)
        except json.JSONDecodeError:
            salvaged = re.findall(r"\{[^{}]+\}", raw)
            if not salvaged:
                raise
            predictions = [json.loads(obj) for obj in salvaged]

        valid = []
        for p in predictions:
            if isinstance(p, dict) and "condition" in p and "confidence" in p:
                try:
                    confidence = float(min(max(p["confidence"], 0.0), 0.85))
                except (TypeError, ValueError):
                    confidence = 0.40
                valid.append({
                    "condition": str(p["condition"]),
                    "specialty": str(p.get("specialty", specialty)),
                    "confidence": confidence,
                })

        if valid:
            return valid[:top_n]

        raise ValueError("No valid predictions could be parsed from Gemini response")