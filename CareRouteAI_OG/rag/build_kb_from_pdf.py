"""
Builds rag/medical_kb.json from a structured WHO/CDC disease-reference PDF.

This is a one-time (or "run when the source PDF changes") build step — it does
NOT run at request time. The app always serves from the generated JSON via
MedicalRAG, exactly as before; only the *source* of medical_kb.json changes.

Usage:
    python rag/build_kb_from_pdf.py /path/to/disease_knowledge_base.pdf \
        --merge rag/medical_kb.legacy.json \
        --out rag/medical_kb.json

The source PDF is expected to lay out each disease as a consistent block of
labelled fields (this matches WHO/CDC-style fact-sheet references):

    <Category header line>       (optional, appears once per category)
    <Disease Name>
    ICD-11: <code + WHO ICD-11 title>
    Also known as:
    <text>
    Medical specialty:
    <text>
    Short description:
    <text>
    Common symptoms:
    - bullet
    - bullet
    Signs / clinical features:
    - bullet
    Warning signs / emergency indicators:
    - bullet
    Risk factors:
    - bullet
    Causes / transmission:
    <text>
    Prevention:
    - bullet
    Diagnosis / evaluation:
    <text>
    Treatment / management:
    <text>
    When to seek medical care:
    <text>
    Related conditions / differentials:
    - bullet
    Source: <org> — <url>
"""

import argparse
import json
import re
import sys
from pathlib import Path

try:
    import pypdf
except ImportError:
    print("pypdf is required: pip install pypdf --break-system-packages", file=sys.stderr)
    raise

# Field labels in the exact order they appear in the source PDF. Each tuple is
# (label_as_it_appears, json_key).
FIELD_LABELS = [
    ("Also known as:", "also_known_as"),
    ("Medical specialty:", "specialty"),
    ("Short description:", "description"),
    ("Common symptoms:", "symptoms"),
    ("Signs / clinical features:", "clinical_signs"),
    ("Warning signs / emergency indicators:", "warning_signs"),
    ("Risk factors:", "risk_factors"),
    ("Causes / transmission:", "causes"),
    ("Prevention:", "prevention"),
    ("Diagnosis / evaluation:", "diagnosis"),
    ("Treatment / management:", "treatment"),
    ("When to seek medical care:", "when_to_seek_care"),
    ("Related conditions / differentials:", "related_conditions"),
]

# Fields that are naturally bullet lists vs. free-text paragraphs. Kept as
# lists where the source uses bullets so both retrieval scoring and prompt
# formatting can treat them consistently.
LIST_FIELDS = {
    "symptoms", "clinical_signs", "warning_signs", "risk_factors",
    "prevention", "related_conditions",
}

ICD11_RE = re.compile(r"^ICD-11:\s*(.+)$", re.MULTILINE)
# The organisation name before the "—" occasionally wraps onto a second PDF
# line (long NIH/CDC institute names), so allow the match to span lines up
# to the URL rather than requiring everything on one physical line.
SOURCE_RE = re.compile(r"^Source:\s*(.+?)\s*—\s*(https?://\S+)", re.MULTILINE | re.DOTALL)


def extract_pdf_text(pdf_path: Path) -> str:
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# The PDF's bullet glyph extracts as a lone control/unicode char on its own
# line (observed as U+007F "DEL") rather than being prefixed to the text.
_BULLET_CHARS = "\x7f\u2022\u25cf\u25aa\u2023\u2043\u00b7-"


def clean_bullets(block: str) -> list[str]:
    """Turn a bullet block into a list of clean strings (drop bullet-glyph
    lines and empty lines)."""
    items = [ln.strip().lstrip(_BULLET_CHARS).strip() for ln in block.split("\n")]
    return [ln for ln in items if ln]


def clean_paragraph(block: str) -> str:
    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    return " ".join(lines)


def split_entries(full_text: str) -> list[tuple[str, int, int]]:
    """Return (disease_name, start_idx, end_idx) for every entry, using the
    ICD-11 line as the anchor (each entry has exactly one) and the
    non-empty line immediately preceding it as the disease name."""
    anchors = [m.start() for m in re.finditer(r"^ICD-11:", full_text, re.MULTILINE)]
    entries = []
    for i, start in enumerate(anchors):
        preceding = full_text[max(0, start - 200):start]
        lines = [ln.strip() for ln in preceding.split("\n") if ln.strip()]
        name = lines[-1] if lines else f"Unknown Condition {i}"
        end = anchors[i + 1] if i + 1 < len(anchors) else len(full_text)
        # trim end back to just before the *next* name line so we don't eat it
        entries.append((name, start, end))
    return entries


