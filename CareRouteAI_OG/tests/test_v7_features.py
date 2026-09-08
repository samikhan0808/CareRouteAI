import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("GOOGLE_API_KEY", "")
os.environ.setdefault("FLASK_SECRET", "test-secret")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")

from ai.confidence_engine import compute_confidence
from rag.knowledge_base import MedicalRAG
from triage.symptom_state import (
    new_patient_state,
    update_state,
    state_to_query_text,
)


class V7FeatureTests(unittest.TestCase):
    def test_patient_state_accumulates_across_turns(self):
        state = new_patient_state()
        update_state(state, "I have chest pain", {"chest pain"})
        update_state(state, "It is worse when walking and started 2 days ago", set())
        query = state_to_query_text(state, "It is worse when walking")
        self.assertIn("chest pain", query)
        self.assertIn("Duration: 2 days", query)
        self.assertIn("worse when", query)

    def test_confidence_fuses_evidence(self):
        result = compute_confidence(
            [{"condition": "Angina", "symptoms": "chest pain", "relevance_score": 0.9}],
            [{"condition": "Angina", "confidence": 0.8}],
            {"confidence": 0.85},
            ["chest pain"],
        )
        self.assertGreater(result["combined_score"], 0)
        self.assertIn("symptom_match", result["evidence"])
        self.assertLess(result["combined_score"], 0.9)

    def test_rag_keyword_fallback_returns_score_breakdown(self):
        rag = MedicalRAG()
        results = rag.query("Mujhe chest pain aur saans ki takleef hai")
        self.assertTrue(results)
        self.assertIn("semantic_score", results[0])
        self.assertIn("keyword_score", results[0])
        self.assertIn("relevance_score", results[0])


if __name__ == "__main__":
    unittest.main()
