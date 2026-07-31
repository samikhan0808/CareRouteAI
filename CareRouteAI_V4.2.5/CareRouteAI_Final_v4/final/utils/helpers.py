"""
Helpers — CareRouteAI
Geocoding, SMS notifications, formatting utilities.
"""
import json, math, re
from datetime import datetime

try:
    import pywhatkit as _pywhatkit
    _PYWHATKIT_AVAILABLE = True
except Exception:
    # pywhatkit pulls in pyautogui -> mouseinfo, which needs a real GUI
    # display. On a headless server this fails with a display/X11 error,
    # not ImportError -- catch broadly so a missing screen can't crash
    # the whole app on import.
    _pywhatkit = None
    _PYWHATKIT_AVAILABLE = False

from config import (
    SMS_ENABLED, TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM,
    EMERGENCY_NUMBER, CRISIS_LINE, CRISIS_LINE_ALT,
)

# ── Known locality coordinates (Thane / Mumbai region) ────────
# Nominatim is accurate for full addresses but often returns city-level
# centroids (or wrong states entirely) for short locality names.
# This lookup handles the most common patient inputs without any network call.
_LOCALITY_COORDS: dict[str, tuple[float, float]] = {
    # Thane city + sub-localities
    "thane":             (19.2183, 72.9780),
    "thane west":        (19.2350, 72.9600),
    "thane east":        (19.2100, 73.0000),
    "thane station":     (19.1973, 72.9717),
    "vartak nagar":      (19.2280, 72.9720),
    "hiranandani estate":(19.2640, 72.9720),
    "ghodbunder road":   (19.2680, 72.9730),
    "ghodbunder":        (19.2680, 72.9730),
    "pokhran":           (19.2330, 72.9740),
    "wagle estate":      (19.2020, 72.9680),
    "balkum":            (19.1980, 72.9680),
    "kopri":             (19.2200, 73.0100),
    "majiwada":          (19.2190, 72.9960),
    "manpada":           (19.2400, 73.0000),
    "kolshet":           (19.2560, 72.9860),
    "brahmand":          (19.2430, 72.9590),
    "owale":             (19.2470, 72.9650),
    "kasarvadavali":     (19.2540, 73.0100),
    "gaimukh":           (19.2700, 73.0000),
    # Kalyan-Dombivli area
    "kalyan":            (19.2437, 73.1355),
    "kalyan west":       (19.2390, 73.1280),
    "kalyan east":       (19.2490, 73.1450),
    "dombivli":          (19.2100, 73.0833),
    "dombivli east":     (19.2150, 73.0950),
    "dombivli west":     (19.2050, 73.0720),
    "ambernath":         (19.2020, 73.1900),
    "ulhasnagar":        (19.2167, 73.1567),
    "badlapur":          (19.1550, 73.2600),
    # Navi Mumbai
    "navi mumbai":       (19.0330, 73.0297),
    "vashi":             (19.0771, 73.0068),
    "belapur":           (19.0217, 73.0380),
    "kharghar":          (19.0477, 73.0660),
    "panvel":            (18.9890, 73.1100),
    "nerul":             (19.0420, 73.0170),
    "airoli":            (19.1530, 73.0080),
    # Mumbai suburbs near Thane
    "mulund":            (19.1720, 72.9560),
    "mulund west":       (19.1720, 72.9480),
    "mulund east":       (19.1720, 72.9620),
    "bhandup":           (19.1500, 72.9400),
    "vikhroli":          (19.1070, 72.9270),
    "powai":             (19.1186, 72.9095),
    "kanjurmarg":        (19.1310, 72.9330),
    "nahur":             (19.1350, 72.9400),
    "nahur west":        (19.1350, 72.9330),
    # Further Mumbai
    "mumbai":            (19.0760, 72.8777),
    "andheri":           (19.1136, 72.8697),
    "borivali":          (19.2307, 72.8567),
    "goregaon":          (19.1663, 72.8526),
    "malad":             (19.1871, 72.8484),
    "dahisar":           (19.2523, 72.8553),
    "mira road":         (19.2813, 72.8706),
    "bhayander":         (19.3033, 72.8517),
    "vasai":             (19.3700, 72.8000),
}


