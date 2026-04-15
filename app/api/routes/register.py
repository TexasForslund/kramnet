import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.audit_log import AuditLog
from app.models.customer import Customer
from app.models.email_account import AccountStatus, EmailAccount, PackageType
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.services.email_service import EmailService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_email_service = EmailService()
_PREFIX_RE = re.compile(r"^[a-z0-9.\-]+$")

_PRICES: dict[str, int] = {
    "single": 14900,   # 149 kr
    "family": 24900,   # 249 kr
}


# ─── address availability check ───────────────────────────────────────────────

@router.get("/api/check-address")
async def check_address(
    prefix: str = "",
    db: AsyncSession = Depends(get_db),
):
    """JSON-endpoint för programmatisk användning."""
    if not prefix or not _PREFIX_RE.match(prefix.strip().lower()):
        return JSONResponse({"available": False, "reason": "invalid"})

    address = f"{prefix.strip().lower()}@kramnet.se"
    taken = await db.execute(
        select(EmailAccount).where(EmailAccount.address == address)
    )
    available = taken.scalar_one_or_none() is None
    return JSONResponse({"available": available})


@router.get("/api/check-address-fragment", response_class=HTMLResponse)
async def check_address_fragment(
    desired_prefix: str = "",
    db: AsyncSession = Depends(get_db),
):
    """HTML-fragment för HTMX live-validering i registreringsformuläret."""
    p = desired_prefix.strip().lower()
    if not p:
        return HTMLResponse("")
    if not _PREFIX_RE.match(p):
        return HTMLResponse('<span style="color:#721c24;">Ogiltiga tecken — använd a-z, 0-9, punkt, bindestreck</span>')

    address = f"{p}@kramnet.se"
    taken = await db.execute(
        select(EmailAccount).where(EmailAccount.address == address)
    )
    if taken.scalar_one_or_none() is not None:
        return HTMLResponse(f'<span style="color:#721c24;">&#10007; {address} är redan tagen</span>')

    return HTMLResponse(f'<span style="color:#155724;">&#10003; {address} är ledig</span>')


# ─── registration form ────────────────────────────────────────────────────────

@router.get("/register")
async def register_page(request: Request):
    return templates.TemplateResponse(
        "register/index.html",
        {"request": request, "error": None},
    )


@router.post("/register")
async def register_submit(
    request: Request,
    name: str = Form(...),
    contact_email: str = Form(...),
    desired_prefix: str = Form(...),
    swish_phone: str = Form(default=""),
    package_type: str = Form(...),
    language: str = Form("sv"),
    db: AsyncSession = Depends(get_db),
):
    desired_prefix = desired_prefix.strip().lower()

    # Validera prefix
    if not _PREFIX_RE.match(desired_prefix):
        return templates.TemplateResponse(
            "register/index.html",
            {
                "request": request,
                "error": "Ogiltig adress — endast a–z, 0–9, punkt och bindestreck är tillåtna.",
                "form": {
                    "name": name,
                    "contact_email": contact_email,
                    "desired_prefix": desired_prefix,
                    "package_type": package_type,
                    "language": language,
                },
            },
            status_code=422,
        )

    if package_type not in ("single", "family"):
        package_type = "single"
    if language not in ("sv", "en"):
        language = "sv"

    address = f"{desired_prefix}@kramnet.se"

    # Kontrollera att adressen inte är tagen
    taken = await db.execute(
        select(EmailAccount).where(EmailAccount.address == address)
    )
    if taken.scalar_one_or_none() is not None:
        return templates.TemplateResponse(
            "register/index.html",
            {
                "request": request,
                "error": f"{address} är redan tagen. Välj ett annat prefix.",
                "form": {
                    "name": name,
                    "contact_email": contact_email,
                    "desired_prefix": desired_prefix,
                    "package_type": package_type,
                    "language": language,
                },
            },
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    pkg = PackageType(package_type)
    amount_ore = _PRICES[package_type]

    customer = Customer(
        name=name.strip(),
        contact_email=contact_email.strip().lower(),
        swish_phone=swish_phone.strip(),
        language=language,
    )
    db.add(customer)
    await db.flush()  # ger customer.id

    account = EmailAccount(
        customer_id=customer.id,
        address=address,
        package_type=pkg,
        status=AccountStatus.inactive,
        created_at=now,
    )
    db.add(account)
    await db.flush()  # ger account.id

    payment = Payment(
        email_account_id=account.id,
        amount_ore=amount_ore,
        payment_type=PaymentType.new,
        status=PaymentStatus.pending,
        created_at=now,
    )
    db.add(payment)

    db.add(AuditLog(
        customer_id=customer.id,
        email_account_id=account.id,
        event_type="account_created",
        metadata_={
            "address": address,
            "package": package_type,
            "amount_ore": amount_ore,
        },
        performed_by="system",
    ))
    await db.flush()

    await _email_service.send_admin_notification(
        event_type="new_registration",
        details={
            "name": customer.name,
            "contact_email": customer.contact_email,
            "address": address,
            "package": package_type,
            "amount_kr": f"{amount_ore / 100:.0f} kr",
            "payment_id": str(payment.id),
        },
        db=db,
    )

    await db.commit()

    return RedirectResponse(f"/checkout?order_id={payment.id}", status_code=303)
