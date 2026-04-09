import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.email_account import EmailAccount

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates" / "emails"

_SUBJECTS: dict[str, dict[str, str]] = {
    "magic_link": {
        "sv": "Din inloggningslänk till Kramnet",
        "en": "Your Kramnet login link",
    },
    "welcome": {
        "sv": "Välkommen till Kramnet!",
        "en": "Welcome to Kramnet!",
    },
    "renewal_reminder": {
        "sv": "Ditt Kramnet-konto går snart ut",
        "en": "Your Kramnet account is expiring soon",
    },
    "deactivation": {
        "sv": "Ditt Kramnet-konto har inaktiverats",
        "en": "Your Kramnet account has been deactivated",
    },
    "reactivation": {
        "sv": "Ditt Kramnet-konto är återaktiverat",
        "en": "Your Kramnet account has been reactivated",
    },
    "deletion_request_received": {
        "sv": "Vi har tagit emot din begäran om radering",
        "en": "We have received your deletion request",
    },
}

_DELETION_REQUEST_BODY: dict[str, str] = {
    "sv": (
        "Hej {name},\n\n"
        "Vi har tagit emot din begäran om att radera kontot {address}.\n\n"
        "Raderingen sker inom 30 dagar. Du kan kontakta oss om du ångrar dig.\n\n"
        "---\nHälsningar,\nKramnet-teamet\nkramnet@broadviewab.se"
    ),
    "en": (
        "Hi {name},\n\n"
        "We have received your request to delete the account {address}.\n\n"
        "Deletion will occur within 30 days. Contact us if you change your mind.\n\n"
        "---\nBest regards,\nThe Kramnet Team\nkramnet@broadviewab.se"
    ),
}


class EmailService:
    def __init__(self) -> None:
        self._jinja = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=False,
        )

    def _render(self, template_name: str, **ctx) -> str:
        return self._jinja.get_template(template_name).render(**ctx)

    async def _send(
        self,
        to: str,
        subject: str,
        body: str,
        db: AsyncSession | None = None,
        audit_customer_id=None,
        audit_account_id=None,
    ) -> bool:
        payload = {
            "From": "Kramnet <noreply@kramnet.se>",
            "To": to,
            "Subject": subject,
            "TextBody": body,
            "MessageStream": "outbound",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    "https://api.postmarkapp.com/email",
                    json=payload,
                    headers={
                        "X-Postmark-Server-Token": settings.postmark_api_key,
                        "Accept": "application/json",
                    },
                )
                response.raise_for_status()

            if db is not None:
                db.add(
                    AuditLog(
                        email_account_id=audit_account_id,
                        customer_id=audit_customer_id,
                        event_type="email_sent",
                        metadata_={"to": to, "subject": subject},
                        performed_by="system",
                    )
                )
                await db.flush()

            return True

        except httpx.HTTPStatusError as exc:
            logger.error(
                "Postmark API error sending to %s: %s %s",
                to,
                exc.response.status_code,
                exc.response.text,
            )
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to, exc)

        return False

    async def send_magic_link(
        self,
        customer: Customer,
        token: str,
        base_url: str,
        db: AsyncSession,
    ) -> bool:
        lang = customer.language or "sv"
        magic_link = f"{base_url}/auth/verify?token={token}"
        body = self._render(
            f"magic_link_{lang}.txt",
            customer=customer,
            magic_link=magic_link,
        )
        return await self._send(
            to=customer.contact_email,
            subject=_SUBJECTS["magic_link"][lang],
            body=body,
            db=db,
            audit_customer_id=customer.id,
        )

    async def send_welcome(
        self,
        customer: Customer,
        email_account: EmailAccount,
        db: AsyncSession,
    ) -> bool:
        lang = customer.language or "sv"
        body = self._render(
            f"welcome_{lang}.txt",
            customer=customer,
            email_account=email_account,
        )
        return await self._send(
            to=customer.contact_email,
            subject=_SUBJECTS["welcome"][lang],
            body=body,
            db=db,
            audit_customer_id=customer.id,
            audit_account_id=email_account.id,
        )

    async def send_renewal_reminder(
        self,
        customer: Customer,
        email_account: EmailAccount,
        days_left: int,
        db: AsyncSession,
    ) -> bool:
        lang = customer.language or "sv"
        body = self._render(
            f"renewal_reminder_{lang}.txt",
            customer=customer,
            email_account=email_account,
            days_left=days_left,
        )
        return await self._send(
            to=customer.contact_email,
            subject=_SUBJECTS["renewal_reminder"][lang],
            body=body,
            db=db,
            audit_customer_id=customer.id,
            audit_account_id=email_account.id,
        )

    async def send_deactivation_notice(
        self,
        customer: Customer,
        email_account: EmailAccount,
        db: AsyncSession,
    ) -> bool:
        lang = customer.language or "sv"
        body = self._render(
            f"deactivation_{lang}.txt",
            customer=customer,
            email_account=email_account,
        )
        return await self._send(
            to=customer.contact_email,
            subject=_SUBJECTS["deactivation"][lang],
            body=body,
            db=db,
            audit_customer_id=customer.id,
            audit_account_id=email_account.id,
        )

    async def send_reactivation_notice(
        self,
        customer: Customer,
        email_account: EmailAccount,
        db: AsyncSession,
    ) -> bool:
        lang = customer.language or "sv"
        body = self._render(
            f"reactivation_{lang}.txt",
            customer=customer,
            email_account=email_account,
        )
        return await self._send(
            to=customer.contact_email,
            subject=_SUBJECTS["reactivation"][lang],
            body=body,
            db=db,
            audit_customer_id=customer.id,
            audit_account_id=email_account.id,
        )

    async def send_deletion_request_received(
        self,
        customer: Customer,
        email_account: EmailAccount,
        db: AsyncSession,
    ) -> bool:
        lang = customer.language or "sv"
        body = _DELETION_REQUEST_BODY[lang].format(
            name=customer.name,
            address=email_account.address,
        )
        return await self._send(
            to=customer.contact_email,
            subject=_SUBJECTS["deletion_request_received"][lang],
            body=body,
            db=db,
            audit_customer_id=customer.id,
            audit_account_id=email_account.id,
        )

    async def send_admin_notification(
        self,
        event_type: str,
        details: dict,
        db: AsyncSession | None = None,
    ) -> bool:
        lines = [f"Kramnet admin-notis: {event_type}", ""]
        for key, value in details.items():
            lines.append(f"  {key}: {value}")
        lines += ["", "---", "Detta är ett automatiskt meddelande från Kramnet."]
        body = "\n".join(lines)

        return await self._send(
            to=settings.admin_email,
            subject=f"[Kramnet] {event_type}",
            body=body,
            db=db,
        )
