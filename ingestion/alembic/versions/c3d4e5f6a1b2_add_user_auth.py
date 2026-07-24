"""add user auth: sessions, email_tokens tables; api_keys name/prefix/last_used_at/created_by_id

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-07-24

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c3d4e5f6a1b2"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("last_seen_at", postgresql.TIMESTAMP(), nullable=True),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"])

    op.create_table(
        "email_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("created_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", postgresql.TIMESTAMP(), server_default=sa.func.now(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(), nullable=False),
        sa.Column("used_at", postgresql.TIMESTAMP(), nullable=True),
    )
    op.create_index("ix_email_tokens_token_hash", "email_tokens", ["token_hash"])

    op.add_column("api_keys", sa.Column("name", sa.Text(), nullable=True))
    op.add_column("api_keys", sa.Column("prefix", sa.Text(), nullable=True))
    op.add_column("api_keys", sa.Column("last_used_at", postgresql.TIMESTAMP(), nullable=True))
    op.add_column(
        "api_keys",
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("api_keys", "created_by_id")
    op.drop_column("api_keys", "last_used_at")
    op.drop_column("api_keys", "prefix")
    op.drop_column("api_keys", "name")
    op.drop_index("ix_email_tokens_token_hash", table_name="email_tokens")
    op.drop_table("email_tokens")
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_table("sessions")
