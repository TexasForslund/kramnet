from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import get_current_customer
from app.models.customer import Customer
from app.models.email_account import EmailAccount

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


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


@router.get("/portal/dashboard")
async def dashboard(
    request: Request,
    db: AsyncSession = Depends(get_db),
    customer: Customer = Depends(get_current_customer),
):
    result = await db.execute(
        select(EmailAccount)
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
