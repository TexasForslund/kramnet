"""Tests for AuthService — token generation, verification, and session JWTs."""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.auth_token import AuthToken
from app.services.auth_service import AuthService, _hash_token


@pytest.fixture
def auth_service() -> AuthService:
    return AuthService()


# ── generate_magic_link ───────────────────────────────────────────────────────

async def test_generate_magic_link_unknown_email(auth_service, test_db, mocker):
    mocker.patch("app.services.auth_service._email_service.send_magic_link")
    result = await auth_service.generate_magic_link(
        "nobody@example.com", test_db, "http://test"
    )
    assert result is None


# ── verify_token ──────────────────────────────────────────────────────────────

async def test_verify_token_valid(auth_service, test_db, sample_customer):
    raw = secrets.token_urlsafe(32)
    test_db.add(AuthToken(
        customer_id=sample_customer.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    ))
    await test_db.commit()

    customer = await auth_service.verify_token(raw, test_db)

    assert customer is not None
    assert customer.id == sample_customer.id


async def test_verify_token_expired(auth_service, test_db, sample_customer):
    raw = secrets.token_urlsafe(32)
    test_db.add(AuthToken(
        customer_id=sample_customer.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    ))
    await test_db.commit()

    result = await auth_service.verify_token(raw, test_db)

    assert result is None


async def test_verify_token_already_used(auth_service, test_db, sample_customer):
    raw = secrets.token_urlsafe(32)
    test_db.add(AuthToken(
        customer_id=sample_customer.id,
        token_hash=_hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
        used=True,
    ))
    await test_db.commit()

    result = await auth_service.verify_token(raw, test_db)

    assert result is None


# ── create_session_token ──────────────────────────────────────────────────────

def test_create_session_token(auth_service):
    token = auth_service.create_session_token(uuid.uuid4())
    assert isinstance(token, str)
    # A JWT consists of three base64-encoded segments separated by dots
    assert token.count(".") == 2


# ── get_current_customer ──────────────────────────────────────────────────────

async def test_get_current_customer_valid_token(
    auth_service, test_db, sample_customer
):
    token = auth_service.create_session_token(sample_customer.id)
    customer = await auth_service.get_current_customer(token, test_db)

    assert customer is not None
    assert customer.id == sample_customer.id


async def test_get_current_customer_invalid_token(auth_service, test_db):
    result = await auth_service.get_current_customer("not.a.valid.jwt", test_db)
    assert result is None
