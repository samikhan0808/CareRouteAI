"""
Gemini request-options helper — CareRouteAI

google.generativeai accepts a `request_options={"timeout": N}` dict, but
that integer only sets the per-attempt gRPC deadline. The SDK's *default*
retry policy will keep retrying transport-level failures (connection resets,
TLS handshake failures, 503s) past that deadline, so a plain integer timeout
does NOT bound total wall-clock time the way the name suggests — confirmed
by testing: a 5s integer timeout took 30+ seconds to actually raise on an
unreachable endpoint.

To get a real ceiling on total time, pass an explicit `retry.Retry(...)`
with its own `timeout=`, which DOES bound total wall-clock time across all
attempts. Use `request_options()` below everywhere a Gemini call is made.
"""
from google.api_core import retry
from config import GEMINI_TIMEOUT_SECONDS


def request_options(seconds: float = GEMINI_TIMEOUT_SECONDS) -> dict:
    """Build a request_options dict that fails within ~`seconds` total,
    across all retry attempts — not just the first one."""
    return {
        "timeout": seconds,
        "retry": retry.Retry(
            initial=1.0,      # wait 1s before first retry
            multiplier=2.0,   # then back off x2 each time
            maximum=seconds / 2,
            timeout=seconds,  # hard ceiling on TOTAL time across all attempts
        ),
    }
