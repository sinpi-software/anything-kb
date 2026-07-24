"""transform name and gate

Revision ID: 172d87c4ab7b
Revises: e76651196869
Create Date: 2026-07-23 17:35:20.155158

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '172d87c4ab7b'
down_revision: Union[str, Sequence[str], None] = 'e76651196869'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("transformations", sa.Column("name", sa.TEXT(), nullable=True))
    op.add_column("transformations", sa.Column("gate", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    # backfill a unique-per-org name for existing rows
    op.execute("UPDATE transformations SET name = type || '-' || position WHERE name IS NULL")
    op.alter_column("transformations", "name", nullable=False)
    op.create_unique_constraint("transformations_org_id_name_key", "transformations", ["org_id", "name"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("transformations_org_id_name_key", "transformations", type_="unique")
    op.drop_column("transformations", "gate")
    op.drop_column("transformations", "name")
