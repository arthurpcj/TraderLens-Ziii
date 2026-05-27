"""Typed exceptions + IBKR Flex error-code classification.

Single source of truth for error codes so flex_client and state agree.
Error code table + classification mirror the IBKR Flex Web Service spec
(see docs/decisions/002-flex-rate-limit-policy.md).
"""

from __future__ import annotations

# Flex error codes that mean "report still being generated" — safe to retry
# after a short wait (this is the normal SendRequest -> GetStatement gap).
SERVER_BUSY: tuple[str, ...] = ("1009", "1019")

# Flex error codes that mean "you are being rate-limited" — must NOT retry.
# Backing off is mandatory: repeat violations can permanently ban the IP.
CLIENT_THROTTLED: tuple[str, ...] = ("1018",)

# Auth/token failures — not retryable, user must renew the token.
AUTH_ERRORS: tuple[str, ...] = ("1011", "1012", "1013", "1015", "1016")

# Full IBKR Flex error-code table (from ibflex client.py + IBKR docs).
ERROR_MESSAGES: dict[str, str] = {
    "1003": "Statement is not available.",
    "1004": "Statement is incomplete at this time. Please try again shortly.",
    "1005": "Settlement data is not ready at this time. Please try again shortly.",
    "1006": "FIFO P/L data is not ready at this time. Please try again shortly.",
    "1007": "MTM P/L data is not ready at this time. Please try again shortly.",
    "1008": "MTM and FIFO P/L data is not ready at this time. Please try again shortly.",
    "1009": "The server is under heavy load. Statement could not be generated at this time.",
    "1010": "Legacy Flex Queries are no longer supported. Convert to Activity Flex.",
    "1011": "Service account is inactive.",
    "1012": "Token has expired.",
    "1013": "IP restriction.",
    "1014": "Query is invalid.",
    "1015": "Token is invalid.",
    "1016": "Account in invalid.",
    "1017": "Reference code is invalid.",
    "1018": "Too many requests have been made from this token.",
    "1019": "Statement generation in progress. Please try again shortly.",
    "1020": "Invalid request or unable to validate request.",
    "1021": "Statement could not be retrieved at this time. Please try again shortly.",
}


class FlexError(Exception):
    """Base class for Flex Web Service errors."""

    def __init__(self, message: str, code: str | None = None):
        self.code = code
        self.message = message
        super().__init__(f"Code={code}: {message}" if code else message)


class FlexThrottledError(FlexError):
    """Rate-limited (code 1018 or HTTP 429). Do NOT retry — back off."""


class FlexServerBusyError(FlexError):
    """Report not ready yet (codes 1009/1019). Retryable after a short wait."""


class FlexAuthError(FlexError):
    """Token/account auth failure. Not retryable — user must renew token."""


class FlexResponseError(FlexError):
    """Malformed or unexpected Flex response we cannot parse."""


class StateCorruptError(Exception):
    """state.json exists but is not valid/parseable JSON."""


def classify_flex_error(code: str, message: str | None = None) -> FlexError:
    """Map an IBKR Flex error code to the appropriate typed exception."""
    msg = message or ERROR_MESSAGES.get(code, "Unknown Flex error")
    if code in CLIENT_THROTTLED:
        return FlexThrottledError(msg, code)
    if code in SERVER_BUSY:
        return FlexServerBusyError(msg, code)
    if code in AUTH_ERRORS:
        return FlexAuthError(msg, code)
    return FlexResponseError(msg, code)
