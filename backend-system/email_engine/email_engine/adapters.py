"""Pluggable email dispatch adapters.

The send pipeline talks to ONE ``DispatchAdapter`` chosen at runtime by
``EMAIL_DISPATCH_ADAPTER`` (default ``mock``):

  * ``mock``  -- ``MockDispatchAdapter``: logs the message and returns a
    synthetic message id. The default so the pipeline runs end-to-end with no
    credentials (also what the E2E "mock ESP" path exercises).
  * ``smtp``  -- ``SMTPDispatchAdapter``: real send via ``smtplib`` using
    ``SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/SMTP_USE_TLS`` and the
    ``EMAIL_FROM_ADDRESS`` / ``EMAIL_FROM_NAME`` envelope sender.

Adding a provider (e.g. SES) later = one more subclass + a branch in
``build_adapter`` -- callers (send.py) are unchanged.
"""

import logging
import os
import smtplib
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    """Outcome of one adapter send. ``ok`` drives the dispatch-log status
    (Sent vs Failed); ``provider_message_id``/``error`` are recorded as-is."""

    ok: bool
    provider_message_id: Optional[str] = None
    error: Optional[str] = None


class DispatchAdapter:
    """Base send interface. ``provider_name`` is written to
    ``cdp_campaign_dispatch_logs.provider`` for traceability."""

    provider_name = "base"

    def send(self, *, to_email: str, subject: str, html_body: str, text_body: str) -> DispatchResult:
        raise NotImplementedError


class MockDispatchAdapter(DispatchAdapter):
    """No-network adapter: logs the send and always succeeds with a synthetic
    message id. Default adapter, and the one the E2E suite asserts against."""

    provider_name = "mock"

    def send(self, *, to_email: str, subject: str, html_body: str, text_body: str) -> DispatchResult:
        message_id = f"mock-{uuid.uuid4()}"
        logger.info("MockDispatchAdapter: to=%s subject=%r message_id=%s", to_email, subject, message_id)
        return DispatchResult(ok=True, provider_message_id=message_id)


class SMTPDispatchAdapter(DispatchAdapter):
    """Real SMTP send. Connection settings come from a resolved config dict
    (``provider_config.load_email_config`` -- DB/Redis) when provided, else from
    static SMTP_* env vars. A connection is opened per ``send`` (simple +
    retry-safe for the batch sizes this beta targets)."""

    provider_name = "smtp"

    def __init__(self, config: Optional[dict] = None) -> None:
        config = config or {}

        def _cfg(key: str, env: str, default=None):
            value = config.get(key)
            return value if value is not None else os.environ.get(env, default)

        self.host = _cfg("smtp_host", "SMTP_HOST", "localhost")
        self.port = int(_cfg("smtp_port", "SMTP_PORT", 587) or 587)
        self.username = _cfg("smtp_username", "SMTP_USERNAME") or None
        self.password = _cfg("smtp_password", "SMTP_PASSWORD") or None
        use_tls = config.get("smtp_use_tls")
        self.use_tls = use_tls if isinstance(use_tls, bool) else (
            os.environ.get("SMTP_USE_TLS", "true").strip().lower() in {"1", "true", "yes"}
        )
        self.from_address = _cfg("from_address", "EMAIL_FROM_ADDRESS", "no-reply@leocdp.local")
        self.from_name = _cfg("from_name", "EMAIL_FROM_NAME", "LEO CDP")
        self.timeout = int(os.environ.get("SMTP_TIMEOUT_SECONDS", "30"))

    def _build_message(self, *, to_email: str, subject: str, html_body: str, text_body: str) -> EmailMessage:
        message = EmailMessage()
        message["From"] = formataddr((self.from_name, self.from_address))
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text_body or "")
        if html_body:
            message.add_alternative(html_body, subtype="html")
        return message

    def send(self, *, to_email: str, subject: str, html_body: str, text_body: str) -> DispatchResult:
        message = self._build_message(
            to_email=to_email, subject=subject, html_body=html_body, text_body=text_body
        )
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as server:
                if self.use_tls:
                    server.starttls()
                if self.username and self.password:
                    server.login(self.username, self.password)
                server.send_message(message)
        except Exception as exc:  # noqa: BLE001 - surface any SMTP/connection failure as a Failed dispatch.
            logger.warning("SMTPDispatchAdapter: send to %s failed: %s", to_email, exc)
            return DispatchResult(ok=False, error=str(exc))
        return DispatchResult(ok=True, provider_message_id=f"smtp-{uuid.uuid4()}")


def build_adapter(config: Optional[dict] = None) -> DispatchAdapter:
    """Return the dispatch adapter for a resolved provider config dict (from
    ``provider_config.load_email_config`` -- DB/Redis). With no config, falls
    back to ``EMAIL_DISPATCH_ADAPTER`` env (default ``mock``). Unknown providers
    fall back to mock with a warning so a typo never silently drops a run into a
    real-send path."""
    if config is not None:
        provider = (config.get("provider") or "mock").strip().lower()
    else:
        provider = os.environ.get("EMAIL_DISPATCH_ADAPTER", "mock").strip().lower()
    if provider == "smtp":
        return SMTPDispatchAdapter(config)
    if provider != "mock":
        logger.warning("Unknown email provider %r; falling back to mock", provider)
    return MockDispatchAdapter()
