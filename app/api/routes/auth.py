from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_customer
from app.core.limiter import limiter
from app.models.customer import Customer
from app.services.auth_service import AuthService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
_auth_service = AuthService()


@router.post("/request-link", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def request_link(
    request: Request,
    email: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    base_url = str(request.base_url).rstrip("/")
    await _auth_service.generate_magic_link(email.strip().lower(), db, base_url)
    return HTMLResponse(
        '<p id="magic-form" class="notice" style="padding:1rem;background:#e6f4ea;border-radius:8px;color:#155724;">'
        'Kolla din e-post — länken är giltig i 30 minuter.</p>'
    )


@router.get("/verify")
async def verify(
    token: str,
    db: AsyncSession = Depends(get_db),
):
    customer = await _auth_service.verify_token(token, db)
    if customer is None:
        return RedirectResponse("/auth/login?error=invalid_token", status_code=302)

    session_token = _auth_service.create_session_token(customer.id)
    # Första gången (inget lösenord satt) → be kunden sätta lösenord
    destination = "/auth/set-password" if not customer.password_hash else "/portal/dashboard"
    response = RedirectResponse(destination, status_code=302)
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.post("/login-password")
@limiter.limit("10/minute")
async def login_password(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    customer = await _auth_service.verify_password_login(
        email.strip().lower(), password, db
    )
    if customer is None:
        return templates.TemplateResponse(
            "auth/login.html",
            {"request": request, "password_error": "Fel e-postadress eller lösenord."},
            status_code=401,
        )
    session_token = _auth_service.create_session_token(customer.id)
    response = RedirectResponse("/portal/dashboard", status_code=302)
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=60 * 60 * 24 * 7,
    )
    return response


@router.get("/set-password")
async def set_password_page(
    request: Request,
    customer: Customer = Depends(get_current_customer),
):
    return templates.TemplateResponse(
        "auth/set_password.html",
        {"request": request, "customer": customer},
    )


@router.post("/set-password")
async def set_password_submit(
    request: Request,
    password: str = Form(...),
    password_confirm: str = Form(...),
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
):
    if len(password) < 8:
        return templates.TemplateResponse(
            "auth/set_password.html",
            {"request": request, "customer": customer,
             "error": "Lösenordet måste vara minst 8 tecken."},
            status_code=422,
        )
    if password != password_confirm:
        return templates.TemplateResponse(
            "auth/set_password.html",
            {"request": request, "customer": customer,
             "error": "Lösenorden matchar inte."},
            status_code=422,
        )
    await _auth_service.set_password(customer, password, db)
    return RedirectResponse("/portal/dashboard", status_code=302)


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("session")
    return response
