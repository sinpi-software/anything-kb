"""drop dead tables: wiki_page_versions, wiki_pages, org_settings, app_settings

These tables backed models that no code references (superseded by the Neo4j
graph and OrgConfig). Dev-only DB — the tables held only placeholder rows.

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-07-23

"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2c3d4e5f6a1"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop dependents before parents (FKs). Dev-only DB — data is disposable.
    op.drop_table("wiki_page_versions")
    op.drop_table("wiki_pages")
    op.drop_table("org_settings")
    op.drop_table("app_settings")


def downgrade() -> None:
    raise NotImplementedError("one-way migration: dropped tables are not recreated")
