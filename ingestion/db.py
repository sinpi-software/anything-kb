from functools import lru_cache
from os import getenv

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@lru_cache(maxsize=1)
def setup_postgres_pool() -> sessionmaker[Session]:
    db_url = getenv("INGESTION_POSTGRES_URL", "postgresql://user:password@localhost:5432/mydatabase")
    engine = create_engine(db_url, pool_size=10, max_overflow=20)
    Session = sessionmaker(bind=engine)
    return Session


def get_postgres_session() -> Session:
    Session = setup_postgres_pool()
    return Session()
