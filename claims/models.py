from datetime import datetime

from sqlalchemy import ForeignKey, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import BOOLEAN, INTEGER, REAL, TEXT, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.expression import false

# Timezone-aware throughout: a naive column would silently drop the offset.
_TS = TIMESTAMP(timezone=True)


class Base(DeclarativeBase):
    pass


class _BaseModel(Base):
    __abstract__ = True
    id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())


class Document(_BaseModel):
    """One submitted link, carrying its whole lifecycle. Every flow is a WHERE clause
    over the stamps below — which is what makes the pipeline self-healing."""

    __tablename__ = "claims_documents"
    __table_args__ = (UniqueConstraint("canonical_url", name="claims_documents_canonical_url"),)

    url: Mapped[str] = mapped_column(TEXT, nullable=False)  # as submitted
    canonical_url: Mapped[str] = mapped_column(TEXT, nullable=False)  # dedup key
    title: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    author: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    full_text: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    # Stamped separately from extraction, so a document whose *LLM call* failed is
    # retried without re-fetching the page.
    fetched_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    extracted_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    attempts: Mapped[int] = mapped_column(INTEGER, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(TEXT, nullable=True)


class Claim(_BaseModel):
    """One assertion the document makes, with who is on the hook for it."""

    __tablename__ = "claims_claims"

    document_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("claims_documents.id"), nullable=False)
    quote: Mapped[str | None] = mapped_column(TEXT, nullable=True)  # verbatim span
    # Who asserted it, in-document.
    attributed_to: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    attribution_type: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    # The upstream origin the text names or links. Null for most claims.
    cited_source: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    cited_source_url: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    claim_type: Mapped[str] = mapped_column(TEXT, nullable=False)
    checkworthiness: Mapped[float] = mapped_column(REAL, nullable=False)
    # The gate decision, stamped at extraction rather than recomputed at verify time:
    # "top N by checkworthiness" as a live query is a moving target, which makes
    # "is this document finished?" unanswerable.
    selected_for_verification: Mapped[bool] = mapped_column(BOOLEAN, nullable=False, server_default=false())
    verdict: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    confidence: Mapped[float | None] = mapped_column(REAL, nullable=True)
    rationale: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    attempts: Mapped[int] = mapped_column(INTEGER, nullable=False, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    # Declared after `attempts` deliberately: naming this field `text` earlier in the
    # class body would shadow the `sqlalchemy.text` import for the rest of the class,
    # breaking `attempts`'s `server_default=text("0")` call above.
    text: Mapped[str] = mapped_column(TEXT, nullable=False)  # normalized to stand alone


class Evidence(_BaseModel):
    """One cited source bearing on one claim. Written only for URLs that appeared in
    OpenRouter's own annotations — see verify.ground_evidence."""

    __tablename__ = "claims_evidence"

    claim_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("claims_claims.id"), nullable=False)
    url: Mapped[str] = mapped_column(TEXT, nullable=False)
    title: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    snippet: Mapped[str | None] = mapped_column(TEXT, nullable=True)
    stance: Mapped[str] = mapped_column(TEXT, nullable=False)  # supports | contradicts | context
    published_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class Report(_BaseModel):
    __tablename__ = "claims_reports"

    document_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("claims_documents.id"), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(_TS, nullable=False)
    path: Mapped[str] = mapped_column(TEXT, nullable=False)
    claim_count: Mapped[int] = mapped_column(INTEGER, nullable=False)
    verified_count: Mapped[int] = mapped_column(INTEGER, nullable=False)
    # `path` is a dev convenience that dies with the pod; this is the durable copy.
    body: Mapped[str | None] = mapped_column(TEXT, nullable=True)