def parse_entry(name: str, block: str) -> dict:
    entry: dict = {"condition": name}

    icd_match = ICD11_RE.search(block)
    entry["icd11"] = icd_match.group(1).strip() if icd_match else ""

    src_match = SOURCE_RE.search(block)
    if src_match:
        entry["source"] = re.sub(r"\s+", " ", src_match.group(1)).strip()
        entry["source_url"] = src_match.group(2).strip()
    else:
        entry["source"] = ""
        entry["source_url"] = ""

    # Find each field's text span: from right after its label to the start
    # of the next label that appears (in source order).
    label_positions = []
    for label, key in FIELD_LABELS:
        m = re.search(r"^" + re.escape(label) + r"\s*$", block, re.MULTILINE)
        if m:
            label_positions.append((m.end(), key))
    label_positions.append((len(block), "__end__"))

    for i, (pos, key) in enumerate(label_positions[:-1]):
        next_pos = label_positions[i + 1][0]
        # next_pos currently points to end of next label's text (its own
        # match end) only for the sentinel; for real neighbours we need the
        # *start* of that label, so recompute properly below.
        pass

    # Simpler/robust approach: iterate matches with their spans directly.
    matches = []
    for label, key in FIELD_LABELS:
        m = re.search(r"^" + re.escape(label) + r"\s*$", block, re.MULTILINE)
        if m:
            matches.append((m.start(), m.end(), key))
    matches.sort()

    for i, (mstart, mend, key) in enumerate(matches):
        field_end = matches[i + 1][0] if i + 1 < len(matches) else len(block)
        # Don't run into the Source: line
        src_pos = block.find("\nSource:", mend)
        if src_pos != -1 and src_pos < field_end:
            field_end = src_pos
        raw = block[mend:field_end]
        if key in LIST_FIELDS:
            entry[key] = clean_bullets(raw)
        else:
            entry[key] = clean_paragraph(raw)

    # Ensure every expected key exists even if a field was absent in the PDF
    for _, key in FIELD_LABELS:
        entry.setdefault(key, [] if key in LIST_FIELDS else "")

    return entry


def parse_pdf(pdf_path: Path) -> list[dict]:
    text = extract_pdf_text(pdf_path)
    # Only look at content after the index, to avoid the alphabetical index
    # page (which also contains disease names) being mis-parsed.
    body_start = text.find("Disease Entries")
    if body_start != -1:
        text = text[body_start:]

    raw_entries = split_entries(text)
    parsed = []
    for name, start, end in raw_entries:
        block = text[start:end]
        parsed.append(parse_entry(name, block))
    return parsed


_STOPWORDS = {"disease", "disorder", "syndrome", "of", "the", "acute", "chronic"}


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


def condition_keys(name: str) -> set[frozenset]:
    """Generate several normalized token-set aliases for a condition name so
    that e.g. 'GERD (Acid Reflux)' and 'Gastroesophageal Reflux Disease
    (GERD)' are recognized as the same condition regardless of which form
    (acronym vs. full name) is inside the parentheses, and 'Dengue Fever'
    matches 'Dengue', or 'Epilepsy / Seizure Disorder' matches 'Epilepsy'."""
    keys = set()
    # Split combined names like "GERD (Acid Reflux)" or "Epilepsy / Seizure
    # Disorder" into their component name candidates.
    candidates = re.split(r"/|\||\bor\b", re.sub(r"\(([^)]*)\)", r" | \1", name))
    for variant in candidates + [name]:
        k = _norm(variant)
        tokens = frozenset(t for t in k.split() if t not in _STOPWORDS and len(t) > 2)
        if tokens:
            keys.add(tokens)
    return keys


def _keys_overlap(a: set[frozenset], b: set[frozenset]) -> bool:
    for ka in a:
        for kb in b:
            if ka == kb or ka <= kb or kb <= ka:
                return True
    return False


def normalize_legacy_entry(legacy: dict) -> dict:
    """Lift an old-schema entry (condition/specialty/symptoms-string/guidance)
    onto the unified schema so every downstream consumer (embeddings,
    keyword scoring, prompt formatting) can treat all 154 entries alike."""
    symptoms_raw = legacy.get("symptoms", "")
    symptoms = [s.strip() for s in re.split(r",\s*", symptoms_raw) if s.strip()]
    entry = {"condition": legacy.get("condition", "")}
    for _, key in FIELD_LABELS:
        entry[key] = [] if key in LIST_FIELDS else ""
    entry["specialty"] = legacy.get("specialty", "")
    entry["symptoms"] = symptoms
    entry["treatment"] = legacy.get("guidance", "")
    entry["icd11"] = ""
    entry["source"] = "CareRouteAI curated"
    entry["source_url"] = ""
    return entry


def merge_knowledge_bases(who_entries: list[dict], legacy_entries: list[dict]) -> list[dict]:
    """WHO/CDC entries win on overlap (richer, sourced, ICD-11 coded).
    Legacy entries that don't overlap (mostly everyday walk-in complaints
    the WHO fact-sheet set doesn't cover, e.g. dental pain, common cold,
    dry eyes) are kept and normalized onto the same schema so triage
    coverage doesn't regress."""
    who_key_sets = [condition_keys(e["condition"]) for e in who_entries]

    merged = list(who_entries)
    kept_legacy = []
    for legacy in legacy_entries:
        legacy_keys = condition_keys(legacy["condition"])
        if any(_keys_overlap(legacy_keys, wk) for wk in who_key_sets):
            continue  # superseded by the richer WHO/CDC entry
        merged.append(normalize_legacy_entry(legacy))
        kept_legacy.append(legacy["condition"])
    print(f"Kept {len(kept_legacy)} legacy-only entries not covered by WHO/CDC set:",
          file=sys.stderr)
    for c in kept_legacy:
        print(f"  - {c}", file=sys.stderr)
    return merged


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("pdf_path", type=Path)
    ap.add_argument("--merge", type=Path, default=None,
                     help="Legacy medical_kb.json to merge in (non-overlapping entries kept)")
    ap.add_argument("--out", type=Path, default=Path("rag/medical_kb.json"))
    args = ap.parse_args()

    who_entries = parse_pdf(args.pdf_path)
    print(f"Parsed {len(who_entries)} entries from {args.pdf_path}", file=sys.stderr)

    if args.merge and args.merge.exists():
        legacy_entries = json.loads(args.merge.read_text())
        final = merge_knowledge_bases(who_entries, legacy_entries)
    else:
        final = who_entries

    args.out.write_text(json.dumps(final, indent=2, ensure_ascii=False))
    print(f"Wrote {len(final)} entries to {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
