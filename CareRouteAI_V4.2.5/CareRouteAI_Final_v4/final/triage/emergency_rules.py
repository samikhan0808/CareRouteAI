"""
Emergency Rules Engine — CareRouteAI
Detects life-threatening conditions requiring immediate emergency care.
"""
import re
from dataclasses import dataclass
from typing import Optional
from config import EMERGENCY_NUMBER, CRISIS_LINE, CRISIS_LINE_ALT

@dataclass
class EmergencyAlert:
    is_emergency: bool
    condition: str
    severity: str        # "CRITICAL" | "URGENT" | "CRISIS"
    action: str
    call_ambulance: bool
    message: str

EMERGENCY_PATTERNS = {
    "chest_pain": {
        "keywords": [
            "chest pain","chest pressure","chest tightness","chest heaviness",
            "heart attack","cardiac","crushing chest","pain in chest",
            "chest discomfort","pain radiates arm","left arm pain","squeezing chest",
            "stabbing chest"
        ],
        "condition":       "Possible Heart Attack / Cardiac Emergency",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Call 108 IMMEDIATELY. Do not drive yourself.",
        "message": (
            "EMERGENCY: Your symptoms suggest a possible heart attack. "
            "Call 108 immediately. Chew an aspirin (325 mg) if available "
            "and not allergic. Stay calm and sit or lie down."
        ),
    },
    "difficulty_breathing": {
        "keywords": [
            "can't breathe","cannot breathe","difficulty breathing",
            "shortness of breath","hard to breathe","trouble breathing",
            "no air","suffocating","choking","gasping","breathless",
            "unable to breathe","blue lips"
        ],
        "condition":       "Respiratory Distress / Breathing Emergency",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Call 108 NOW. Sit upright, stay calm.",
        "message": (
            "EMERGENCY: Severe breathing difficulty is a medical emergency. "
            "Call 108 immediately. Sit upright, loosen tight clothing. "
            "If you have an inhaler, use it. Do not lie down."
        ),
    },
    "stroke": {
        "keywords": [
            "stroke","face drooping","arm weakness","speech difficulty",
            "sudden confusion","sudden numbness","slurred speech",
            "sudden severe headache","vision loss suddenly","sudden dizziness",
            "can't speak","one side weak","thunderclap headache"
        ],
        "condition":       "Possible Stroke (Brain Attack)",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Call 108 IMMEDIATELY. Every minute counts.",
        "message": (
            "STROKE EMERGENCY — F.A.S.T.: Face drooping, Arm weakness, "
            "Speech difficulty, Time to call 108. Do NOT give food or water. "
            "Note the exact time symptoms started."
        ),
    },
    "heavy_bleeding": {
        "keywords": [
            "heavy bleeding","bleeding won't stop","blood everywhere",
            "losing lot of blood","severe bleeding","bleeding heavily",
            "blood spurting","uncontrolled bleeding","hemorrhage",
            "gushing blood","can't stop bleeding","massive bleeding"
        ],
        "condition":       "Severe / Uncontrolled Bleeding",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Apply firm pressure. Call 108. Do not remove cloth.",
        "message": (
            "EMERGENCY: Apply firm, continuous pressure to the wound. "
            "Do NOT remove it if it soaks through — add more on top. "
            "Elevate the injured area above heart level if possible. Call 108."
        ),
    },
    "loss_of_consciousness": {
        "keywords": [
            "unconscious","passed out","fainted","not waking up","collapsed",
            "loss of consciousness","unresponsive","blacked out","knocked out",
            "won't wake up","can't wake","not responding"
        ],
        "condition":       "Loss of Consciousness / Unresponsiveness",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Call 108. Check breathing. Begin CPR if trained.",
        "message": (
            "EMERGENCY: Call 108 immediately. Check if the person is breathing. "
            "If not breathing and no pulse, begin CPR if trained. "
            "Place unconscious breathing person on their side."
        ),
    },
    "seizure": {
        "keywords": [
            "seizure","convulsion","fitting","shaking uncontrollably",
            "epilepsy attack","epileptic fit","convulsing","having a fit",
            "jerking","twitching severely","grand mal","having seizures","fits"
        ],
        "condition":       "Active Seizure / Convulsion",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Call 108. Do NOT restrain. Move objects away.",
        "message": (
            "SEIZURE EMERGENCY: Do NOT hold the person down or put anything "
            "in their mouth. Clear the area of hard objects. Cushion their head. "
            "Time the seizure — if > 5 minutes or no consciousness, call 108."
        ),
    },
    "anaphylaxis": {
        "keywords": [
            "anaphylaxis","severe allergic reaction","throat closing",
            "tongue swelling","epipen","allergy attack","can't swallow",
            "throat swelling","hives spreading","face swelling","lips swelling"
        ],
        "condition":       "Anaphylaxis / Severe Allergic Reaction",
        "severity":        "CRITICAL",
        "call_ambulance":  True,
        "action":          "Use EpiPen if available. Call 108 immediately.",
        "message": (
            "ANAPHYLAXIS EMERGENCY: Use EpiPen on the outer thigh immediately "
            "if available. Call 108. Lay the person flat with legs elevated "
            "(unless breathing is difficult)."
        ),
    },
}

