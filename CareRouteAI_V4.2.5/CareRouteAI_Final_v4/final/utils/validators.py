# utils/validators.py

import re

def validate_phone(phone: str) -> bool:
    """
    Accept Indian phone numbers in multiple formats:
      - 10-digit mobile: 9876543210
      - With country code: +919876543210 or 919876543210
      - With spaces/dashes: +91-98765-43210
    Returns True if valid, False otherwise.
    """
    if not phone:
        return False
    # Strip all non-digit characters except leading +
    digits = re.sub(r"[^\d]", "", phone)
    # Plain 10-digit Indian mobile (starts 6–9)
    if re.match(r"^[6-9]\d{9}$", digits):
        return True
    # With country code 91 prefix → 12 digits
    if re.match(r"^91[6-9]\d{9}$", digits):
        return True
    return False
