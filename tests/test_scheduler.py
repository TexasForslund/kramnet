"""
Tests for the scheduler's background-job logic.

Each test patches app.services.scheduler.AsyncSessionLocal with the
test session factory so the functions read/write the in-memory SQLite
database instead of Supabase.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.auth_token import AuthToken
from app.models.customer import Customer
from app.models.email_account import AccountStatus, EmailAccount, PackageType
from app.services.scheduler import (
    cleanup_expired_tokens,
    daily_deactivation_check,
    daily_renewal_check,
)


# ── helpers ───────────────────────────────────────────────────────────────────

async def _customer(db, suffix: str) -> Customer:
    c = Customer(
        name=f"Sched{suffix}",
        contact_email=f"sched{suffix}@test.se",
        swish_phone="0701234567",
        language="sv",
    )
    db.add(c)
    await db.flush()
    return c


async def _account(db, customer_id, address: str, status: AccountStatus, expires_at) -> EmailAccount:
    a = EmailAccount(
        customer_id=customer_id,
        address=address,
        package_type=PackageType.single,
        status=status,
        expires_at=expires_at,
    )
    db.add(a)
    await db.flush()
    return a


def _noon_on(base_dt: datetime, delta_days: int) -> datetime:
    """Return noon UTC on the date that is delta_days from base_dt."""
    d = (base_dt + timedelta(days=delta_days)).date()
    return datetime(d.year, d.month, d.day, 12, 0, tzinfo=timezone.utc)


# ── daily_renewal_check ───────────────────────────────────────────────────────

async def test_renewal_check_30_days(test_db, test_engine, mocker):
    now = datetime.now(timezone.utc)
    customer = await _customer(test_db, "30d")
    await _account(
        test_db, customer.id,
        "expire30@kramnet.se", AccountStatus.active, _noon_on(now, 30),
    )
    await test_db.commit()

    mock_send = mocker.patch(
        "app.services.scheduler._email_service.send_renewal_reminder"
    )
    mocker.patch(
        "app.services.scheduler.AsyncSessionLocal",
        async_sessionmaker(test_engine, expire_on_commit=False),
    )

    await daily_renewal_check()

    assert mock_send.call_count >= 1
    days_values = {c.kwargs["days_left"] for c in mock_send.call_args_list}
    assert 30 in days_values


async def test_renewal_check_7_days(test_db, test_engine, mocker):
    now = datetime.now(timezone.utc)
    customer = await _customer(test_db, "7d")
    await _account(
        test_db, customer.id,
        "expire7@kramnet.se", AccountStatus.active, _noon_on(now, 7),
    )
    await test_db.commit()

    mock_send = mocker.patch(
        "app.services.scheduler._email_service.send_renewal_reminder"
    )
    mocker.patch(
        "app.services.scheduler.AsyncSessionLocal",
        async_sessionmaker(test_engine, expire_on_commit=False),
    )

    await daily_renewal_check()

    assert mock_send.call_count >= 1
    days_values = {c.kwargs["days_left"] for c in mock_send.call_args_list}
    assert 7 in days_values


# ── daily_deactivation_check ──────────────────────────────────────────────────

async def test_deactivation_check(test_db, test_engine, mocker):
    now = datetime.now(timezone.utc)
    customer = await _customer(test_db, "deact")
    account = await _account(
        test_db, customer.id,
        "expired@kramnet.se", AccountStatus.active,
        now - timedelta(days=1),
    )
    await test_db.commit()

    mocker.patch("app.services.scheduler._email_service.send_deactivation_notice")
    mocker.patch("app.services.scheduler._email_service.send_admin_notification")
    mocker.patch(
        "app.services.scheduler.AsyncSessionLocal",
        async_sessionmaker(test_engine, expire_on_commit=False),
    )

    await daily_deactivation_check()

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as verify:
        result = await verify.execute(
            select(EmailAccount).where(EmailAccount.id == account.id)
        )
        updated = result.scalar_one()

    assert updated.status == AccountStatus.inactive


# ── cleanup_expired_tokens ────────────────────────────────────────────────────

async def test_cleanup_expired_tokens(test_db, test_engine, mocker):
    now = datetime.now(timezone.utc)
    customer = await _customer(test_db, "tok")
    test_db.add(AuthToken(
        customer_id=customer.id,
        token_hash="expired_hash",
        expires_at=now - timedelta(hours=1),
    ))
    test_db.add(AuthToken(
        customer_id=customer.id,
        token_hash="valid_hash",
        expires_at=now + timedelta(hours=1),
    ))
    await test_db.commit()

    mocker.patch(
        "app.services.scheduler.AsyncSessionLocal",
        async_sessionmaker(test_engine, expire_on_commit=False),
    )

    await cleanup_expired_tokens()

    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as verify:
        result = await verify.execute(
            select(AuthToken).where(AuthToken.customer_id == customer.id)
        )
        tokens = result.scalars().all()

    assert len(tokens) == 1
    assert tokens[0].token_hash == "valid_hash"
