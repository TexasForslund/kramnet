"""add pending_verification to paymentstatus

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-09 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE IF NOT EXISTS is valid PostgreSQL since 9.3
    op.execute(sa.text(
        "ALTER TYPE paymentstatus ADD VALUE IF NOT EXISTS 'pending_verification'"
    ))


def downgrade() -> None:
    # PostgreSQL does not support removing enum values — no-op
    pass
