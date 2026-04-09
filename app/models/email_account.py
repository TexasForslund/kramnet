import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

import enum


class PackageType(str, enum.Enum):
    single = "single"
    family = "family"


class AccountStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    pending_deletion = "pending_deletion"
    deleted = "deleted"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailAccount(Base):
    __tablename__ = "email_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False, index=True
    )
    address: Mapped[str] = mapped_column(
        String(255), unique=True, nullable=False, index=True
    )
    package_type: Mapped[PackageType] = mapped_column(
        Enum(PackageType, name="packagetype"), nullable=False
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="accountstatus"),
        nullable=False,
        default=AccountStatus.inactive,
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    customer: Mapped["Customer"] = relationship(
        "Customer", back_populates="email_accounts"
    )
    payments: Mapped[list["Payment"]] = relationship(
        "Payment", back_populates="email_account"
    )
    deletion_request: Mapped["DeletionRequest | None"] = relationship(
        "DeletionRequest", back_populates="email_account", uselist=False
    )
