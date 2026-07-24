"""type descriptions on config

Revision ID: 1f9837e47d09
Revises: cd59b9725d7a
Create Date: 2026-07-24 15:11:52.795644

Turn entity_types / relationship_types from text[] into jsonb lists of
{"name", "description"} objects, so each type can carry a description that
guides the extractor. Existing names are preserved with an empty description.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "1f9837e47d09"
down_revision: str | Sequence[str] | None = "cd59b9725d7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = ("entity_types", "relationship_types")


def upgrade() -> None:
    # ALTER ... TYPE USING can't hold a subquery, so: cast text[] -> jsonb array of
    # strings, then UPDATE each string element into a {name, description} object.
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE knowledge_base_configs ALTER COLUMN {column} DROP DEFAULT")
        op.execute(f"ALTER TABLE knowledge_base_configs ALTER COLUMN {column} TYPE jsonb USING to_jsonb({column})")
        op.execute(
            f"""
            UPDATE knowledge_base_configs SET {column} = COALESCE(
                (SELECT jsonb_agg(jsonb_build_object('name', e, 'description', ''))
                 FROM jsonb_array_elements_text({column}) AS e),
                '[]'::jsonb)
            """
        )
        op.execute(f"ALTER TABLE knowledge_base_configs ALTER COLUMN {column} SET DEFAULT '[]'::jsonb")


def downgrade() -> None:
    for column in _COLUMNS:
        op.execute(f"ALTER TABLE knowledge_base_configs ALTER COLUMN {column} DROP DEFAULT")
        # Reduce objects back to their names (a jsonb array of strings)...
        op.execute(
            f"""
            UPDATE knowledge_base_configs SET {column} = COALESCE(
                (SELECT jsonb_agg(elem->>'name') FROM jsonb_array_elements({column}) AS elem),
                '[]'::jsonb)
            """
        )
        # ...then reshape the jsonb array literal into a text[] literal (names are simple identifiers).
        op.execute(
            f"ALTER TABLE knowledge_base_configs ALTER COLUMN {column} TYPE text[] "
            f"USING translate({column}::text, '[]', '{{}}')::text[]"
        )
        op.execute(f"ALTER TABLE knowledge_base_configs ALTER COLUMN {column} SET DEFAULT '{{}}'")
