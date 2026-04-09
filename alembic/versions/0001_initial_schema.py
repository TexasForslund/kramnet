"""initial_schema

Revision ID: 0001
Revises:
Create Date: 2026-04-09 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Enum types ---
    # PostgreSQL has no CREATE TYPE IF NOT EXISTS — use exception guard instead.
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE packagetype AS ENUM ('single', 'family');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE accountstatus
                AS ENUM ('active', 'inactive', 'pending_deletion', 'deleted');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE paymenttype AS ENUM ('new', 'renewal');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE paymentstatus AS ENUM ('pending', 'paid', 'failed', 'refunded');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))
    op.execute(sa.text("""
        DO $$ BEGIN
            CREATE TYPE deletionstatus AS ENUM ('pending', 'approved', 'completed');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
    """))

    # --- customers ---
    op.create_table(
        "customers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("contact_email", sa.String(255), nullable=False),
        sa.Column("swish_phone", sa.String(20), nullable=False),
        sa.Column("language", sa.String(2), nullable=False, server_default="sv"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_customers_contact_email", "customers", ["contact_email"], unique=True)

    # --- email_accounts ---
    op.create_table(
        "email_accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("address", sa.String(255), nullable=False),
        sa.Column(
            "package_type",
            postgresql.ENUM("single", "family", name="packagetype", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "active", "inactive", "pending_deletion", "deleted",
                name="accountstatus", create_type=False,
            ),
            nullable=False,
            server_default="inactive",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_accounts_customer_id", "email_accounts", ["customer_id"])
    op.create_index("ix_email_accounts_address", "email_accounts", ["address"], unique=True)
    op.create_index("ix_email_accounts_status", "email_accounts", ["status"])
    op.create_index("ix_email_accounts_expires_at", "email_accounts", ["expires_at"])

    # --- payments ---
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount_ore", sa.Integer(), nullable=False),
        sa.Column("swish_reference", sa.String(100), nullable=True),
        sa.Column(
            "payment_type",
            postgresql.ENUM("new", "renewal", name="paymenttype", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "paid", "failed", "refunded",
                name="paymentstatus", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("swish_reference"),
    )
    op.create_index("ix_payments_email_account_id", "payments", ["email_account_id"])
    op.create_index("ix_payments_status", "payments", ["status"])

    # --- auth_tokens ---
    op.create_table(
        "auth_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auth_tokens_customer_id", "auth_tokens", ["customer_id"])
    op.create_index("ix_auth_tokens_token_hash", "auth_tokens", ["token_hash"])

    # --- audit_logs ---
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_account_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("performed_by", sa.String(100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"]),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_email_account_id", "audit_logs", ["email_account_id"])
    op.create_index("ix_audit_logs_customer_id", "audit_logs", ["customer_id"])
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # --- deletion_requests ---
    op.create_table(
        "deletion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "approved", "completed",
                name="deletionstatus", create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("requested_by_ip", sa.String(45), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("scheduled_delete_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["email_account_id"], ["email_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email_account_id"),
    )


def downgrade() -> None:
    op.drop_table("deletion_requests")
    op.drop_table("audit_logs")
    op.drop_table("auth_tokens")
    op.drop_table("payments")
    op.drop_table("email_accounts")
    op.drop_table("customers")

    op.execute(sa.text("DROP TYPE IF EXISTS deletionstatus"))
    op.execute(sa.text("DROP TYPE IF EXISTS paymentstatus"))
    op.execute(sa.text("DROP TYPE IF EXISTS paymenttype"))
    op.execute(sa.text("DROP TYPE IF EXISTS accountstatus"))
    op.execute(sa.text("DROP TYPE IF EXISTS packagetype"))
