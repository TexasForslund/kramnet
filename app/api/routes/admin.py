import uuid
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.database import get_db
from app.core.dependencies import require_admin
from app.core.limiter import limiter
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.deletion_request import DeletionRequest, DeletionStatus
from app.models.email_account import AccountStatus, EmailAccount
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.services.email_service import EmailService
from app.services.hostek_service import HostekAPIError, HostekService
from app.services.migration_service import MigrationService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_email_service = EmailService()
_hostek = HostekService()
_migration = MigrationService()


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
@limiter.limit("5/minute")
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

    await _email_service._send(
        to=customer.contact_email,
        subject=subject,
        body=body,
        db=db,
        audit_customer_id=customer.id,
        audit_account_id=account.id,
    )

    await db.commit()

    # HTMX: ta bort raden
    return HTMLResponse(f'<div id="pv-row-{payment_id}" style="display:none;"></div>')


@router.get("/admin/test-email", response_class=PlainTextResponse)
async def admin_test_email(
    _: None = Depends(require_admin),
):
    ok = await _email_service._send(
        to=settings.admin_email,
        subject="[Kramnet] Testmejl",
        body=(
            "Hej!\n\n"
            "Det här är ett testmejl från Kramnet-adminpanelen.\n"
            "Om du ser det här fungerar SMTP-utskicket korrekt.\n\n"
            f"Avsändare: {settings.smtp_from}\n"
            f"Server: {settings.smtp_host}:{settings.smtp_port}\n\n"
            "---\nKramnet"
        ),
    )
    if ok:
        return PlainTextResponse(f"Testmejl skickat till {settings.admin_email}!")
    return PlainTextResponse("Fel: Kunde inte skicka testmejl — se server-loggen.", status_code=500)


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


@router.get("/admin/hostek-debug")
async def admin_hostek_debug(
    _: None = Depends(require_admin),
):
    """Rå debug-anrop mot Hostek API – returnerar statuskod + råsvar som plaintext."""
    import base64

    cid = settings.hostek_customer_id
    did = settings.hostek_domain_id
    base = settings.hostek_api_url.rstrip("/")
    url = f"{base}/customers/{cid}/domains/{did}/email_mailboxes"

    user = settings.hostek_api_user
    password = settings.hostek_api_password
    token = base64.b64encode(f"{user}:{password}".encode()).decode()

    lines = [
        f"URL: {url}",
        f"Auth user: {user!r}",
        f"Authorization: Basic {token[:10]}… (trunkerad)",
        "",
    ]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, auth=(user, password))
        lines.append(f"HTTP Status: {resp.status_code}")
        lines.append(f"Content-Type: {resp.headers.get('content-type', '(saknas)')}")
        lines.append("")
        lines.append("--- RAW BODY (första 2000 tecken) ---")
        lines.append(resp.text[:2000])
    except Exception as exc:
        lines.append(f"EXCEPTION: {type(exc).__name__}: {exc}")

    return PlainTextResponse("\n".join(lines))


@router.get("/admin/migration")
async def admin_migration(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    if not settings.hostek_customer_id:
        return templates.TemplateResponse(
            "admin/migration.html",
            {
                "request": request,
                "preview": None,
                "no_credentials": True,
            },
        )

    preview = None
    error = None
    try:
        preview = await _migration.preview_migration(db, _hostek)
    except HostekAPIError as exc:
        error = str(exc)

    return templates.TemplateResponse(
        "admin/migration.html",
        {
            "request": request,
            "preview": preview,
            "error": error,
            "no_credentials": False,
        },
    )


@router.post("/admin/migration/dry-run", response_class=HTMLResponse)
async def admin_migration_dry_run(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    try:
        result = await _migration.run_migration(db, _hostek, dry_run=True)
    except HostekAPIError as exc:
        return HTMLResponse(
            f'<div class="error">API-fel: {exc}</div>',
            status_code=502,
        )

    return HTMLResponse(_render_migration_result(result))


@router.post("/admin/migration/run", response_class=HTMLResponse)
async def admin_migration_run(
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_admin),
):
    body = await request.json()
    if body.get("confirm") != "IMPORTERA":
        return HTMLResponse(
            '<div class="error">Bekräftelse saknas — skriv IMPORTERA i fältet.</div>',
            status_code=400,
        )

    try:
        result = await _migration.run_migration(db, _hostek, dry_run=False)
    except HostekAPIError as exc:
        return HTMLResponse(
            f'<div class="error">API-fel: {exc}</div>',
            status_code=502,
        )

    return HTMLResponse(_render_migration_result(result))


def _render_migration_result(result: dict) -> str:
    dry = result["dry_run"]
    mode_label = "TESTKÖRNING" if dry else "GENOMFÖRD IMPORT"
    mode_css = "notice" if dry else "badge-active"
    errors_html = ""
    if result["errors"]:
        items = "".join(f"<li>{e}</li>" for e in result["errors"])
        errors_html = f'<ul style="margin:.5rem 0 0 1rem;color:#721c24;">{items}</ul>'

    return f"""
<div class="card" style="border-color:{'#0057b8' if dry else '#27ae60'};">
  <div style="display:flex;align-items:center;gap:.75rem;margin-bottom:.75rem;">
    <span class="badge {mode_css}" style="font-size:.85rem;padding:.3rem .75rem;">{mode_label}</span>
  </div>
  <table style="width:auto;">
    <tr><th style="text-align:right;padding-right:1rem;">Importerade</th>
        <td><strong>{result['imported']}</strong></td></tr>
    <tr><th style="text-align:right;padding-right:1rem;">Hoppades över</th>
        <td>{result['skipped']}</td></tr>
    <tr><th style="text-align:right;padding-right:1rem;">Fel</th>
        <td>{len(result['errors'])}</td></tr>
  </table>
  {errors_html}
</div>
"""


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
