"""
Shared fixtures for the Kramnet test suite.

Uses an in-memory (file-based temp) SQLite database so tests never touch
Supabase.  JSONB columns are replaced with plain JSON at session start so
SQLAlchemy's SQLite DDL compiler does not choke on a PostgreSQL-only type.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# ── JSONB → JSON shim for SQLite ─────────────────────────────────────────────
# Must happen before Base.metadata is used to CREATE tables.
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

import app.models.audit_log  # noqa: F401 – registers AuditLog
import app.models.auth_token  # noqa: F401
import app.models.customer  # noqa: F401
import app.models.deletion_request  # noqa: F401
import app.models.email_account  # noqa: F401
import app.models.payment  # noqa: F401
from app.core.database import Base, get_db
from app.main import app
from app.models.customer import Customer
from app.models.email_account import AccountStatus, EmailAccount, PackageType
from app.models.payment import Payment, PaymentStatus, PaymentType

for _tbl in Base.metadata.tables.values():
    for _col in _tbl.columns:
        if isinstance(_col.type, JSONB):
            _col.type = JSON()


# ── Event loop ────────────────────────────────────────────────────────────────

@pytest.fixture
def event_loop():
    """One fresh event loop per test function."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ── Database engine ───────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_engine(tmp_path):
    """Fresh SQLite database for each test (file in pytest's tmp_path)."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


# ── Sessions ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def test_db(test_engine):
    """Async SQLAlchemy session bound to the test database."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture
async def test_client(test_engine, mocker):
    """
    HTTPX async client backed by the FastAPI app with get_db overridden.
    The APScheduler is mocked so it does not start during lifespan.
    """
    mocker.patch("app.main._scheduler.start")
    mocker.patch("app.main._scheduler.shutdown")

    factory = async_sessionmaker(test_engine, expire_on_commit=False)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ── Sample data fixtures ──────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sample_customer(test_db):
    customer = Customer(
        name="Test Person",
        contact_email="test@example.com",
        swish_phone="0701234567",
        language="sv",
    )
    test_db.add(customer)
    await test_db.commit()
    await test_db.refresh(customer)
    return customer


@pytest_asyncio.fixture
async def sample_email_account(test_db, sample_customer):
    account = EmailAccount(
        customer_id=sample_customer.id,
        address="test@kramnet.se",
        package_type=PackageType.single,
        status=AccountStatus.active,
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
    )
    test_db.add(account)
    await test_db.commit()
    await test_db.refresh(account)
    return account


@pytest_asyncio.fixture
async def sample_payment(test_db, sample_email_account):
    payment = Payment(
        email_account_id=sample_email_account.id,
        amount_ore=14900,
        payment_type=PaymentType.new,
        status=PaymentStatus.pending,
    )
    test_db.add(payment)
    await test_db.commit()
    await test_db.refresh(payment)
    return payment
