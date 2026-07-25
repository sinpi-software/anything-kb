from functools import lru_cache
from os import getenv

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

import config


@lru_cache(maxsize=1)
def setup_postgres_pool() -> sessionmaker[Session]:
    db_url = getenv(config.POSTGRES_URL_ENV, "postgresql://ingestion:ingestion@localhost:5432/ingestion")
    engine = create_engine(db_url, pool_size=5, max_overflow=10)
    return sessionmaker(bind=engine)


def get_postgres_session() -> Session:
    return setup_postgres_pool()()