# Self-harm / suicide risk is intentionally its own category, separate from
# EMERGENCY_PATTERNS: the right response here is a mental-health crisis line
# and a supportive tone, not "call an ambulance / go to the ER" framing.
# severity="CRISIS" lets callers (UI, SMS notify, etc.) branch on this
# distinctly from a medical emergency if they want different handling.
CRISIS_PATTERNS = {
    "suicide_self_harm": {
        "keywords": [
            "suicide","suicidal","kill myself","end my life","want to die",
            "don't want to live","not worth living","better off dead",
            "hurt myself","harm myself","cutting myself","self harm",
            "ending it all","no reason to live","want to disappear forever",
        ],
        "condition":      "Possible Suicide Risk / Self-Harm",
        "severity":       "CRISIS",
        "call_ambulance": False,
        "action":         f"Call or text the Tele-MANAS helpline: {CRISIS_LINE} (or {CRISIS_LINE_ALT}).",
        "message": (
            f"I'm really glad you told me this, and I want you to get support "
            f"right now from people trained for exactly this. Please reach out "
            f"to Tele-MANAS at {CRISIS_LINE} (toll-free, 24/7, also reachable at "
            f"{CRISIS_LINE_ALT}) — they can talk with you in your language. "
            f"If you are in immediate physical danger, please call {EMERGENCY_NUMBER} "
            f"or go to your nearest emergency room. You don't have to go through "
            f"this alone."
        ),
    },
}

URGENT_PATTERNS = {
    "high_fever": {
        "keywords": [
            "very high fever","fever above 104","fever 40 degrees",
            "104 fever","105 fever","burning up","extremely high temperature"
        ],
        "condition":      "Dangerously High Fever (>40 °C)",
        "severity":       "URGENT",
        "call_ambulance": False,
        "action":         "Go to the emergency room immediately.",
        "message": (
            "URGENT: A fever above 40 °C (104 °F) requires immediate "
            "medical attention. Go to the nearest ER now."
        ),
    },
    "head_injury": {
        "keywords": [
            "hit head hard","head injury","concussion","head trauma",
            "knocked head","skull injury","vomiting after head injury"
        ],
        "condition":      "Head Trauma / Possible Concussion",
        "severity":       "URGENT",
        "call_ambulance": False,
        "action":         "Seek emergency care immediately.",
        "message": (
            "URGENT: Head injuries can be serious even without visible wounds. "
            "Watch for vomiting, confusion, unequal pupils, or worsening headache."
        ),
    },
}


def _normalize(s: str) -> str:
    """Lowercase and strip apostrophes (straight ' and curly ’) so "can't"
    and "cant" — or "don't" and "dont" — match the same way. People typing
    in genuine distress very often drop apostrophes, and previously this
    silently caused real emergency phrases like "cant breathe" or "dont
    want to live" to go completely undetected."""
    return re.sub(r"[''']", "", s.lower())


def check_emergency(text: str) -> Optional[EmergencyAlert]:
    """Scan symptom text for emergency keywords. Returns EmergencyAlert or None.

    Checks crisis (self-harm/suicide) patterns first — these were previously
    entirely undetected, falling through to ordinary AI triage. Crisis intent
    is checked ahead of medical patterns since a message could plausibly
    contain both, and the crisis response is the one that must not be missed.
    """
    normalized = _normalize(text)
    for rule in (list(CRISIS_PATTERNS.values()) +
                 list(EMERGENCY_PATTERNS.values()) +
                 list(URGENT_PATTERNS.values())):
        if any(_normalize(kw) in normalized for kw in rule["keywords"]):
            return EmergencyAlert(
                is_emergency=True,
                condition=rule["condition"],
                severity=rule["severity"],
                action=rule["action"],
                call_ambulance=rule["call_ambulance"],
                message=rule["message"],
            )
    return None


def format_alert(alert: EmergencyAlert) -> str:
    if alert.severity == "CRISIS":
        # Deliberately gentler framing than the medical-emergency banner below:
        # no "🔴 CRITICAL", no "stop using this app" — this isn't about routing
        # to a clinic, it's about getting the person to a person who can help.
        return (
            f"\n{'=' * 55}\n"
            f"💙 You're not alone — support is available\n"
            f"{'=' * 55}\n"
            f"{alert.message}\n\n"
            f"{alert.action}\n"
            f"{'=' * 55}\n"
        )

    header = "🔴 CRITICAL EMERGENCY" if alert.severity == "CRITICAL" else "🟠 URGENT — IMMEDIATE CARE"
    amb    = f"\n🚑  CALL {EMERGENCY_NUMBER} (AMBULANCE) NOW\n" if alert.call_ambulance else ""
    return (
        f"\n{'=' * 55}\n{header}\n{'=' * 55}\n"
        f"Condition : {alert.condition}{amb}\n"
        f"{alert.message}\n\n"
        f"Action : {alert.action}\n"
        f"{'=' * 55}\n"
        f"⚕️  Stop using this app — get emergency help NOW.\n"
    )
