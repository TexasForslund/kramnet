import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.email_account import AccountStatus, EmailAccount
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.services import klarna_service
from app.services.email_service import EmailService
from app.services.hostek_service import HostekService

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_email_service = EmailService()
_hostek = HostekService()


async def _load_payment_with_relations(
    payment_id: uuid.UUID,
    db: AsyncSession,
) -> Payment:
    result = await db.execute(
        select(Payment)
        .options(
            selectinload(Payment.email_account).selectinload(EmailAccount.customer)
        )
        .where(Payment.id == payment_id)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail="Betalningen hittades inte")
    return payment


async def _activate_account(
    payment: Payment,
    db: AsyncSession,
) -> None:
    """Aktivera konto och bekräfta Klarna-order. Idempotent — gör ingenting om redan betald."""
    if payment.status == PaymentStatus.paid:
        return

    now = datetime.now(timezone.utc)
    account: EmailAccount = payment.email_account
    customer: Customer = account.customer

    payment.status = PaymentStatus.paid
    payment.paid_at = now

    account.status = AccountStatus.active
    account.activated_at = now
    account.expires_at = now + timedelta(days=365)

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="account_activated",
        metadata_={
            "payment_id": str(payment.id),
            "amount_ore": payment.amount_ore,
            "klarna_order_id": payment.klarna_order_id,
        },
        performed_by="klarna",
    ))
    await db.flush()

    # Hostek: skapa postlåda för ny registrering, aktivera vid förnyelse
    try:
        if payment.payment_type == PaymentType.new:
            await _hostek.create_account(account.address, "temp-password-set-by-customer")
        else:
            await _hostek.activate_account(account.address)
    except Exception:
        logger.exception("Hostek-anrop misslyckades för %s", account.address)

    # Välkomstmejl (stubbat — email_service hanterar om det saknas)
    try:
        await _email_service.send_welcome(customer, account, db)
    except Exception:
        logger.exception("Välkomstmejl misslyckades för %s", customer.contact_email)

    # Bekräfta mot Klarna
    if payment.klarna_order_id:
        try:
            await klarna_service.acknowledge_order(payment.klarna_order_id)
        except Exception:
            logger.exception("Klarna acknowledge misslyckades för %s", payment.klarna_order_id)

    await db.commit()


# ─── GET /checkout ─────────────────────────────────────────────────────────────

@router.get("/checkout")
async def checkout_page(
    request: Request,
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    payment = await _load_payment_with_relations(order_id, db)
    account: EmailAccount = payment.email_account
    customer: Customer = account.customer

    # Skapa Klarna-order om inte redan gjort
    if not payment.klarna_order_id:
        try:
            klarna_order = await klarna_service.create_order(payment, customer, account)
        except Exception as exc:
            logger.exception("Klarna create_order misslyckades")
            raise HTTPException(
                status_code=502,
                detail=f"Kunde inte skapa betalning hos Klarna: {exc}",
            )
        payment.klarna_order_id = klarna_order["order_id"]
        await db.commit()
        html_snippet = klarna_order["html_snippet"]
    else:
        # Hämta befintlig order (innehåller uppdaterat html_snippet)
        try:
            klarna_order = await klarna_service.get_order(payment.klarna_order_id)
            html_snippet = klarna_order["html_snippet"]
        except Exception:
            logger.exception("Klarna get_order misslyckades")
            raise HTTPException(status_code=502, detail="Kunde inte hämta betalningssidan.")

    return templates.TemplateResponse(
        "checkout/index.html",
        {
            "request": request,
            "payment": payment,
            "account": account,
            "customer": customer,
            "amount_kr": payment.amount_ore // 100,
            "html_snippet": html_snippet,
        },
    )


# ─── GET /checkout/confirmation ────────────────────────────────────────────────

@router.get("/checkout/confirmation")
async def checkout_confirmation(
    request: Request,
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    payment = await _load_payment_with_relations(order_id, db)
    account: EmailAccount = payment.email_account

    if payment.klarna_order_id and payment.status != PaymentStatus.paid:
        try:
            klarna_order = await klarna_service.get_order(payment.klarna_order_id)
            if klarna_order.get("status") == "checkout_complete":
                await _activate_account(payment, db)
        except Exception:
            logger.exception("Klarna get_order/activate misslyckades vid confirmation")

    return templates.TemplateResponse(
        "checkout/confirmation.html",
        {
            "request": request,
            "payment": payment,
            "account": account,
        },
    )


# ─── POST /api/klarna/push ─────────────────────────────────────────────────────

@router.post("/api/klarna/push")
async def klarna_push(
    order_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Klarna push-webhook — anropas av Klarna när betalning är genomförd."""
    try:
        payment = await _load_payment_with_relations(order_id, db)
    except HTTPException:
        # Returnera 200 ändå så Klarna inte försöker igen i onödan
        logger.warning("Push webhook: payment %s hittades inte", order_id)
        return JSONResponse({"ok": True})

    if payment.status == PaymentStatus.paid:
        return JSONResponse({"ok": True})

    if payment.klarna_order_id:
        try:
            klarna_order = await klarna_service.get_order(payment.klarna_order_id)
            if klarna_order.get("status") == "checkout_complete":
                await _activate_account(payment, db)
        except Exception:
            logger.exception("Push webhook: aktivering misslyckades för payment %s", order_id)

    # Alltid 200 — annars försöker Klarna igen
    return JSONResponse({"ok": True})
