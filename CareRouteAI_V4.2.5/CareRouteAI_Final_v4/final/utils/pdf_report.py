"""
PDF Report Generation — CareRouteAI
Generates a structured, readable triage + appointment PDF.
Pure Python, no external dependencies.
"""
from datetime import datetime


def _esc(text: str) -> str:
    """Escape special PDF string characters."""
    return str(text).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _build_content(patient_info: dict, triage: dict, booking_info: dict | None) -> bytes:
    now       = datetime.now().strftime("%d %b %Y, %I:%M %p")
    name      = patient_info.get("name", "Patient")
    urgency   = triage.get("urgency", "NON-URGENT")
    specialty = triage.get("specialty", "General Practice")
    confidence = int((triage.get("confidence", 0.8)) * 100)

    URGENCY_CHAR = {"EMERGENCY": "! EMERGENCY", "URGENT": "! URGENT",
                    "SEMI-URGENT": "~ SEMI-URGENT", "NON-URGENT": "OK NON-URGENT"}

    lines = []

    def heading(text, size=14, bold=True):
        lines.append(("heading", text, size, bold))

    def body(text, size=11):
        lines.append(("body", text, size))

    def rule():
        lines.append(("rule",))

    def blank():
        lines.append(("blank",))

    # ── Header ────────────────────────────────────────────
    heading("CareRouteAI — Triage Report", 15)
    body(f"Generated: {now}  |  For: {name}", 10)
    rule()
    blank()

    # ── Urgency banner ────────────────────────────────────
    heading(f"URGENCY: {URGENCY_CHAR.get(urgency, urgency)}", 13)
    severity_desc = {
        "EMERGENCY":    "Life-threatening — seek emergency care immediately. Call 108.",
        "URGENT":       "See a doctor within 2-4 hours.",
        "SEMI-URGENT":  "See a doctor today or tomorrow.",
        "NON-URGENT":   "Schedule an appointment within the week.",
    }.get(urgency, "")
    body(severity_desc)
    blank()

    # ── Patient details ───────────────────────────────────
    heading("PATIENT DETAILS", 12)
    rule()
    body(f"Name    : {name}")
    body(f"Gender  : {patient_info.get('gender', 'N/A').capitalize()}")
    body(f"Phone   : {patient_info.get('phone', 'N/A')}")
    body(f"Location: {patient_info.get('city', 'N/A')}")
    blank()

    # ── Triage summary ────────────────────────────────────
    heading("TRIAGE SUMMARY", 12)
    rule()
    body(f"Specialty      : {specialty.replace('generalPractice','General Practice')}")
    body(f"Urgency        : {urgency}")
    body(f"AI Confidence  : {confidence}%")
    body(f"Primary Concern: {triage.get('primaryConcern', 'As described')}")
    body(f"Duration       : {triage.get('duration', 'As reported')}")
    blank()

    if triage.get("keySymptoms"):
        heading("KEY SYMPTOMS", 11)
        for s in triage["keySymptoms"]:
            body(f"  - {s}")
        blank()

    if triage.get("summary"):
        heading("CLINICAL SUMMARY", 11)
        # Wrap long summary lines
        summary = str(triage["summary"])
        words = summary.split()
        line, chunks = "", []
        for w in words:
            if len(line) + len(w) + 1 > 80:
                chunks.append(line); line = w
            else:
                line = (line + " " + w).strip()
        if line: chunks.append(line)
        for chunk in chunks:
            body(f"  {chunk}")
        blank()

    # ── Condition predictions ─────────────────────────────
    if triage.get("predictions"):
        heading("POSSIBLE CONDITIONS (AI PREDICTION)", 11)
        rule()
        for pred in triage["predictions"][:3]:
            pct = int((pred.get("confidence", 0)) * 100)
            body(f"  {pred.get('condition','?'):30}  {pct}% confidence  ({pred.get('specialty','?')})")
        blank()
        body("NOTE: These are AI-generated predictions, not a medical diagnosis.")
        body("Please consult a qualified doctor for professional medical advice.")
        blank()

    # ── Appointment ───────────────────────────────────────
    if booking_info:
        heading("APPOINTMENT DETAILS", 12)
        rule()
        body(f"Clinic    : {booking_info.get('clinic_name', 'N/A')}")
        body(f"Address   : {booking_info.get('address', 'N/A')}")
        body(f"Phone     : {booking_info.get('phone', 'N/A')}")
        body(f"Token #   : {booking_info.get('token', 'N/A')}")
        body(f"Est. Wait : ~{booking_info.get('wait_mins', 'N/A')} minutes")
        if booking_info.get("booking_link"):
            body(f"Track     : {booking_info.get('booking_link','')}")
        blank()

    # ── Footer ────────────────────────────────────────────
    rule()
    body("CareRouteAI - AI-powered Healthcare Routing | For emergencies, call 108", 9)
    body("This report is AI-generated and does not replace professional medical advice.", 9)

    # ── Build PDF content stream ──────────────────────────
    stream = "BT\n"
    y = 770
    lh_heading = 22
    lh_body    = 16
    lh_blank   = 8

    for item in lines:
        kind = item[0]
        if kind == "blank":
            y -= lh_blank
        elif kind == "rule":
            stream += "ET\n"
            stream += f"0.7 0.7 0.7 RG\n1 w\n50 {y-2} m 545 {y-2} l S\n"
            stream += "0 0 0 RG\n"
            stream += "BT\n"
            y -= 10
        elif kind == "heading":
            _, text, size, _ = item
            stream += f"/F2 {size} Tf\n"
            stream += f"50 {y} Td\n"
            stream += f"({_esc(text)}) Tj\n"
            stream += f"-50 -{lh_heading} Td\n"  # Reset x, move down
            y -= lh_heading
            stream += f"/F1 11 Tf\n"
        elif kind == "body":
            _, text, size = item
            stream += f"/F1 {size} Tf\n"
            stream += f"50 {y} Td\n"
            stream += f"({_esc(text)}) Tj\n"
            stream += f"-50 -{lh_body} Td\n"
            y -= lh_body
            # New page check (rough)
            if y < 60:
                stream += "ET\nshowpage\nBT\n"
                y = 770

    stream += "ET"
    return stream.encode("latin-1", errors="replace")


def generate_triage_pdf(patient_info: dict, triage: dict, booking_info: dict | None = None) -> bytes:
    content_bytes = _build_content(patient_info, triage, booking_info)
    stream_len    = len(content_bytes)

    # Build PDF objects
    objs = []
    objs.append(b"%PDF-1.4\n")
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objs.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objs.append(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]\n   /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>\nendobj\n")
    # Body font
    objs.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    # Bold font for headings
    objs.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")
    # Content stream
    objs.append(
        f"6 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("latin-1")
        + content_bytes
        + b"\nendstream\nendobj\n"
    )

    xref_offset = sum(len(o) for o in objs)
    xref = [b"xref\n0 7\n0000000000 65535 f \n"]
    off = 0
    for o in objs:
        xref.append(f"{off:010d} 00000 n \n".encode("latin-1"))
        off += len(o)
    trailer = (
        b"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n"
        + str(xref_offset).encode("latin-1")
        + b"\n%%EOF\n"
    )
    return b"".join(objs + xref + [trailer])
