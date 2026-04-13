"""
Tests for the admin panel routes.

Uses the test_client fixture (FastAPI + httpx) with admin_session cookie
set to settings.admin_secret, which is "admin123" from the .env file.
Hostek and EmailService calls are mocked so no external I/O occurs.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.models.customer import Customer
from app.models.email_account import AccountStatus, EmailAccount, PackageType

ADMIN_COOKIES = {"admin_session": settings.admin_secret}


# ── helpers ───────────────────────────────────────────────────────────────────

async def _seed_account(
    db,
    status: AccountStatus = AccountStatus.inactive,
    address: str = "admintest@kramnet.se",
    contact_email: str = "admintest@example.com",
) -> EmailAccount:
    """Insert a Customer + EmailAccount and return the account."""
    customer = Customer(
        name="Admin Test",
        contact_email=contact_email,
        swish_phone="0709876543",
        language="sv",
    )
    db.add(customer)
    await db.flush()

    expires = (
        datetime.now(timezone.utc) + timedelta(days=30)
        if status == AccountStatus.active
        else None
    )
    account = EmailAccount(
        customer_id=customer.id,
        address=address,
        package_type=PackageType.single,
        status=status,
        expires_at=expires,
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


# ── login ─────────────────────────────────────────────────────────────────────

async def test_admin_login_correct(test_client):
    resp = await test_client.post(
        "/admin/login",
        data={"password": settings.admin_secret},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/dashboard"


async def test_admin_login_wrong(test_client):
    resp = await test_client.post(
        "/admin/login",
        data={"password": "completely_wrong"},
    )
    assert resp.status_code == 401


# ── account activate / deactivate ─────────────────────────────────────────────

async def test_admin_activate_account(test_client, test_db, test_engine, mocker):
    account = await _seed_account(test_db, status=AccountStatus.inactive)

    mocker.patch("app.api.routes.admin._hostek.activate_account", return_value=True)
    mocker.patch("app.api.routes.admin._email_service.send_reactivation_notice")

    resp = await test_client.post(
        f"/admin/account/{account.id}/activate",
        cookies=ADMIN_COOKIES,
    )

    assert resp.status_code == 200
    assert b"Aktiv" in resp.content

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as verify:
        result = await verify.execute(
            select(EmailAccount).where(EmailAccount.id == account.id)
        )
        updated = result.scalar_one()

    assert updated.status == AccountStatus.active


async def test_admin_deactivate_account(test_client, test_db, test_engine, mocker):
    account = await _seed_account(
        test_db, status=AccountStatus.active, address="active@kramnet.se",
        contact_email="active@example.com",
    )

    mocker.patch("app.api.routes.admin._hostek.deactivate_account", return_value=True)

    resp = await test_client.post(
        f"/admin/account/{account.id}/deactivate",
        cookies=ADMIN_COOKIES,
    )

    assert resp.status_code == 200
    assert b"Inaktiv" in resp.content

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as verify:
        result = await verify.execute(
            select(EmailAccount).where(EmailAccount.id == account.id)
        )
        updated = result.scalar_one()

    assert updated.status == AccountStatus.inactive


# ── account extend ────────────────────────────────────────────────────────────

async def test_admin_extend_account(test_client, test_db, test_engine):
    account = await _seed_account(
        test_db, status=AccountStatus.active, address="extend@kramnet.se",
        contact_email="extend@example.com",
    )
    original_expires = account.expires_at

    resp = await test_client.post(
        f"/admin/account/{account.id}/extend",
        data={"days": "30"},
        cookies=ADMIN_COOKIES,
    )

    assert resp.status_code == 200

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as verify:
        result = await verify.execute(
            select(EmailAccount).where(EmailAccount.id == account.id)
        )
        updated = result.scalar_one()

    assert updated.expires_at > original_expires
