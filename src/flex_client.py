"""IBKR Flex Web Service HTTP two-step flow (requests; no ibflex dep).

Envelope/endpoint format mirrors ibflex client.py (verified source), but the
rate-limit handling is OURS and deliberately differs: on 1018/429 we raise
FlexThrottledError and do NOT retry (ibflex retries after 10s — a ban risk).
SERVER_BUSY (1009/1019) is the only retryable case (report still generating).

This module does NOT touch state — the caller (ib_sync) runs the rate-limit
gate before invoking download_statement.
"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from typing import Callable, Protocol

import requests

from .constants import (
    FLEX_API_VERSION,
    FLEX_GET_STATEMENT_URL,
    FLEX_SEND_REQUEST_URL,
    FLEX_USER_AGENT,
    HTTP_TIMEOUT_SEC,
    SERVER_BUSY_INTERVAL_SEC,
    SERVER_BUSY_RETRY_MAX,
)
from .errors import (
    FlexResponseError,
    FlexServerBusyError,
    FlexThrottledError,
    classify_flex_error,
)


class _SessionLike(Protocol):
    def get(self, url: str, *, params: dict, headers: dict, timeout: float): ...


def _submit(session: _SessionLike, url: str, token: str, query: str):
    """One GET against a Flex endpoint. HTTP 429 -> throttle (no retry)."""
    resp = session.get(
        url,
        params={"v": FLEX_API_VERSION, "t": token, "q": query},
        headers={"user-agent": FLEX_USER_AGENT},
        timeout=HTTP_TIMEOUT_SEC,
    )
    if getattr(resp, "status_code", 200) == 429:
        raise FlexThrottledError("HTTP 429 Too Many Requests", "429")
    return resp


def _parse_envelope(content: bytes) -> tuple[str | None, dict[str, str | None]]:
    """Parse a <FlexStatementResponse> envelope into (Status, {tag: text})."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise FlexResponseError(f"Unparseable Flex envelope: {exc}") from exc
    if root.tag != "FlexStatementResponse":
        raise FlexResponseError(f"Unexpected envelope tag: {root.tag}")
    data = {child.tag: child.text for child in root}
    status = data.pop("Status", None)
    return status, data


def _parse_send_response(content: bytes) -> tuple[str, str | None]:
    """Step-1 result -> (ReferenceCode, Url). Raises typed error on Fail/Warn."""
    status, data = _parse_envelope(content)
    if status == "Success":
        ref = data.get("ReferenceCode")
        if not ref:
            raise FlexResponseError("SendRequest Success but no ReferenceCode")
        return ref, data.get("Url")
    raise classify_flex_error(data.get("ErrorCode") or "", data.get("ErrorMessage"))


def download_statement(
    token: str,
    query_id: str,
    *,
    retry_count: int = SERVER_BUSY_RETRY_MAX,
    retry_interval_sec: int = SERVER_BUSY_INTERVAL_SEC,
    session: _SessionLike | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """Two-step Flex download. Returns the FlexQueryResponse XML bytes.

    Raises:
        FlexThrottledError  on 1018 / HTTP 429 (NO retry — back off via state).
        FlexServerBusyError on 1009/1019 after `retry_count` exhausted.
        FlexAuthError       on token/account errors.
        FlexResponseError   on malformed/unexpected responses.
    """
    sess = session or requests.Session()

    # Step 1: SendRequest -> ReferenceCode (+ optional Url override)
    send_resp = _submit(sess, FLEX_SEND_REQUEST_URL, token, query_id)
    ref_code, url = _parse_send_response(send_resp.content)
    stmt_url = url or FLEX_GET_STATEMENT_URL

    # Step 2: GetStatement, retrying only on SERVER_BUSY (report not ready)
    attempt = 0
    while True:
        resp = _submit(sess, stmt_url, token, ref_code)
        if b"FlexQueryResponse" in resp.content:
            return resp.content
        # Otherwise it must be an error envelope.
        status, data = _parse_envelope(resp.content)
        err = classify_flex_error(data.get("ErrorCode") or "", data.get("ErrorMessage"))
        if isinstance(err, FlexServerBusyError):
            attempt += 1
            if attempt > retry_count:
                raise err
            sleep(retry_interval_sec)
            continue
        raise err  # FlexThrottledError / FlexAuthError / other -> no retry
