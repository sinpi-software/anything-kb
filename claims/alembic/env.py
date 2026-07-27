import os
from logging.config import fileConfig
from typing import Any

import dotenv
from sqlalchemy import engine_from_config, pool

from alembic import context
from models import Base

config = context.config

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
_db_url = os.environ.get("CLAIMS_POSTGRES_URL") or os.environ.get("INGESTION_POSTGRES_URL")
if _db_url:
    config.set_main_option("sqlalchemy.url", _db_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# claims shares a database with the engine and with neonews, each of which owns its
# own Alembic chain. A separate version table keeps them from stamping over each other.
_VERSION_TABLE = "alembic_version_claims"


def _only_claims(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Autogenerate must ignore the other apps' tables, which share this database."""
    if type_ == "table":
        return bool(name and name.startswith("claims_"))
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=_VERSION_TABLE,
        include_object=_only_claims,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=_VERSION_TABLE,
            include_object=_only_claims,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
