"""Structured patient state accumulated across triage turns."""
from __future__ import annotations

import re
from typing import Any


def new_patient_state() -> dict[str, Any]:
    return {
        "symptoms": [],
        "duration": None,
        "severity": None,
        "onset": None,
        "triggers": [],
        "associated_symptoms": [],
        "risk_factors": [],
        "red_flags": [],
        "age_group": None,
        "relevant_history": [],
        "completed_questions": [],
        "missing_information": [],
    }


_DURATION_RE = re.compile(
    r"\b(\d+\s*(?:day|days|week|weeks|month|months|hour|hours|min|mins|minute|minutes|year|years))\b",
    re.IGNORECASE,
)
_DURATION_WORDS = (
    "today", "yesterday", "since morning", "since last night",
    "few days", "couple of days", "several weeks", "kal se", "aaj se",
)
_SEVERITY_SCALE_RE = re.compile(r"\b([0-9]|10)\s*(?:/|out of)\s*10\b")
_SEVERITY_WORDS = {
    "mild": ("mild", "slight", "a little", "thoda"),
    "moderate": ("moderate", "medium", "manageable"),
    "severe": ("severe", "unbearable", "excruciating", "extreme", "very bad", "bahut zyada", "bahut dard"),
}
_TRIGGER_MARKERS = (
    "worse when", "worse during", "worse after", "triggered by", "happens when",
    "starts when", "after eating", "after exercise", "during walking",
    "khaane ke baad", "chalne ke baad", "exertion",
)
_ASSOCIATED_MARKERS = ("also", "along with", "as well as", "and also", "saath mein", "aur")
_ONSET_WORDS = ("sudden", "suddenly", "gradual", "gradually", "came on slowly", "started suddenly", "achanak")
_RISK_FACTOR_WORDS = (
    "diabetes", "diabetic", "hypertension", "high blood pressure", "smoker", "smoking",
    "pregnant", "pregnancy", "heart disease", "asthma", "obesity", "family history",
    "on medication", "blood thinner",
)
_RED_FLAG_WORDS = (
    "chest pain", "shortness of breath", "can't breathe", "cannot breathe",
    "fainted", "unconscious", "severe bleeding", "blue lips", "confusion",
    "slurred speech", "one side weak", "worst headache of my life",
)


def _extract_symptoms(text_lower: str, kb_vocab: set[str]) -> list[str]:
    return [phrase for phrase in kb_vocab if phrase and phrase in text_lower]


def update_state(state: dict[str, Any], user_text: str, kb_vocab: set[str] | None = None) -> dict[str, Any]:
    text_lower = user_text.lower()
    kb_vocab = kb_vocab or set()
    for symptom in _extract_symptoms(text_lower, kb_vocab):
        if symptom not in state["symptoms"]:
            state["symptoms"].append(symptom)

    match = _DURATION_RE.search(text_lower)
    if match:
        state["duration"] = match.group(1)
    else:
        for word in _DURATION_WORDS:
            if word in text_lower:
                state["duration"] = word
                break

    match = _SEVERITY_SCALE_RE.search(text_lower)
    if match:
        state["severity"] = f"{match.group(1)}/10"
    else:
        for level, words in _SEVERITY_WORDS.items():
            if any(word in text_lower for word in words):
                state["severity"] = level
                break

    for word in _ONSET_WORDS:
        if word in text_lower:
            state["onset"] = "sudden" if "sudden" in word or word == "achanak" else "gradual"
            break

    for marker in _TRIGGER_MARKERS:
        if marker in text_lower and marker not in state["triggers"]:
            state["triggers"].append(marker)
    for marker in _ASSOCIATED_MARKERS:
        if marker in text_lower:
            index = text_lower.find(marker)
            snippet = user_text[index:index + 60].strip()
            if snippet and snippet not in state["associated_symptoms"]:
                state["associated_symptoms"].append(snippet)
    for word in _RISK_FACTOR_WORDS:
        if word in text_lower and word not in state["risk_factors"]:
            state["risk_factors"].append(word)
    for word in _RED_FLAG_WORDS:
        if word in text_lower and word not in state["red_flags"]:
            state["red_flags"].append(word)
    return state


def missing_fields(state: dict[str, Any]) -> list[str]:
    missing = []
    if not state["symptoms"]:
        missing.append("symptoms")
    if not state["duration"]:
        missing.append("duration")
    if not state["severity"]:
        missing.append("severity")
    if not state["associated_symptoms"]:
        missing.append("associated_symptoms")
    state["missing_information"] = missing
    return missing


def state_to_query_text(state: dict[str, Any], latest_message: str) -> str:
    parts = [latest_message]
    if state.get("symptoms"):
        parts.append("Symptoms: " + ", ".join(state["symptoms"]))
    if state.get("associated_symptoms"):
        parts.append("Associated: " + "; ".join(state["associated_symptoms"]))
    if state.get("duration"):
        parts.append(f"Duration: {state['duration']}")
    if state.get("severity"):
        parts.append(f"Severity: {state['severity']}")
    if state.get("triggers"):
        parts.append("Triggers: " + ", ".join(state["triggers"]))
    if state.get("risk_factors"):
        parts.append("Risk factors: " + ", ".join(state["risk_factors"]))
    return "\n".join(parts)


def build_kb_vocab(kb: list[dict]) -> set[str]:
    vocabulary: set[str] = set()
    for entry in kb:
        raw_symptoms = entry.get("symptoms", "")
        symptoms_text = ", ".join(raw_symptoms) if isinstance(raw_symptoms, list) else raw_symptoms
        for phrase in str(symptoms_text).split(","):
            phrase = phrase.strip().lower()
            if len(phrase) >= 4:
                vocabulary.add(phrase)
    return vocabulary
