"""
Health Report Card — CareRouteAI
Generates a multi-visit AI Health Report Card PDF: visit history,
condition-frequency trend graph, and recurring-pattern alerts.
Pure Python, no external dependencies — same low-level PDF-writing
approach as pdf_report.py, but uses absolute text positioning (Tm)
instead of relative (Td) so lines never overlap or go missing.
"""
from datetime import datetime


def _esc(text: str) -> str:
    """Escape special PDF string characters."""
    return str(text).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _build_content(patient_info: dict, history: dict) -> bytes:
    now   = datetime.now().strftime("%d %b %Y, %I:%M %p")
    name  = patient_info.get("name", "Patient")
    phone = patient_info.get("phone", "N/A")

    total_visits         = history.get("total_visits", 0)
    visits               = history.get("visits", [])
    condition_frequency  = history.get("condition_frequency", {})
    recurring            = history.get("recurring", [])

    lines = []

    def heading(text, size=14):
        lines.append(("heading", text, size))

    def body(text, size=11):
        lines.append(("body", text, size))

    def alert(text, size=11):
        lines.append(("alert", text, size))

    def rule():
        lines.append(("rule",))

    def blank():
        lines.append(("blank",))

    def chart(bars):
        lines.append(("chart", bars))

    # ── Header ────────────────────────────────────────────
    heading("CareRouteAI - AI Health Report Card", 15)
    body(f"Generated: {now}  |  For: {name}  |  Phone: {phone}", 10)
    rule()
    blank()

    # ── Summary ───────────────────────────────────────────
    heading("VISIT SUMMARY", 12)
    rule()
    body(f"Total Visits    : {total_visits}")
    body(f"First Visit     : {history.get('first_visit', 'N/A')}")
    body(f"Most Recent     : {history.get('last_visit', 'N/A')}")
    blank()

    # ── Recurring pattern alert ────────────────────────────
    if recurring:
        alert(f"[ALERT] Recurring pattern detected: {', '.join(recurring)}")
        alert("Consider follow-up with a specialist for long-term monitoring.")
        blank()

    # ── Visit history table (text-aligned, matches existing report style) ──
    if visits:
        heading("VISIT HISTORY (Most Recent First)", 12)
        rule()
        body(f"{'DATE':12} {'CONDITION (AI)':28} {'CONF.':7} {'URGENCY':12}")
        for v in visits[:8]:
            cond = (v['condition'] or 'N/A')[:28]
            body(f"{v['date']:12} {cond:28} {str(v['confidence'])+'%':7} {v['severity']:12}")
        blank()
        if total_visits > 8:
            body(f"... and {total_visits - 8} more earlier visit(s).")
            blank()

    # ── Condition frequency trend graph (vector bar chart) ─
    if condition_frequency:
        heading("SYMPTOM / CONDITION TREND", 12)
        rule()
        sorted_conditions = sorted(condition_frequency.items(), key=lambda x: x[1], reverse=True)[:5]
        chart(sorted_conditions)
        blank()

    # ── Footer ────────────────────────────────────────────
    rule()
    body("NOTE: This report summarizes AI-generated triage history and is not a medical diagnosis.", 9)
    body("CareRouteAI - AI-powered Healthcare Routing | For emergencies, call 108", 9)
    body("Please consult a qualified doctor for professional medical advice.", 9)

    # ── Build PDF content stream (absolute Tm positioning) ─
    stream = "BT\n"
    y = 770
    lh_heading = 22
    lh_body    = 16
    lh_blank   = 8
    lh_bar     = 20

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
            _, text, size = item
            stream += f"/F2 {size} Tf\n"
            stream += f"1 0 0 1 50 {y} Tm\n"
            stream += f"({_esc(text)}) Tj\n"
            y -= lh_heading
            stream += f"/F1 11 Tf\n"
        elif kind == "body":
            _, text, size = item
            stream += f"/F1 {size} Tf\n"
            stream += f"1 0 0 1 50 {y} Tm\n"
            stream += f"({_esc(text)}) Tj\n"
            y -= lh_body
            if y < 100:
                stream += "ET\nshowpage\nBT\n"
                y = 770
        elif kind == "alert":
            _, text, size = item
            stream += "0.75 0.1 0.1 rg\n"
            stream += f"/F2 {size} Tf\n"
            stream += f"1 0 0 1 50 {y} Tm\n"
            stream += f"({_esc(text)}) Tj\n"
            stream += "0 0 0 rg\n"
            y -= lh_body
        elif kind == "chart":
            _, bars = item
            if bars:
                max_count = max(c for _, c in bars) or 1
                bar_max_width = 280
                stream += "ET\n"
                for label, count in bars:
                    bar_width = (count / max_count) * bar_max_width
                    stream += f"0.2 0.45 0.65 rg\n50 {y-10} {bar_width:.1f} 12 re f\n"
                    stream += "0 0 0 rg\nBT\n/F1 10 Tf\n"
                    stream += f"1 0 0 1 {50+bar_width+8:.1f} {y-8} Tm\n"
                    label_text = f"{label[:30]} - {count} visit(s)"
                    stream += f"({_esc(label_text)}) Tj\nET\n"
                    y -= lh_bar
                    if y < 100:
                        stream += "showpage\n"
                        y = 770
                stream += "BT\n"

    stream += "ET"
    return stream.encode("latin-1", errors="replace")


def generate_health_report_pdf(patient_info: dict, history: dict) -> bytes:
    """
    patient_info: {"name": str, "phone": str}
    history:      dict returned by db_history.get_health_report_data(phone)
    """
    content_bytes = _build_content(patient_info, history)
    stream_len    = len(content_bytes)

    objs = []
    objs.append(b"%PDF-1.4\n")
    objs.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objs.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objs.append(b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]\n   /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>\nendobj\n")
    objs.append(b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")
    objs.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n")
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
