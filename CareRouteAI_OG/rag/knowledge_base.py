"""
RAG Knowledge Base — CareRouteAI
Embedding-based retrieval over a medical conditions knowledge base.
Uses Gemini's text-embedding model + cosine similarity (no external vector DB).
"""
import json
import logging
import math
import os
import re
from pathlib import Path
import google.generativeai as genai
from config import (
    GOOGLE_API_KEY, RAG_KB_FILE, RAG_TOP_K,
    RAG_SEMANTIC_WEIGHT, RAG_KEYWORD_WEIGHT,
)
from utils.gemini_timeout import request_options

genai.configure(api_key=GOOGLE_API_KEY)

EMBED_MODEL  = "models/text-embedding-004"
_CACHE_FILE  = Path(RAG_KB_FILE).with_suffix(".embeddings.json")
logger = logging.getLogger("carerouteai.rag")


def _estimate_tokens(text: str) -> int:
    """Estimate token count without network calls.
    Approximation: 1 token ≈ 4 characters (close to GPT/Gemini average for English).
    Accurate enough for budget-checking; avoids tiktoken's network dependency.
    """
    return max(1, len(text) // 4)


def _text(field) -> str:
    """Normalize KB fields that may be strings or lists of bullet points."""
    if isinstance(field, list):
        return "; ".join(str(item) for item in field)
    return str(field or "")


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na  = math.sqrt(sum(x * x for x in a))
    nb  = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class MedicalRAG:
    """
    Loads a medical knowledge base, embeds it once (cached to disk),
    and answers `.query(text)` with the top-k most relevant conditions.
    """

    def __init__(self):
        self.kb: list[dict] = json.loads(Path(RAG_KB_FILE).read_text())
        self._embeddings: list[list[float]] = []
        self._load_or_build_embeddings()

    # ── setup ────────────────────────────────────────────
    def _load_or_build_embeddings(self) -> None:
        if _CACHE_FILE.exists():
            try:
                cached = json.loads(_CACHE_FILE.read_text())
                if len(cached.get("vectors", [])) == len(self.kb):
                    self._embeddings = cached["vectors"]
                    return
            except (json.JSONDecodeError, KeyError):
                pass
        self._build_embeddings()

    def _build_embeddings(self) -> None:
        """Embed every KB entry once and cache to disk.

        Stops on the first failure instead of retrying every remaining
        entry — without this, an unreachable/invalid API key means every
        one of the KB entries individually exhausts its own retry
        budget (each up to GEMINI_TIMEOUT_SECONDS), so building the whole
        KB could take minutes before falling back to keyword search. One
        confirmed failure is enough to know the rest will fail too.
        """
        vectors = []
        for entry in self.kb:
            text = self._embedding_text(entry)
            vec = self._embed(text)
            if not vec:
                # First failure — assume the API is unavailable for this run
                # and stop retrying it 24 more times. self._embeddings stays
                # empty, so query() will use the keyword fallback below.
                self._embeddings = []
                return
            vectors.append(vec)
        self._embeddings = vectors
        # Only cache to disk on full success — caching a partial/empty
        # result here would make the next run treat "API was down once"
        # as "embeddings permanently disabled", since _load_or_build_embeddings
        # trusts a same-length cache file without re-checking the API.
        _CACHE_FILE.write_text(json.dumps({"vectors": vectors}))

    def _embedding_text(self, entry: dict) -> str:
        """Build embedding text for both legacy and expanded KB schemas."""
        guidance = _text(entry.get("guidance")) or _text(entry.get("treatment"))
        parts = [
            _text(entry.get("condition")),
            _text(entry.get("also_known_as")),
            f"Symptoms: {_text(entry.get('symptoms'))}",
            f"Warning signs: {_text(entry.get('warning_signs'))}",
            _text(entry.get("description")),
            guidance,
        ]
        return ". ".join(part for part in parts if part)

    def _embed(self, text: str, task_type: str = "retrieval_document") -> list[float]:
        try:
            result = genai.embed_content(
                model=EMBED_MODEL,
                content=text,
                task_type=task_type,
                request_options=request_options(),
            )
            return result["embedding"]
        except Exception:
            # Covers auth errors, network errors, and timeouts alike — on any
            # failure we fall back to keyword search rather than propagate,
            # since this method is also called during __init__ (embedding the
            # whole KB) and a hard failure there would crash app startup.
            return []

    # ── public ───────────────────────────────────────────
    def query(self, text: str, top_k: int = None) -> list[dict]:
        """
        Retrieve the top-k most relevant KB entries for a symptom description.
        Returns entries with combined, semantic, and keyword relevance scores.
        """
        top_k = top_k or RAG_TOP_K
        if not text.strip():
            return []
        if not self._embeddings or not self._embeddings[0]:
            return self._keyword_fallback(text, top_k)

        q_vec = self._embed(text, task_type="retrieval_query")
        if not q_vec:
            return self._keyword_fallback(text, top_k)

        return self._hybrid_query(text, q_vec, top_k)

    def _cosine_safe(self, a, b):
        return _cosine(a, b) if a and b else 0.0

    def _hybrid_query(self, text: str, q_vec: list[float], top_k: int) -> list[dict]:
        semantic_scores = [self._cosine_safe(q_vec, vec) for vec in self._embeddings]
        keyword_scores = [self._keyword_score(text, entry) for entry in self.kb]
        max_keyword = max(keyword_scores) or 1.0
        fused = []
        for entry, semantic, keyword in zip(self.kb, semantic_scores, keyword_scores):
            semantic = max(0.0, min(1.0, semantic))
            keyword = keyword / max_keyword
            combined = (RAG_SEMANTIC_WEIGHT * semantic) + (RAG_KEYWORD_WEIGHT * keyword)
            fused.append((combined, semantic, keyword, entry))
        fused.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                **entry,
                "relevance_score": round(combined, 3),
                "semantic_score": round(semantic, 3),
                "keyword_score": round(keyword, 3),
            }
            for combined, semantic, keyword, entry in fused[:top_k]
        ]

    def _keyword_score(self, text: str, entry: dict) -> float:
        words = [word for word in re.findall(r"[a-z]+", text.lower()) if len(word) > 2]
        if not words:
            return 0.0
        condition_text = f"{_text(entry.get('condition'))} {_text(entry.get('also_known_as'))}".lower()
        warning_text = _text(entry.get("warning_signs")).lower()
        symptom_text = (
            f"{_text(entry.get('symptoms'))} {_text(entry.get('clinical_signs'))} "
            f"{_text(entry.get('description'))} {_text(entry.get('guidance'))} "
            f"{_text(entry.get('treatment'))}"
        ).lower()
        score = 0.0
        for word in set(words):
            if word in condition_text:
                score += 2.0
            elif word in warning_text:
                score += 1.5
            elif word in symptom_text:
                score += 1.0
            elif any(word in token or token in word for token in symptom_text.split() if len(token) > 3):
                score += 0.4
        return score

    def _keyword_fallback(self, text: str, top_k: int) -> list[dict]:
        """Use robust keyword scoring when embeddings are unavailable."""
        scored = [(self._keyword_score(text, entry), entry) for entry in self.kb]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                **entry,
                "relevance_score": round(score, 3),
                "semantic_score": 0.0,
                "keyword_score": round(score, 3),
            }
            for score, entry in scored[:top_k] if score > 0
        ]

    def specialty_candidates(self, text: str, top_k: int = 5) -> list[str]:
        """Return a ranked list of likely specialties based on retrieval."""
        results = self.query(text, top_k=top_k)
        seen, ordered = set(), []
        for r in results:
            sp = r.get("specialty")
            if sp and sp not in seen:
                seen.add(sp)
                ordered.append(sp)
        return ordered

    def format_context(self, results: list[dict]) -> str:
        """Format retrieved KB entries as a context block for the LLM prompt."""
        if not results:
            return ""
        lines = ["Relevant medical knowledge base entries:"]
        for r in results:
            line = f"- {r['condition']} ({r.get('specialty', '')}): {_text(r.get('symptoms'))}."
            warning = _text(r.get("warning_signs"))
            guidance = _text(r.get("guidance")) or _text(r.get("treatment"))
            if warning:
                line += f" Warning signs: {warning}."
            if guidance:
                line += f" Guidance: {guidance}"
            if r.get("source"):
                line += f" (Source: {r['source']})"
            lines.append(line)
        return "\n".join(lines)

    def count_tokens(self, text: str) -> int:
        """Estimate tokens in text (offline, no network needed)."""
        return _estimate_tokens(text)

    def get_context_for_prompt(self, results: list[dict], max_tokens: int = 800) -> str:
        """
        Build a RAG context string that fits within a token budget.
        Trims KB entries if needed to stay under max_tokens.
        Useful for preventing context window overflow on large KBs.
        """
        if not results:
            return ""

        context_parts = []
        used_tokens = 0

        for entry in results:
            # Format this KB entry
            lines = [f"Condition: {entry['condition']} ({entry.get('specialty', '')})"]
            lines.append(f"Symptoms: {_text(entry.get('symptoms'))}")
            warning = _text(entry.get("warning_signs"))
            if warning:
                lines.append(f"Warning signs: {warning}")
            guidance = _text(entry.get("guidance")) or _text(entry.get("treatment"))
            if guidance:
                lines.append(f"Guidance: {guidance}")
            chunk = "\n".join(lines) + "\n"
            chunk_tokens = self.count_tokens(chunk)

            # Check if adding this chunk would exceed budget
            if used_tokens + chunk_tokens > max_tokens:
                break

            context_parts.append(chunk)
            used_tokens += chunk_tokens

        # Add separator between entries
        context = "\n---\n".join(context_parts)
        return f"Relevant medical knowledge base entries:\n{context}" if context else ""
