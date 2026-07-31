"""
RAG Knowledge Base — CareRouteAI
Embedding-based retrieval over a medical conditions knowledge base.
Uses Gemini's text-embedding model + cosine similarity (no external vector DB).
"""
import json
import math
import os
from pathlib import Path
import google.generativeai as genai
from config import GOOGLE_API_KEY, RAG_KB_FILE, RAG_TOP_K
from utils.gemini_timeout import request_options

genai.configure(api_key=GOOGLE_API_KEY)

EMBED_MODEL  = "models/text-embedding-004"
_CACHE_FILE  = Path(RAG_KB_FILE).with_suffix(".embeddings.json")


def _estimate_tokens(text: str) -> int:
    """Estimate token count without network calls.
    Approximation: 1 token ≈ 4 characters (close to GPT/Gemini average for English).
    Accurate enough for budget-checking; avoids tiktoken's network dependency.
    """
    return max(1, len(text) // 4)


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
        one of the ~25 KB entries individually exhausts its own retry
        budget (each up to GEMINI_TIMEOUT_SECONDS), so building the whole
        KB could take minutes before falling back to keyword search. One
        confirmed failure is enough to know the rest will fail too.
        """
        vectors = []
        for entry in self.kb:
            text = f"{entry['condition']}. Symptoms: {entry['symptoms']}. {entry['guidance']}"
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
        Returns list of {condition, specialty, symptoms, guidance, score}.
        """
        top_k = top_k or RAG_TOP_K
        if not text.strip() or not self._embeddings or not self._embeddings[0]:
            return self._keyword_fallback(text, top_k)

        q_vec = self._embed(text, task_type="retrieval_query")
        if not q_vec:
            return self._keyword_fallback(text, top_k)

        scored = [
            (self._cosine_safe(q_vec, vec), entry)
            for vec, entry in zip(self._embeddings, self.kb)
        ]
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {**entry, "relevance_score": round(score, 3)}
            for score, entry in scored[:top_k]
        ]

    def _cosine_safe(self, a, b):
        return _cosine(a, b) if a and b else 0.0

    def _keyword_fallback(self, text: str, top_k: int) -> list[dict]:
        """If embeddings unavailable (e.g. no API key), fall back to keyword overlap."""
        words = set(text.lower().split())
        scored = []
        for entry in self.kb:
            kb_text = f"{entry['condition']} {entry['symptoms']}".lower()
            overlap = sum(1 for w in words if w in kb_text)
            scored.append((overlap, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {**entry, "relevance_score": score}
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
            lines.append(
                f"- {r['condition']} ({r['specialty']}): {r['symptoms']}. "
                f"Guidance: {r['guidance']}"
            )
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
            chunk = (
                f"Condition: {entry['condition']} ({entry['specialty']})\n"
                f"Symptoms: {entry['symptoms']}\n"
                f"Guidance: {entry['guidance']}\n"
            )
            chunk_tokens = self.count_tokens(chunk)

            # Check if adding this chunk would exceed budget
            if used_tokens + chunk_tokens > max_tokens:
                break

            context_parts.append(chunk)
            used_tokens += chunk_tokens

        # Add separator between entries
        context = "\n---\n".join(context_parts)
        return f"Relevant medical knowledge base entries:\n{context}" if context else ""
