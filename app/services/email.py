"""Email backends: console, memory, ACS, SendGrid, and failover composition."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from app.config import get_settings

log = logging.getLogger("solutionshub.email")


@dataclass
class SentMessage:
    provider_message_id: str | None


class EmailBackend:
    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:  # pragma: no cover
        raise NotImplementedError


class ConsoleEmailBackend(EmailBackend):
    """Logs the message. Sign-in links appear in the application log for local development."""

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:
        log.info("\n===== EMAIL to %s =====\nSubject: %s\n\n%s\n===== END EMAIL =====", to, subject, text)
        return SentMessage(provider_message_id="console")


class MemoryEmailBackend(EmailBackend):
    """Captures messages in memory for tests."""

    def __init__(self) -> None:
        self.outbox: list[dict] = []

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:
        self.outbox.append({"to": to, "subject": subject, "text": text, "html": html})
        return SentMessage(provider_message_id=f"memory-{len(self.outbox)}")


class AcsEmailBackend(EmailBackend):
    def __init__(self) -> None:
        from azure.communication.email import EmailClient

        s = get_settings()
        if s.acs_connection_string:
            self._client = EmailClient.from_connection_string(s.acs_connection_string)
        elif s.acs_endpoint:
            from azure.identity import DefaultAzureCredential

            self._client = EmailClient(s.acs_endpoint, DefaultAzureCredential())
        else:
            raise RuntimeError("EMAIL_BACKEND=acs requires ACS_CONNECTION_STRING or ACS_ENDPOINT")
        self._sender = s.acs_sender

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:
        message = {
            "senderAddress": self._sender,
            "recipients": {"to": [{"address": to}]},
            "content": {"subject": subject, "plainText": text, **({"html": html} if html else {})},
        }
        poller = self._client.begin_send(message)
        result = poller.result()
        message_id = result.get("id") if isinstance(result, dict) else None
        return SentMessage(provider_message_id=f"acs:{message_id}" if message_id else "acs")


class SendGridEmailBackend(EmailBackend):
    def __init__(self) -> None:
        from sendgrid import SendGridAPIClient

        s = get_settings()
        if not s.sendgrid_api_key:
            raise RuntimeError("EMAIL_BACKEND=sendgrid requires SENDGRID_API_KEY (or SendGridKey)")
        if not s.sendgrid_sender:
            raise RuntimeError("SendGrid requires SENDGRID_SENDER (must match a verified sender identity)")
        self._client = SendGridAPIClient(api_key=s.sendgrid_api_key)
        self._sender = s.sendgrid_sender

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=self._sender,
            to_emails=to,
            subject=subject,
            plain_text_content=text,
            html_content=html,
        )
        response = self._client.send(message)
        headers = dict(getattr(response, "headers", {}) or {})
        message_id = headers.get("X-Message-Id") or headers.get("x-message-id")
        return SentMessage(provider_message_id=f"sendgrid:{message_id}" if message_id else "sendgrid")


def _error_status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _is_transient_send_error(exc: Exception) -> bool:
    status_code = _error_status_code(exc)
    if status_code is not None:
        return status_code in {408, 425, 429, 500, 502, 503, 504}
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    msg = str(exc).lower()
    return "timed out" in msg or "timeout" in msg or "temporar" in msg


class FailoverEmailBackend(EmailBackend):
    def __init__(self, primary: EmailBackend, fallback: EmailBackend, primary_name: str, fallback_name: str) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_name = primary_name
        self._fallback_name = fallback_name

    def send(self, to: str, subject: str, text: str, html: str | None = None) -> SentMessage:
        try:
            return self._primary.send(to, subject, text, html)
        except Exception as primary_exc:  # noqa: BLE001 - fail over on transient provider errors
            if not _is_transient_send_error(primary_exc):
                raise
            log.warning(
                "email provider %s failed (%s); falling back to %s",
                self._primary_name,
                primary_exc.__class__.__name__,
                self._fallback_name,
            )
            return self._fallback.send(to, subject, text, html)


_override: EmailBackend | None = None


def set_email_backend_for_tests(backend: EmailBackend | None) -> None:
    global _override
    _override = backend
    get_email_backend.cache_clear()


@lru_cache
def get_email_backend() -> EmailBackend:
    if _override is not None:
        return _override
    return _build_backend(get_settings().email_backend)


def _build_backend(kind: str, *, allow_failover: bool = True) -> EmailBackend:
    key = (kind or "").lower().strip()
    if key == "acs":
        return AcsEmailBackend()
    if key == "sendgrid":
        return SendGridEmailBackend()
    if key == "memory":
        return MemoryEmailBackend()
    if key == "console":
        return ConsoleEmailBackend()
    if key == "failover" and allow_failover:
        s = get_settings()
        primary = (s.email_primary_backend or "").lower().strip()
        fallback = (s.email_fallback_backend or "").lower().strip()
        if primary in {"", "failover"} or fallback in {"", "failover"}:
            raise RuntimeError("EMAIL_BACKEND=failover requires concrete EMAIL_PRIMARY_BACKEND and EMAIL_FALLBACK_BACKEND")
        if primary == fallback:
            raise RuntimeError("EMAIL_BACKEND=failover requires different EMAIL_PRIMARY_BACKEND and EMAIL_FALLBACK_BACKEND")
        return FailoverEmailBackend(
            _build_backend(primary, allow_failover=False),
            _build_backend(fallback, allow_failover=False),
            primary,
            fallback,
        )
    raise RuntimeError(f"Unsupported EMAIL_BACKEND '{kind}'")
