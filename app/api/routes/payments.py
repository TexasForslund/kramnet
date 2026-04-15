import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_customer
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.email_account import EmailAccount
from app.models.payment import Payment, PaymentStatus

from app.services.email_service import EmailService

# Portal-facing router (registreras utan prefix i main.py)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_email_service = EmailService()

_PRICES = {
    "single": 14900,
    "family": 24900,
}


async def _get_owned_account(
    account_id: uuid.UUID,
    customer: Customer,
    db: AsyncSession,
) -> EmailAccount:
    result = await db.execute(
        select(EmailAccount).where(
            EmailAccount.id == account_id,
            EmailAccount.customer_id == customer.id,
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Kontot hittades inte")
    return account


# ─── GET pay page ─────────────────────────────────────────────────────────────

@router.get("/portal/account/{account_id}/pay")
async def pay_page(
    request: Request,
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)

    # Hämta senaste pending-betalning, eller skapa ny
    result = await db.execute(
        select(Payment)
        .where(
            Payment.email_account_id == account.id,
            Payment.status.in_([PaymentStatus.pending, PaymentStatus.pending_verification]),
        )
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    payment = result.scalar_one_or_none()

    if payment is None:
        # Skapa ny betalning (förnyelse)
        from app.models.payment import PaymentType
        amount_ore = _PRICES.get(account.package_type.value, 14900)
        payment = Payment(
            email_account_id=account.id,
            amount_ore=amount_ore,
            payment_type=PaymentType.renewal,
            status=PaymentStatus.pending,
            created_at=datetime.now(timezone.utc),
        )
        db.add(payment)
        await db.commit()
        await db.refresh(payment)

    return RedirectResponse(f"/checkout?order_id={payment.id}", status_code=303)


# ─── POST payment-sent ────────────────────────────────────────────────────────

@router.post(
    "/portal/account/{account_id}/payment-sent",
    response_class=HTMLResponse,
)
async def payment_sent(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)

    result = await db.execute(
        select(Payment)
        .where(
            Payment.email_account_id == account.id,
            Payment.status == PaymentStatus.pending,
        )
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        return HTMLResponse(
            '<div id="payment-action" class="notice">'
            'Ingen väntande betalning hittades. Kontakta oss om problemet kvarstår.'
            '</div>'
        )

    payment_ref = str(payment.id).replace("-", "")[:8].upper()
    payment.status = PaymentStatus.pending_verification

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="payment_reported",
        metadata_={
            "payment_id": str(payment.id),
            "ref": payment_ref,
            "amount_ore": payment.amount_ore,
        },
        performed_by=customer.contact_email,
    ))
    await db.flush()

    await _email_service.send_admin_notification(
        event_type="payment_reported",
        details={
            "kund": customer.name,
            "konto": account.address,
            "belopp": f"{payment.amount_ore // 100} kr",
            "referens": payment_ref,
            "payment_id": str(payment.id),
            "swish": customer.swish_phone,
        },
        db=db,
    )
    await db.commit()

    return HTMLResponse(
        '<div id="payment-action" class="notice">'
        '<strong>Tack!</strong> Vi har tagit emot din betalningsrapport '
        'och verifierar den inom kort. Du får ett mejl när kontot är aktiverat.'
        '</div>'
    )
