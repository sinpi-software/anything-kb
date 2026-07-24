"""rename orgs to knowledge_bases

Revision ID: cd59b9725d7a
Revises: c3d4e5f6a1b2
Create Date: 2026-07-24 12:31:56.074330

Renames the tenant concept from "org" to "knowledge_base" across Postgres.
Uses ALTER ... RENAME throughout, so all existing rows are preserved.
Foreign keys follow their columns automatically; only the physical
table and column names change.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cd59b9725d7a"
down_revision: str | Sequence[str] | None = "c3d4e5f6a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, org column) pairs that carry a tenant foreign key.
_KB_ID_COLUMNS = [
    ("knowledge_base_configs", "org_id"),
    ("knowledge_base_users", "org_id"),
    ("api_keys", "org_id"),
    ("ingest_jobs", "org_id"),
]


def upgrade() -> None:
    op.rename_table("orgs", "knowledge_bases")
    op.rename_table("org_configs", "knowledge_base_configs")
    op.rename_table("org_users", "knowledge_base_users")
    for table, _ in _KB_ID_COLUMNS:
        op.alter_column(table, "org_id", new_column_name="knowledge_base_id")


def downgrade() -> None:
    for table, _ in _KB_ID_COLUMNS:
        op.alter_column(table, "knowledge_base_id", new_column_name="org_id")
    op.rename_table("knowledge_base_users", "org_users")
    op.rename_table("knowledge_base_configs", "org_configs")
    op.rename_table("knowledge_bases", "orgs")
