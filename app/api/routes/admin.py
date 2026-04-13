import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_admin
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.deletion_request import DeletionRequest, DeletionStatus
from app.models.email_account import AccountStatus, EmailAccount
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.services.email_service import EmailService
from app.services.hostek_service import HostekAPIError, HostekService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_email_service = EmailService()
_hostek = HostekService()


# ─── helpers ──────────────────────────────────────────────────────────────────

async def _get_account(account_id: uuid.UUID, db: AsyncSession) -> EmailAccount:
    result = await db.execute(
        select(EmailAccount)
        .options(selectinload(EmailAccount.customer))
        .where(EmailAccount.id == account_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Kontot hittades inte")
    return account


def _status_fragment(account: EmailAccount) -> str:
    """Returnerar HTML-fragment med badge + action-knappar för ett konto."""
    _map = {
        AccountStatus.active:           ("badge-active",   "Aktiv"),
        AccountStatus.inactive:         ("badge-inactive", "Inaktiv"),
        AccountStatus.pending_deletion: ("badge-pending",  "Inväntar radering"),
        AccountStatus.deleted:          ("badge-pending",  "Raderat"),
    }
    css, label = _map.get(account.status, ("", account.status.value))
    aid = account.id

    activate = (
        ""
        if account.status == AccountStatus.active
        else (
            f'<button type="button" class="btn-sm btn-ok" '
            f'hx-post="/admin/account/{aid}/activate" '
            f'hx-target="#status-{aid}" hx-swap="outerHTML">'
            f"Aktivera</button>"
        )
    )
    deactivate = (
        ""
        if account.status != AccountStatus.active
        else (
            f'<button type="button" class="btn-sm btn-warn" '
            f'hx-post="/admin/account/{aid}/deactivate" '
            f'hx-target="#status-{aid}" hx-swap="outerHTML">'
            f"Inaktivera</button>"
        )
    )

    return (
        f'<span id="status-{aid}" '
        f'style="display:inline-flex;gap:.4rem;align-items:center;flex-wrap:wrap;">'
        f'<span class="badge {css}">{label}</span>'
        f"{activate}{deactivate}"
        f"</span>"
    )


# ─── root ─────────────────────────────────────────────────────────────────────

@router.get("/admin")
async def admin_root(_: None = Depends(require_admin)):
    return RedirectResponse("/admin/dashboard", status_code=302)


# ─── login / logout ───────────────────────────────────────────────────────────

@router.get("/admin/login")
async def admin_login_page(request: Request):
    error = request.query_params.get("error")
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "error": error},
    )


@router.post("/admin/login")
async def admin_login(
    request: Request,
    password: str = Form(...),
):
    if password != settings.admin_secret:
        return templates.TemplateResponse(
            "admin/login.html",
            {"request": request, "error": "Fel lösenord."},
            status_code=401,
        )
    response = RedirectResponse("/admin/dashboard", status_code=302)
    response.set_cookie(
        key="admin_session",
        value=settings.admin_secret,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 8,  # 8 timmar
    )
    return response


@router.post("/admin/logout")
async def admin_logout():
    response = RedirectResponse("/admin/login", status_code=302)
    response.delete_cookie("admin_session")
    return response


# ─── dashboard ────────────────────────────────────────────────────────────────

@router.get("/admin/dashboard")
async def admin_dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

    total_customers = await db.scalar(select(func.count(Customer.id)))
    active_accounts = await db.scalar(
        select(func.count(EmailAccount.id)).where(
            EmailAccount.status == AccountStatus.active
        )
    )
    inactive_accounts = await db.scalar(
        select(func.count(EmailAccount.id)).where(
            EmailAccount.status == AccountStatus.inactive
        )
    )
    pending_deletion = await db.scalar(
        select(func.count(EmailAccount.id)).where(
            EmailAccount.status == AccountStatus.pending_deletion
        )
    )
    payments_count = await db.scalar(
        select(func.count(Payment.id)).where(
            Payment.status == PaymentStatus.paid,
            Payment.paid_at >= thirty_days_ago,
        )
    )
    payments_sum_ore = await db.scalar(
        select(func.coalesce(func.sum(Payment.amount_ore), 0)).where(
            Payment.status == PaymentStatus.paid,
            Payment.paid_at >= thirty_days_ago,
        )
    )
    recent_audit = (
        await db.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "stats": {
                "total_customers": total_customers or 0,
                "active_accounts": active_accounts or 0,
                "inactive_accounts": inactive_accounts or 0,
                "pending_deletion": pending_deletion or 0,
                "payments_count": payments_count or 0,
                "payments_sum_kr": (payments_sum_ore or 0) / 100,
            },
            "recent_audit": recent_audit,
        },
    )


# ─── customers ────────────────────────────────────────────────────────────────

