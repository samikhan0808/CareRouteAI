"""Heuristic confidence fusion for triage evidence."""
from __future__ import annotations

from typing import Any

from config import CONFIDENCE_WEIGHTS


def _symptom_match_score(state_symptoms: list[str], rag_top: dict | None) -> float:
    if not state_symptoms or not rag_top:
        return 0.0
    raw_symptoms = rag_top.get("symptoms", "")
    kb_text = (", ".join(raw_symptoms) if isinstance(raw_symptoms, list) else str(raw_symptoms)).lower()
    if not kb_text:
        return 0.0
    matched = sum(1 for symptom in state_symptoms if symptom.lower() in kb_text)
    return round(matched / len(state_symptoms), 3)


def compute_confidence(
    rag_results: list[dict] | None,
    ml_predictions: list[dict] | None,
    triage: dict | None,
    state_symptoms: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> dict[str, Any]:
    weights = weights or CONFIDENCE_WEIGHTS
    rag_results = rag_results or []
    ml_predictions = ml_predictions or []
    triage = triage or {}
    state_symptoms = state_symptoms or []

    rag_top = rag_results[0] if rag_results else None
    ml_top = ml_predictions[0] if ml_predictions else None
    evidence = {
        "rag": round(max(0.0, min(1.0, float((rag_top or {}).get("relevance_score", 0.0)))), 3),
        "ml": round(float((ml_top or {}).get("confidence", 0.0)), 3),
        "symptom_match": _symptom_match_score(state_symptoms, rag_top),
        "llm_assessment": round(float(triage.get("confidence", 0.0) or 0.0), 3),
    }
    present = {
        "rag": bool(rag_top),
        "ml": bool(ml_top),
        "symptom_match": bool(state_symptoms and rag_top),
        "llm_assessment": bool(triage),
    }
    total_weight = sum(weights.get(key, 0.0) for key in evidence if present[key] and weights.get(key, 0.0) > 0)
    weighted_sum = sum(weights.get(key, 0.0) * score for key, score in evidence.items() if present[key] and weights.get(key, 0.0) > 0)
    combined = weighted_sum / total_weight if total_weight else 0.0
    possible_weight = sum(weight for weight in weights.values() if weight > 0)
    coverage = total_weight / possible_weight if possible_weight else 0.0
    combined = round(combined * coverage, 3)
    return {
        "condition": (ml_top or {}).get("condition") or (rag_top or {}).get("condition", "Unknown"),
        "combined_score": combined,
        "evidence": evidence,
        "weights_used": dict(weights),
        "note": "Heuristic evidence fusion, not a clinically validated probability.",
    }
