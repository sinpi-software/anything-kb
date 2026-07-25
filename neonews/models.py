from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import BOOLEAN, INTEGER, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.expression import true

# Timezone-aware throughout. Everything here is compared against the engine's
# datetimes and against `now`, so a naive column would silently drop the offset
# and shift every watermark comparison.
_TS = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    pass


class _BaseModel(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())


class Source(_BaseModel):
    """A place items come from. Upserted from neonews.toml on every poll run:
    config in git, runtime state here."""

    __tablename__ = "neonews_sources"
    __table_args__ = (UniqueConstraint("kind", "locator", name="neonews_sources_kind_locator"),)

    kind: Mapped[str] = mapped_column(TEXT, nullable=False)  # "rss" | "files"
    locator: Mapped[str] = mapped_column(TEXT, nullable=False)  # feed URL or directory path
    title: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    active: Mapped[bool] = mapped_column(BOOLEAN, nullable=False, server_default=true())
    last_polled_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    failure_count: Mapped[int] = mapped_column(INTEGER, nullable=False, server_default=text("0"))


class Item(_BaseModel):
    """One gathered item, carrying its whole lifecycle. Every flow is a WHERE clause
    over this table — which is what makes the pipeline self-healing without a reconciler."""

    __tablename__ = "neonews_items"
    __table_args__ = (UniqueConstraint("source_id", "dedup_key", name="neonews_items_source_dedup"),)

    source_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("neonews_sources.id"), nullable=False)
    dedup_key: Mapped[str] = mapped_column(TEXT, nullable=False)
    url: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    title: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    content: Mapped[str | None] = mapped_column(TEXT, nullable=True)  # as the source gave it
    published_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    full_text: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    job_id: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    job_status: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    attempts: Mapped[int] = mapped_column(INTEGER, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(TEXT, nullable=True)


class Issue(_BaseModel):
    __tablename__ = "neonews_issues"

    generated_at: Mapped[datetime] = mapped_column(_TS, nullable=False)
    covers_since: Mapped[datetime] = mapped_column(_TS, nullable=False)
    path: Mapped[str] = mapped_column(TEXT, nullable=False)
    story_count: Mapped[int] = mapped_column(INTEGER, nullable=False)


class JobState(Base):
    """Watermarks, keyed by flow name."""

    __tablename__ = "neonews_job_state"

    key: Mapped[str] = mapped_column(TEXT, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(_TS, nullable=False)
