import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.core.dependencies import get_current_customer
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.deletion_request import DeletionRequest, DeletionStatus
from app.models.email_account import AccountStatus, EmailAccount
from app.models.payment import Payment  # noqa: F401 — behövs för selectinload
from app.services.email_service import EmailService
from app.services.hostek_service import HostekService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_email_service = EmailService()
_hostek = HostekService()

_PREFIX_RE = re.compile(r"^[a-z0-9.\-]+$")


# ─── helpers ────────────────────────────────────────────────────────────────

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


# ─── root / login ────────────────────────────────────────────────────────────

@router.get("/")
async def root(request: Request):
    if request.cookies.get("session"):
        return RedirectResponse("/portal/dashboard", status_code=302)
    return RedirectResponse("/auth/login", status_code=302)


@router.get("/auth/login")
async def login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "auth/login.html",
        {"request": request, "error": error},
    )


# ─── dashboard ───────────────────────────────────────────────────────────────

@router.get("/portal/dashboard")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    result = await db.execute(
        select(EmailAccount)
        .options(selectinload(EmailAccount.payments))
        .where(EmailAccount.customer_id == customer.id)
        .order_by(EmailAccount.created_at.asc())
    )
    email_accounts = result.scalars().all()

    return templates.TemplateResponse(
        "portal/dashboard.html",
        {
            "request": request,
            "customer": customer,
            "email_accounts": email_accounts,
        },
    )


# ─── settings ────────────────────────────────────────────────────────────────

@router.get("/portal/settings")
async def settings_page(
    request: Request,
    customer: Customer = Depends(get_current_customer),
):
    return templates.TemplateResponse(
        "portal/settings.html",
        {"request": request, "customer": customer},
    )


@router.post("/portal/settings", response_class=HTMLResponse)
async def settings_save(
    request: Request,
    name: str = Form(...),
    swish_phone: str = Form(...),
    language: str = Form(...),
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    if language not in ("sv", "en"):
        language = "sv"

    customer.name = name.strip()
    customer.swish_phone = swish_phone.strip()
    customer.language = language

    db.add(AuditLog(
        customer_id=customer.id,
        event_type="customer_updated",
        metadata_={"fields": ["name", "swish_phone", "language"]},
        performed_by=customer.contact_email,
    ))
    await db.commit()

    return HTMLResponse(
        '<div id="settings-form" class="notice">Dina uppgifter har sparats.</div>'
    )


# ─── change password ─────────────────────────────────────────────────────────

@router.get("/portal/account/{account_id}/change-password")
async def change_password_page(
    request: Request,
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)
    return templates.TemplateResponse(
        "portal/change_password.html",
        {"request": request, "account": account},
    )


@router.post(
    "/portal/account/{account_id}/change-password",
    response_class=HTMLResponse,
)
async def change_password_save(
    account_id: uuid.UUID,
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)

    if new_password != confirm_password:
        return HTMLResponse(
            '<div id="pw-form" class="error">Lösenorden matchar inte.</div>',
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    await _hostek.change_password(account.address, new_password)

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="password_changed",
        performed_by=customer.contact_email,
    ))
    await db.commit()

    return HTMLResponse(
        '<div id="pw-form" class="notice">Lösenordet har bytts.</div>'
    )


# ─── change address ───────────────────────────────────────────────────────────

@router.get("/portal/account/{account_id}/change-address")
async def change_address_page(
    request: Request,
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)
    return templates.TemplateResponse(
        "portal/change_address.html",
        {"request": request, "account": account, "error": None},
    )


@router.post(
    "/portal/account/{account_id}/change-address",
    response_class=HTMLResponse,
)
async def change_address_save(
    request: Request,
    account_id: uuid.UUID,
    new_prefix: str = Form(...),
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)

    new_prefix = new_prefix.strip().lower()
    if not _PREFIX_RE.match(new_prefix):
        return templates.TemplateResponse(
            "portal/change_address.html",
            {
                "request": request,
                "account": account,
                "error": "Ogiltig adress — endast a–z, 0–9, punkt och bindestreck är tillåtna.",
            },
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    new_address = f"{new_prefix}@kramnet.se"

    # Kontrollera att adressen inte är tagen
    taken = await db.execute(
        select(EmailAccount).where(EmailAccount.address == new_address)
    )
    if taken.scalar_one_or_none() is not None:
        return templates.TemplateResponse(
            "portal/change_address.html",
            {
                "request": request,
                "account": account,
                "error": f"{new_address} är redan tagen.",
            },
            status_code=status.HTTP_409_CONFLICT,
        )

    old_address = account.address
    await _hostek.change_address(old_address, new_address)
    account.address = new_address

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="prefix_changed",
        metadata_={"old": old_address, "new": new_address},
        performed_by=customer.contact_email,
    ))
    await db.commit()

    return HTMLResponse(
        f'<div id="addr-form" class="notice">'
        f'Adressen har bytts till <strong>{new_address}</strong>.'
        f'</div>'
    )


# ─── request deletion ─────────────────────────────────────────────────────────

@router.post(
    "/portal/account/{account_id}/request-deletion",
    response_class=HTMLResponse,
)
async def request_deletion(
    account_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    account = await _get_owned_account(account_id, customer, db)

    # Kontrollera att det inte redan finns en pending-begäran
    existing = await db.execute(
        select(DeletionRequest).where(
            DeletionRequest.email_account_id == account.id,
            DeletionRequest.status == DeletionStatus.pending,
        )
    )
    if existing.scalar_one_or_none() is not None:
        return HTMLResponse(
            f'<span id="delete-btn-{account.id}" class="badge badge-pending">'
            f'Raderingsbegäran redan inskickad</span>'
        )

    now = datetime.now(timezone.utc)
    client_ip = request.client.host if request.client else None

    db.add(DeletionRequest(
        email_account_id=account.id,
        status=DeletionStatus.pending,
        requested_by_ip=client_ip,
        requested_at=now,
        scheduled_delete_at=now + timedelta(days=30),
    ))

    account.status = AccountStatus.pending_deletion

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="deletion_requested",
        metadata_={"ip": client_ip},
        performed_by=customer.contact_email,
    ))
    await db.flush()

    await _email_service.send_deletion_request_received(customer, account, db)
    await _email_service.send_admin_notification(
        event_type="deletion_requested",
        details={
            "address": account.address,
            "customer": customer.name,
            "customer_email": customer.contact_email,
            "scheduled_delete_at": str(now + timedelta(days=30)),
        },
        db=db,
    )
    await db.commit()

    return HTMLResponse(
        f'<span id="delete-btn-{account.id}" class="badge badge-pending">'
        f'Raderingsbegäran inskickad</span>'
    )
