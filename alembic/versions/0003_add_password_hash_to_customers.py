"""add_password_hash_to_customers

Revision ID: 0003
Revises: d4e9ba7dd8ca
Create Date: 2026-04-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003'
down_revision: Union[str, None] = 'd4e9ba7dd8ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('customers', sa.Column('password_hash', sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column('customers', 'password_hash')
