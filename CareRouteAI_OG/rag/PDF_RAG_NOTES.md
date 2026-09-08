# PDF-based RAG — integration notes

This `rag/` folder now serves knowledge-base entries built from the WHO/CDC
**PDF** (`source_pdf/disease_knowledge_base.pdf`), not from a hand-written
JSON file, plus your original 56 curated entries where they aren't already
covered by the PDF.

## Files

- `source_pdf/disease_knowledge_base.pdf` — the source document (127 WHO/CDC entries).
- `build_kb_from_pdf.py` — build-time script. Parses the PDF, merges in
  non-overlapping legacy entries, writes `medical_kb.json`. **Not called at
  request time** — the Flask app only ever reads `medical_kb.json`.
- `medical_kb.json` — the generated, merged knowledge base (154 entries:
  127 from the PDF + 27 legacy-only entries like dental pain, common cold,
  dry eyes that the WHO fact-sheet set doesn't cover).
- `medical_kb.legacy.json` — your original 56-entry KB, kept as the merge
  input (and as a backup of the pre-PDF version).
- `knowledge_base.py` — `MedicalRAG` class, updated to handle the new
  richer schema (list-valued fields like `symptoms`/`warning_signs`, plus
  a keyword-scoring boost when a symptom matches a listed warning sign).

## Re-running the build (e.g. if the PDF changes)

```bash
python rag/build_kb_from_pdf.py rag/source_pdf/disease_knowledge_base.pdf \
    --merge rag/medical_kb.legacy.json \
    --out rag/medical_kb.json
```

Delete `rag/medical_kb.embeddings.json` afterwards (if present) so the app
re-embeds the new KB on next startup instead of using a stale cache.

## What else changed to make this compatible

- `config.py` — added `RAG_SEMANTIC_WEIGHT` / `RAG_KEYWORD_WEIGHT`.
- `triage/disease_predictor.py` — the KB-fallback relevance scorer assumed
  `symptoms` was a plain string; the new KB stores it as a list. Fixed to
  handle both.
