import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.core.database import Base

import enum


class DeletionStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    completed = "completed"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DeletionRequest(Base):
    __tablename__ = "deletion_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("email_accounts.id"),
        unique=True,
        nullable=False,
    )
    status: Mapped[DeletionStatus] = mapped_column(
        Enum(DeletionStatus, name="deletionstatus"),
        nullable=False,
        default=DeletionStatus.pending,
    )
    requested_by_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scheduled_delete_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    email_account: Mapped["EmailAccount"] = relationship(
        "EmailAccount", back_populates="deletion_request"
    )
