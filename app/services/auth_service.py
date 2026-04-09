import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.auth_token import AuthToken
from app.models.customer import Customer
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_SESSION_TTL_DAYS = 7
_MAGIC_LINK_TTL_MINUTES = 30

_email_service = EmailService()


def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


class AuthService:
    async def generate_magic_link(
        self,
        customer_email: str,
        db: AsyncSession,
        base_url: str,
    ) -> str | None:
        result = await db.execute(
            select(Customer).where(Customer.contact_email == customer_email)
        )
        customer = result.scalar_one_or_none()
        if customer is None:
            return None

        raw_token = secrets.token_urlsafe(32)
        token_hash = _hash_token(raw_token)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=_MAGIC_LINK_TTL_MINUTES)

        db.add(AuthToken(
            customer_id=customer.id,
            token_hash=token_hash,
            expires_at=expires_at,
        ))
        db.add(AuditLog(
            customer_id=customer.id,
            event_type="login_link_sent",
            performed_by="system",
        ))
        await db.flush()

        await _email_service.send_magic_link(
            customer=customer,
            token=raw_token,
            base_url=base_url,
            db=db,
        )
        await db.commit()

        return f"{base_url}/auth/verify?token={raw_token}"

    async def verify_token(
        self,
        raw_token: str,
        db: AsyncSession,
    ) -> Customer | None:
        token_hash = _hash_token(raw_token)
        now = datetime.now(timezone.utc)

        result = await db.execute(
            select(AuthToken).where(
                AuthToken.token_hash == token_hash,
                AuthToken.used.is_(False),
                AuthToken.expires_at > now,
            )
        )
        auth_token = result.scalar_one_or_none()
        if auth_token is None:
            return None

        auth_token.used = True
        db.add(AuditLog(
            customer_id=auth_token.customer_id,
            event_type="login_link_used",
            performed_by="system",
        ))

        result = await db.execute(
            select(Customer).where(Customer.id == auth_token.customer_id)
        )
        customer = result.scalar_one_or_none()
        await db.commit()
        return customer

    def create_session_token(self, customer_id: uuid.UUID) -> str:
        expire = datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)
        payload = {
            "sub": str(customer_id),
            "exp": expire,
        }
        return jwt.encode(payload, settings.secret_key, algorithm=_ALGORITHM)

    async def get_current_customer(
        self,
        token: str,
        db: AsyncSession,
    ) -> Customer | None:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=[_ALGORITHM])
            customer_id: str | None = payload.get("sub")
            if customer_id is None:
                return None
        except JWTError:
            return None

        result = await db.execute(
            select(Customer).where(Customer.id == uuid.UUID(customer_id))
        )
        return result.scalar_one_or_none()
