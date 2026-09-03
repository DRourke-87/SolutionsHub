"""Email backends. `console` prints to the log (local dev); `acs` uses Azure Communication Services."""

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
        return SentMessage(provider_message_id=result.get("id") if isinstance(result, dict) else None)


_override: EmailBackend | None = None


def set_email_backend_for_tests(backend: EmailBackend | None) -> None:
    global _override
    _override = backend
    get_email_backend.cache_clear()


@lru_cache
def get_email_backend() -> EmailBackend:
    if _override is not None:
        return _override
    kind = get_settings().email_backend.lower()
    if kind == "acs":
        return AcsEmailBackend()
    if kind == "memory":
        return MemoryEmailBackend()
    return ConsoleEmailBackend()