@router.get("/admin/customers")
async def admin_customers(
    request: Request,
    search: str = "",
    status: str = "all",
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    query = (
        select(Customer)
        .options(selectinload(Customer.email_accounts))
        .order_by(Customer.created_at.desc())
    )
    if search:
        query = query.where(
            or_(
                Customer.name.ilike(f"%{search}%"),
                Customer.contact_email.ilike(f"%{search}%"),
            )
        )

    customers_all = (await db.execute(query)).scalars().all()

    if status != "all":
        try:
            filter_status = AccountStatus(status)
            customers_all = [
                c for c in customers_all
                if any(a.status == filter_status for a in c.email_accounts)
            ]
        except ValueError:
            pass

    return templates.TemplateResponse(
        "admin/customers.html",
        {
            "request": request,
            "customers": customers_all,
            "search": search,
            "status_filter": status,
        },
    )


# ─── customer detail ──────────────────────────────────────────────────────────

@router.get("/admin/customers/{customer_id}")
async def admin_customer_detail(
    request: Request,
    customer_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    result = await db.execute(
        select(Customer)
        .options(
            selectinload(Customer.email_accounts).selectinload(EmailAccount.payments)
        )
        .where(Customer.id == customer_id)
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        raise HTTPException(status_code=404, detail="Kunden hittades inte")

    audit_logs = (
        await db.execute(
            select(AuditLog)
            .where(AuditLog.customer_id == customer_id)
            .order_by(AuditLog.created_at.desc())
            .limit(50)
        )
    ).scalars().all()

    return templates.TemplateResponse(
        "admin/customer_detail.html",
        {
            "request": request,
            "customer": customer,
            "audit_logs": audit_logs,
            "status_fragment": _status_fragment,
        },
    )


# ─── account actions ──────────────────────────────────────────────────────────

@router.post("/admin/account/{account_id}/activate", response_class=HTMLResponse)
async def admin_activate(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    account = await _get_account(account_id, db)
    now = datetime.now(timezone.utc)

    account.status = AccountStatus.active
    account.activated_at = now
    account.expires_at = now + timedelta(days=365)

    db.add(AuditLog(
        customer_id=account.customer_id,
        email_account_id=account.id,
        event_type="account_activated",
        performed_by="admin",
    ))
    await _hostek.activate_account(account.address)
    await db.flush()
    await _email_service.send_reactivation_notice(account.customer, account, db)
    await db.commit()

    return HTMLResponse(_status_fragment(account))


@router.post("/admin/account/{account_id}/deactivate", response_class=HTMLResponse)
async def admin_deactivate(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    account = await _get_account(account_id, db)

    account.status = AccountStatus.inactive
    account.deactivated_at = datetime.now(timezone.utc)

    db.add(AuditLog(
        customer_id=account.customer_id,
        email_account_id=account.id,
        event_type="account_deactivated",
        performed_by="admin",
    ))
    await _hostek.deactivate_account(account.address)
    await db.commit()

    return HTMLResponse(_status_fragment(account))


@router.post("/admin/account/{account_id}/extend", response_class=HTMLResponse)
async def admin_extend(
    account_id: uuid.UUID,
    days: int = Form(...),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    if days < 1 or days > 3650:
        raise HTTPException(status_code=422, detail="Ogiltigt antal dagar")

    account = await _get_account(account_id, db)
    base = account.expires_at or datetime.now(timezone.utc)
    account.expires_at = base + timedelta(days=days)

    db.add(AuditLog(
        customer_id=account.customer_id,
        email_account_id=account.id,
        event_type="admin_override",
        metadata_={"action": "extended", "days": days},
        performed_by="admin",
    ))
    await db.commit()

    return HTMLResponse(
        f'<span id="expires-{account.id}">'
        f'{account.expires_at.strftime("%Y-%m-%d")}'
        f'</span>'
    )


# ─── deletion requests ────────────────────────────────────────────────────────

@router.get("/admin/deletion-requests")
async def admin_deletion_requests(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    result = await db.execute(
        select(DeletionRequest)
        .options(
            selectinload(DeletionRequest.email_account).selectinload(
                EmailAccount.customer
            )
        )
        .where(DeletionRequest.status == DeletionStatus.pending)
        .order_by(DeletionRequest.requested_at.asc())
    )
    deletion_requests = result.scalars().all()

    return templates.TemplateResponse(
        "admin/deletion_requests.html",
        {"request": request, "deletion_requests": deletion_requests},
    )


# ─── payment confirm ─────────────────────────────────────────────────────────

@router.post("/admin/payment/{payment_id}/confirm", response_class=HTMLResponse)
async def admin_confirm_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
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

    now = datetime.now(timezone.utc)
    account = payment.email_account
    customer = account.customer

    payment.status = PaymentStatus.paid
    payment.paid_at = now

    account.status = AccountStatus.active
    account.activated_at = now
    account.expires_at = now + timedelta(days=365)

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="account_activated",
        metadata_={"payment_id": str(payment_id), "amount_ore": payment.amount_ore},
        performed_by="admin",
    ))

    await _hostek.create_account(account.address, "temp-password-set-by-customer")
    await db.flush()
    await _email_service.send_welcome(customer, account, db)
    await db.commit()

    return HTMLResponse(
        f'<span id="payment-status-{payment_id}" '
        f'style="display:inline-flex;gap:.5rem;align-items:center;">'
        f'<span class="badge badge-active">Betald &amp; aktiverad</span>'
        f'<small style="color:#666;">{now.strftime("%Y-%m-%d %H:%M")}</small>'
        f'</span>'
    )


@router.post("/admin/payment/{payment_id}/reject", response_class=HTMLResponse)
async def admin_reject_payment(
    payment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
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

    account = payment.email_account
    customer = account.customer

    payment.status = PaymentStatus.failed

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="payment_rejected",
        metadata_={"payment_id": str(payment_id)},
        performed_by="admin",
    ))
    await db.flush()

    await _email_service.send_admin_notification(
        event_type="payment_rejected_customer_notified",
        details={"kund": customer.name, "konto": account.address},
        db=db,
    )

    # Skicka mejl till kunden
    lang = customer.language or "sv"
    body_sv = (
        f"Hej {customer.name},\n\n"
        f"Vi kunde tyvärr inte verifiera din betalning för {account.address}.\n\n"
        f"Kontakta oss på kramnet@broadviewab.se så hjälper vi dig.\n\n"
        f"---\nHälsningar,\nKramnet-teamet\nkramnet@broadviewab.se"
    )
    body_en = (
        f"Hi {customer.name},\n\n"
        f"We were unable to verify your payment for {account.address}.\n\n"
        f"Please contact us at kramnet@broadviewab.se and we will assist you.\n\n"
        f"---\nBest regards,\nThe Kramnet Team\nkramnet@broadviewab.se"
    )
    subject_sv = "Vi kunde inte verifiera din betalning – Kramnet"
    subject_en = "We could not verify your payment – Kramnet"
    body = body_sv if lang == "sv" else body_en
    subject = subject_sv if lang == "sv" else subject_en

    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                "https://api.postmarkapp.com/email",
                json={
                    "From": "Kramnet <noreply@kramnet.se>",
                    "To": customer.contact_email,
                    "Subject": subject,
                    "TextBody": body,
                    "MessageStream": "outbound",
                },
                headers={
                    "X-Postmark-Server-Token": settings.postmark_api_key,
                    "Accept": "application/json",
                },
            )
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Failed to send payment rejection email")

    await db.commit()

    # HTMX: ta bort raden
    return HTMLResponse(f'<div id="pv-row-{payment_id}" style="display:none;"></div>')


@router.get("/admin/hostek-test")
async def admin_hostek_test(
    request: Request,
    _: None = Depends(require_admin),
):
    if not settings.hostek_customer_id:
        return templates.TemplateResponse(
            "admin/hostek_test.html",
            {"request": request, "mailboxes": None, "error": None, "no_credentials": True},
        )

    mailboxes = None
    error = None
    try:
        mailboxes = await _hostek.list_mailboxes()
    except HostekAPIError as exc:
        error = str(exc)

    return templates.TemplateResponse(
        "admin/hostek_test.html",
        {
            "request": request,
            "mailboxes": mailboxes[:20] if mailboxes else [],
            "error": error,
            "no_credentials": False,
        },
    )


@router.post(
    "/admin/deletion-requests/{request_id}/approve",
    response_class=HTMLResponse,
)
async def admin_approve_deletion(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    result = await db.execute(
        select(DeletionRequest)
        .options(
            selectinload(DeletionRequest.email_account).selectinload(
                EmailAccount.customer
            )
        )
        .where(DeletionRequest.id == request_id)
    )
    dr = result.scalar_one_or_none()
    if dr is None:
        raise HTTPException(status_code=404)

    now = datetime.now(timezone.utc)
    dr.status = DeletionStatus.approved
    dr.approved_at = now
    dr.approved_by = "admin"

    account = dr.email_account
    account.status = AccountStatus.inactive
    account.deactivated_at = now

    db.add(AuditLog(
        customer_id=account.customer_id,
        email_account_id=account.id,
        event_type="deletion_approved",
        performed_by="admin",
    ))
    await _hostek.deactivate_account(account.address)
    await _email_service.send_admin_notification(
        event_type="deletion_approved",
        details={
            "address": account.address,
            "customer": account.customer.name,
            "approved_at": str(now),
        },
        db=db,
    )
    await db.commit()

    return HTMLResponse(f'<tr id="dr-row-{request_id}" style="display:none;"></tr>')
