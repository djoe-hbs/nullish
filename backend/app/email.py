"""Transactional email delivery via SMTP (email verification and password reset)."""

from __future__ import annotations

import asyncio
import html
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr
from urllib.parse import quote

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class EmailDeliveryError(RuntimeError):
    """Raised when an email cannot be sent due to configuration or SMTP failure."""


def resolve_app_base_url(settings: Settings | None = None) -> str:
    """Return the public frontend origin used to build links inside emails."""
    cfg = settings or get_settings()
    if cfg.app_base_url.strip():
        return cfg.app_base_url.strip().rstrip("/")
    for origin in cfg.frontend_origin.split(","):
        cleaned = origin.strip().rstrip("/")
        if cleaned:
            return cleaned
    return "http://localhost:3000"


def build_auth_action_url(route: str, token: str, settings: Settings | None = None) -> str:
    """Build a hash-routed frontend action URL carrying a one-time token."""
    base = resolve_app_base_url(settings)
    clean_route = route.lstrip("#/")
    return f"{base}/#{clean_route}?token={quote(token, safe='')}"


def _send_smtp_sync(settings: Settings, msg: EmailMessage) -> None:
    """Send *msg* over SMTP synchronously (called inside a worker thread)."""
    tls_context = ssl.create_default_context()
    if settings.smtp_use_ssl:
        with smtplib.SMTP_SSL(
            settings.smtp_host,
            settings.smtp_port,
            timeout=settings.smtp_timeout_sec,
            context=tls_context,
        ) as client:
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(msg)
        return

    with smtplib.SMTP(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_sec,
    ) as client:
        if settings.smtp_use_starttls:
            client.starttls(context=tls_context)
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(msg)


