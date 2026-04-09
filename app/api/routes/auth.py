from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.auth_service import AuthService

router = APIRouter()
_auth_service = AuthService()


class RequestLinkBody(BaseModel):
    email: EmailStr


@router.post("/request-link", response_class=HTMLResponse)
async def request_link(
    body: RequestLinkBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    base_url = str(request.base_url).rstrip("/")
    # Anropa oavsett utfall — avslöja aldrig om adressen finns
    await _auth_service.generate_magic_link(body.email, db, base_url)
    # HTMX-svar: byt ut formuläret mot bekräftelsetext
    return HTMLResponse(
        '<p id="login-form" class="notice">Kolla din e-post — länken är giltig i 30 minuter.</p>'
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
    response = RedirectResponse("/portal/dashboard", status_code=302)
    response.set_cookie(
        key="session",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=60 * 60 * 24 * 7,  # 7 dagar
    )
    return response


@router.post("/logout")
async def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("session")
    return response
