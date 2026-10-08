from __future__ import annotations

import pytest

from app.services.email import EmailBackend, FailoverEmailBackend, SentMessage


class _HttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"status={status_code}")
        self.status_code = status_code


class _Backend(EmailBackend):
    def __init__(self, result: SentMessage | None = None, exc: Exception | None = None):
        self._result = result
        self._exc = exc
        self.calls = 0

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:
        self.calls += 1
        if self._exc:
            raise self._exc
        return self._result or SentMessage(provider_message_id="ok")


def test_failover_uses_fallback_on_429() -> None:
    primary = _Backend(exc=_HttpError(429))
    fallback = _Backend(result=SentMessage(provider_message_id="sendgrid:abc"))
    backend = FailoverEmailBackend(primary, fallback, "acs", "sendgrid")

    result = backend.send("user@example.com", "subject", "body")

    assert result.provider_message_id == "sendgrid:abc"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_failover_uses_fallback_on_timeout() -> None:
    primary = _Backend(exc=TimeoutError("provider timed out"))
    fallback = _Backend(result=SentMessage(provider_message_id="sendgrid:def"))
    backend = FailoverEmailBackend(primary, fallback, "acs", "sendgrid")

    result = backend.send("user@example.com", "subject", "body")

    assert result.provider_message_id == "sendgrid:def"
    assert primary.calls == 1
    assert fallback.calls == 1


def test_failover_does_not_mask_non_transient_errors() -> None:
    primary = _Backend(exc=_HttpError(400))
    fallback = _Backend(result=SentMessage(provider_message_id="sendgrid:ghi"))
    backend = FailoverEmailBackend(primary, fallback, "acs", "sendgrid")

    with pytest.raises(_HttpError):
        backend.send("user@example.com", "subject", "body")

    assert primary.calls == 1
    assert fallback.calls == 0