class Mailer:
    """SMTP mailer used by authentication endpoints and injectable in tests."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    @property
    def is_configured(self) -> bool:
        from_addr = (self.settings.smtp_from_email or self.settings.smtp_username).strip()
        return bool(self.settings.smtp_host.strip() and from_addr)

    async def send_email(
        self,
        *,
        to_email: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> None:
        """Build and send a multipart plain/HTML email via configured SMTP."""
        if not self.is_configured:
            raise EmailDeliveryError("Email delivery is not configured on this server.")

        from_addr = (self.settings.smtp_from_email or self.settings.smtp_username).strip()
        from_name = (self.settings.smtp_from_name or "Smish").strip()

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((from_name, from_addr))
        msg["To"] = to_email
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")

        try:
            await asyncio.to_thread(_send_smtp_sync, self.settings, msg)
        except Exception as exc:
            log.warning("SMTP send failed for %s (%s)", to_email, type(exc).__name__)
            raise EmailDeliveryError(
                "Could not deliver email right now. Please try again."
            ) from exc

    async def send_verification_email(
        self,
        *,
        to_email: str,
        name: str,
        username: str | None,
        token: str,
    ) -> None:
        """Send an account email-verification link."""
        url = build_auth_action_url("verify-email", token, self.settings)
        display_name = name.strip() or username or to_email
        hours = self.settings.email_verify_token_expire_hours
        handle_line = f"Username: @{username}\n" if username else ""
        handle_html = (
            f"<p style='margin:0 0 12px;color:#574b4e;'>Username: "
            f"<strong>@{html.escape(username)}</strong></p>"
            if username
            else ""
        )

        text_body = (
            f"Hi {display_name},\n\n"
            "Verify your email address to activate your Smish account.\n"
            f"{handle_line}\n"
            f"Verify your email: {url}\n\n"
            f"This link expires in {hours} hours.\n"
            "If you did not register this account, you can ignore this email.\n"
        )
        html_body = _wrap_email_html(
            title="Verify your email address",
            greeting=f"Hi {html.escape(display_name)},",
            intro=(
                "Confirm that this email address belongs to you to finish setting up your "
                "Smish account."
            ),
            meta_html=handle_html,
            cta_label="Verify email address",
            cta_url=url,
            footer=(
                f"This verification link expires in {hours} hours. "
                "If you did not create this account, you can safely ignore this message."
            ),
        )
        await self.send_email(
            to_email=to_email,
            subject="Verify your Smish email address",
            text_body=text_body,
            html_body=html_body,
        )

    async def send_password_reset_email(
        self,
        *,
        to_email: str,
        name: str,
        username: str | None,
        token: str,
    ) -> None:
        """Send a password reset link along with account username reminder."""
        url = build_auth_action_url("reset-password", token, self.settings)
        display_name = name.strip() or username or to_email
        minutes = self.settings.password_reset_token_expire_min
        handle_line = f"Your sign-in username: @{username}\n" if username else ""
        handle_html = (
            f"<p style='margin:0 0 12px;color:#574b4e;'>Your sign-in ID: "
            f"<strong>@{html.escape(username)}</strong> "
            f"({html.escape(to_email)})</p>"
            if username
            else (
                f"<p style='margin:0 0 12px;color:#574b4e;'>Account email: "
                f"<strong>{html.escape(to_email)}</strong></p>"
            )
        )

        text_body = (
            f"Hi {display_name},\n\n"
            "We received a request to reset your Smish password or recover your sign-in ID.\n"
            f"{handle_line}"
            f"Account email: {to_email}\n\n"
            f"Reset your password: {url}\n\n"
            f"This link expires in {minutes} minutes and can only be used once.\n"
            "If you did not request this, you can safely ignore this email.\n"
        )
        html_body = _wrap_email_html(
            title="Reset your password",
            greeting=f"Hi {html.escape(display_name)},",
            intro=(
                "We received a request to recover your sign-in ID or reset your password for Smish."
            ),
            meta_html=handle_html,
            cta_label="Reset password",
            cta_url=url,
            footer=(
                f"This link expires in {minutes} minutes and can only be used once. "
                "If you did not request a password reset, you can safely ignore this email."
            ),
        )
        await self.send_email(
            to_email=to_email,
            subject="Reset your Smish password",
            text_body=text_body,
            html_body=html_body,
        )


def _wrap_email_html(
    *,
    title: str,
    greeting: str,
    intro: str,
    meta_html: str,
    cta_label: str,
    cta_url: str,
    footer: str,
) -> str:
    safe_url = html.escape(cta_url, quote=True)
    return (
        "<!doctype html><html><body style=\"margin:0;padding:24px;background:#faf7f4;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;"
        "color:#241c1e;\">"
        "<div style=\"max-width:520px;margin:0 auto;background:#ffffff;border:1px solid #e7dfd8;"
        "border-radius:14px;padding:28px;\">"
        "<div style=\"font-size:13px;font-weight:700;letter-spacing:.04em;color:#663357;"
        "text-transform:uppercase;margin-bottom:8px;\">Smish</div>"
        f"<h1 style=\"font-size:20px;margin:0 0 12px;color:#241c1e;\">{html.escape(title)}</h1>"
        f"<p style=\"margin:0 0 10px;line-height:1.5;\">{greeting}</p>"
        f"<p style=\"margin:0 0 14px;line-height:1.5;color:#4a3f42;\">{html.escape(intro)}</p>"
        f"{meta_html}"
        "<div style=\"margin:22px 0;\">"
        f"<a href=\"{safe_url}\" style=\"display:inline-block;background:#663357;"
        "color:#ffffff;text-decoration:none;font-weight:600;font-size:14px;"
        f"padding:11px 20px;border-radius:10px;\">{html.escape(cta_label)}</a></div>"
        "<p style=\"margin:0 0 12px;font-size:12px;color:#6e6165;word-break:break-all;\">"
        f"Or copy this link into your browser:<br>{safe_url}</p>"
        f"<p style=\"margin:0;font-size:12px;color:#6e6165;line-height:1.45;\">"
        f"{html.escape(footer)}</p>"
        "</div></body></html>"
    )


def get_mailer() -> Mailer:
    """FastAPI dependency returning the application SMTP mailer."""
    return Mailer(get_settings())
