import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.api.routes import accounts, admin, customers, payments
from app.api.routes import auth as auth_router
from app.api.routes import portal as portal_router
from app.api.routes import register as register_router
from app.core.config import settings
from app.core.dependencies import get_current_customer  # noqa: F401 — re-exporteras
from app.services.email_service import EmailService
from app.services.scheduler import SchedulerService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)

_scheduler = SchedulerService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _scheduler.start()
    yield
    _scheduler.shutdown()


app = FastAPI(
    title="Kramnet E-posttjänst",
    description="API för kramnet.se e-posttjänst",
    version="1.0.0",
    debug=settings.debug,
    lifespan=lifespan,
)

templates = Jinja2Templates(directory="app/templates")

# Browser-facing routes (ingen /api-prefix)
app.include_router(auth_router.router, prefix="/auth", tags=["auth"])
app.include_router(portal_router.router, tags=["portal"])
app.include_router(register_router.router, tags=["register"])
app.include_router(admin.router, tags=["admin"])

# REST API routes
app.include_router(customers.router, prefix="/api/customers", tags=["customers"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(payments.router, prefix="/api/payments", tags=["payments"])


def get_email_service() -> EmailService:
    return EmailService()


@app.get("/health", tags=["system"])
async def health_check():
    return {"status": "ok"}