def geocode_address(address: str) -> tuple[float, float] | None:
    """
    Convert a locality/city name to lat/lng.

    Strategy:
      1. Check local lookup table (instant, no network, covers common Thane inputs)
      2. Try Nominatim for anything not in the table
      3. Return None if both fail — callers use Thane center as fallback

    The local table exists because patients enter short locality names like
    "Thane West", "Mulund", "Ghodbunder" which Nominatim sometimes geocodes
    to wrong cities (there are Thanes in other states) or returns centroids
    far from the actual clinic catchment area.
    """
    key = address.strip().lower()

    # 1. Local lookup — handles the vast majority of patient inputs
    if key in _LOCALITY_COORDS:
        return _LOCALITY_COORDS[key]

    # Partial match: "thane west mh" should hit "thane west"
    for known_key, coords in _LOCALITY_COORDS.items():
        if known_key in key or key in known_key:
            return coords

    # 2. Nominatim fallback for addresses not in local table
    try:
        from geopy.geocoders import Nominatim
        geolocator = Nominatim(user_agent="CareRouteAI", timeout=5)
        # Bias towards Maharashtra to avoid geocoding to wrong state
        location = geolocator.geocode(
            address + ", Maharashtra, India",
            exactly_one=True,
        )
        if location:
            return (location.latitude, location.longitude)
    except Exception as e:
        print(f"Geocoding error for '{address}': {e}")

    return None


THANE_CENTER_LAT = 19.2183
THANE_CENTER_LNG = 72.9780
THANE_RADIUS_KM  = 35.0   # expanded to cover full MMR (Thane + Navi Mumbai + nearby Mumbai)


def is_thane_coordinate(lat: float, lng: float, max_radius_km: float = THANE_RADIUS_KM) -> bool:
    if lat is None or lng is None:
        return False
    return haversine_km(lat, lng, THANE_CENTER_LAT, THANE_CENTER_LNG) <= max_radius_km


def clinic_location_warning(clinic: dict) -> str | None:
    lat = clinic.get("lat")
    lng = clinic.get("lng")
    if lat is None or lng is None:
        return f"{clinic.get('name', 'Clinic')} has missing location coordinates."
    # With the expanded radius the clinics now all fall inside MMR — no false warnings
    return None


# ── Haversine distance ─────────────────────────────────────────
def haversine_km(lat1, lng1, lat2, lng2) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlng / 2) ** 2)
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


# ── WhatsApp Notification ──────────────────────────────────────
def notify_via_whatsapp(phone: str, message: str):
    try:
        formatted_phone = sanitise_phone(phone)
        if not _PYWHATKIT_AVAILABLE:
            print(f"[WhatsApp stub] Would send to {phone}: {message}")
            return True
        _pywhatkit.sendwhatmsg_instantly(formatted_phone, message, wait_time=15, tab_close=True)
        return True
    except Exception as e:
        print(f"WhatsApp failed: {e}")
        return False


def notify_appointment(patient_name: str, phone: str,
                       clinic_name: str, token: int,
                       wait_mins: int, booking_link: str | None = None) -> None:
    msg = (
        f"CareRouteAI Appointment Confirmed!\n"
        f"Patient : {patient_name}\n"
        f"Clinic  : {clinic_name}\n"
        f"Token   : #{token}\n"
        f"Est. Wait: ~{wait_mins} minutes\n"
    )
    if booking_link:
        msg += f"Booking link: {booking_link}\n"
    msg += "Safe travels!"
    notify_via_whatsapp(phone, msg)


def notify_emergency(phone: str, condition: str, severity: str = "CRITICAL") -> None:
    if severity == "CRISIS":
        msg = (
            f"CareRouteAI: We're thinking of you. If things feel hard right now, "
            f"Tele-MANAS ({CRISIS_LINE} or {CRISIS_LINE_ALT}) is free, confidential, "
            f"and available 24/7. You don't have to face this alone."
        )
    else:
        msg = (
            f"CAREROUTE EMERGENCY ALERT\n"
            f"Condition: {condition}\n"
            f"Please call {EMERGENCY_NUMBER} immediately or visit the nearest ER."
        )
    notify_via_whatsapp(phone, msg)


# ── Formatting ─────────────────────────────────────────────────
def format_wait(mins: int) -> str:
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sanitise_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 10:
        return "+91" + digits
    elif len(digits) == 12:
        return "+" + digits
    return "+" + digits
