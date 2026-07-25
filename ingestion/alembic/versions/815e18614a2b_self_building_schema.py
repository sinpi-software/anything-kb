"""self building schema

Revision ID: 815e18614a2b
Revises: 1f9837e47d09
Create Date: 2026-07-24 17:24:58.012533

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "815e18614a2b"
down_revision: str | Sequence[str] | None = "1f9837e47d09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("knowledge_base_configs", "relevance_prompt", new_column_name="interests")
    op.add_column(
        "knowledge_base_configs",
        sa.Column("discover_types", sa.BOOLEAN(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("knowledge_base_configs", "discover_types")
    op.alter_column("knowledge_base_configs", "interests", new_column_name="relevance_prompt")
