"""Step 5 tests — flex_client two-step flow with mocked HTTP (no live IBKR).

Covers AC-7b (throttle no-retry) + AC-14 (1009 retry then success).
"""

from __future__ import annotations

import pytest

from src import flex_client
from src.errors import FlexAuthError, FlexServerBusyError, FlexThrottledError

SEND_OK = (
    b'<FlexStatementResponse timestamp="20 May, 2026 09:00 AM EDT">'
    b"<Status>Success</Status><ReferenceCode>9999</ReferenceCode>"
    b"<Url>https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement</Url>"
    b"</FlexStatementResponse>"
)
QUERY_OK = b'<FlexQueryResponse queryName="t" type="AF"><FlexStatements count="1"></FlexStatements></FlexQueryResponse>'


def _err_envelope(code: str, msg: str = "x") -> bytes:
    return (
        f'<FlexStatementResponse timestamp="20 May, 2026 09:00 AM EDT">'
        f"<Status>Fail</Status><ErrorCode>{code}</ErrorCode><ErrorMessage>{msg}</ErrorMessage>"
        f"</FlexStatementResponse>"
    ).encode()


class FakeResponse:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    """Returns queued responses in order; records calls."""

    def __init__(self, responses: list[FakeResponse]):
        self._queue = list(responses)
        self.calls: list[dict] = []

    def get(self, url, *, params, headers, timeout):
        self.calls.append({"url": url, "params": params})
        return self._queue.pop(0)


def test_happy_path_returns_query_xml():
    sess = FakeSession([FakeResponse(SEND_OK), FakeResponse(QUERY_OK)])
    out = flex_client.download_statement("tok", "123", session=sess)
    assert b"FlexQueryResponse" in out
    assert len(sess.calls) == 2  # SendRequest + GetStatement
    assert headers_ok(sess)


def headers_ok(sess) -> bool:
    # all calls carry v=3 + token + query
    return all(c["params"].get("v") == "3" for c in sess.calls)


def test_1009_then_success_retries(monkeypatch):
    slept = []
    sess = FakeSession([FakeResponse(SEND_OK), FakeResponse(_err_envelope("1009")), FakeResponse(QUERY_OK)])
    out = flex_client.download_statement(
        "tok", "123", session=sess, retry_interval_sec=0, sleep=lambda s: slept.append(s)
    )
    assert b"FlexQueryResponse" in out
    assert len(slept) == 1  # retried once after 1009


def test_1018_throttle_no_retry():
    sess = FakeSession([FakeResponse(SEND_OK), FakeResponse(_err_envelope("1018"))])
    slept = []
    with pytest.raises(FlexThrottledError):
        flex_client.download_statement("tok", "123", session=sess, sleep=lambda s: slept.append(s))
    assert slept == []  # never slept/retried


def test_http_429_throttle():
    sess = FakeSession([FakeResponse(SEND_OK), FakeResponse(b"", status_code=429)])
    with pytest.raises(FlexThrottledError):
        flex_client.download_statement("tok", "123", session=sess)


def test_auth_error_at_send():
    sess = FakeSession([FakeResponse(_err_envelope("1012", "Token has expired."))])
    with pytest.raises(FlexAuthError):
        flex_client.download_statement("tok", "123", session=sess)


def test_server_busy_exhausted_raises():
    sess = FakeSession(
        [FakeResponse(SEND_OK)] + [FakeResponse(_err_envelope("1019")) for _ in range(5)]
    )
    with pytest.raises(FlexServerBusyError):
        flex_client.download_statement(
            "tok", "123", session=sess, retry_count=2, retry_interval_sec=0, sleep=lambda s: None
        )
