"""`report-documents`: render the check.

A document is reportable once nothing is still pending — where pending means a claim
that was selected for verification, has no verdict, and has attempts left. A
dead-lettered claim is therefore NOT pending: it unblocks the report and appears in it
as "could not be checked", which is a different statement from "unverifiable".

The markdown is written to a file for convenience and stored in claims_reports.body,
which is the durable copy.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from prefect import flow, get_run_logger
from sqlalchemy import select

import config
from db import get_postgres_session
from models import Claim, Document, Evidence, Report

_STANCE_LABELS = {"supports": "Supporting", "contradicts": "Contradicting", "context": "Context"}


def is_pending(claim: Claim) -> bool:
    """Still owed a verdict: selected, unjudged, and not out of attempts."""
    return claim.selected_for_verification and claim.verdict is None and claim.attempts < config.MAX_VERIFY_ATTEMPTS


def _slug(document: Document) -> str:
    raw = (document.title or document.canonical_url).lower()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", raw)).strip("-")[:60] or "document"


def _unchecked_reason(claim: Claim) -> str:
    if claim.selected_for_verification:
        return f"could not be checked: {claim.error or 'unknown error'}"
    if claim.claim_type != config.CHECKABLE_CLAIM_TYPE:
        return claim.claim_type
    if claim.checkworthiness < config.CHECKWORTHINESS_MIN:
        return f"below the checkworthiness floor ({claim.checkworthiness:.2f})"
    # Clears the floor but ranked outside VERIFY_MAX_PER_DOCUMENT. Saying "below the
    # floor" here would be false — the claim was crowded out, not judged too weak.
    return f"not among the {config.VERIFY_MAX_PER_DOCUMENT} most checkworthy claims"


def _attribution_line(claim: Claim) -> str:
    parts = [f"Attributed to {claim.attributed_to or 'the article'}"]
    if claim.attribution_type:
        parts[0] += f" ({claim.attribution_type.replace('_', ' ')})"
    if claim.cited_source:
        cited = claim.cited_source
        if claim.cited_source_url:
            cited = f"[{cited}]({claim.cited_source_url})"
        parts.append(f"citing {cited}")
    return " · ".join(parts)


def render_report(
    document: Document,
    claims: list[Claim],
    evidence_by_claim: dict[str, list[Evidence]],
    generated_at: datetime,
) -> str:
    judged = [claim for claim in claims if claim.verdict is not None]
    tally: dict[str, int] = defaultdict(int)
    for claim in judged:
        tally[str(claim.verdict)] += 1
    summary = ", ".join(f"{count} {verdict}" for verdict, count in sorted(tally.items())) or "none checked"

    byline = " · ".join(
        part
        for part in (
            document.canonical_url,
            document.author,
            f"published {document.published_at.date().isoformat()}" if document.published_at else None,
            f"checked {generated_at.date().isoformat()}",
        )
        if part
    )

    lines = [
        f"# Claim check — {document.title or document.canonical_url}",
        "",
        byline,
        "",
        f"*{len(claims)} claims extracted · {len(judged)} checked · {summary}.*",
        "",
    ]

    for claim in judged:
        lines += [
            f'## "{claim.text}"',
            "",
            _attribution_line(claim),
            "",
            f"**{str(claim.verdict).capitalize()}** · confidence {claim.confidence:.2f}"
            if claim.confidence is not None
            else f"**{str(claim.verdict).capitalize()}**",
            "",
            claim.rationale or "",
            "",
        ]
        for evidence in evidence_by_claim.get(str(claim.id), []):
            label = _STANCE_LABELS.get(evidence.stance, evidence.stance)
            snippet = f' — "{evidence.snippet}"' if evidence.snippet else ""
            lines.append(f"- **{label}:** [{evidence.title or evidence.url}]({evidence.url}){snippet}")
        lines.append("")

    unchecked = [claim for claim in claims if claim.verdict is None]
    if unchecked:
        lines += ["## Not checked", ""]
        lines += [f'- "{claim.text}" — {_unchecked_reason(claim)}' for claim in unchecked]
        lines.append("")

    return "\n".join(lines) + "\n"


@flow(name="report-documents")
def report_documents() -> dict[str, int]:
    logger = get_run_logger()
    written = 0

    with get_postgres_session() as session:
        candidates = list(
            session.scalars(
                select(Document)
                .where(Document.extracted_at.is_not(None), Document.reported_at.is_(None))
                .order_by(Document.extracted_at)
                .limit(config.REPORT_BATCH_SIZE)
            )
        )

        for document in candidates:
            claims = list(
                session.scalars(
                    select(Claim).where(Claim.document_id == document.id).order_by(Claim.checkworthiness.desc())
                )
            )
            if any(is_pending(claim) for claim in claims):
                continue

            evidence_by_claim: dict[str, list[Evidence]] = defaultdict(list)
            for claim in claims:
                for evidence in session.scalars(select(Evidence).where(Evidence.claim_id == claim.id)):
                    evidence_by_claim[str(claim.id)].append(evidence)

            generated_at = datetime.now(UTC)
            markdown = render_report(document, claims, evidence_by_claim, generated_at)
            output_dir = Path(config.OUTPUT_DIR)
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"{_slug(document)}-{generated_at.strftime('%Y-%m-%d-%H%M%S')}.md"
            path.write_text(markdown, encoding="utf-8")

            session.add(
                Report(
                    document_id=document.id,
                    generated_at=generated_at,
                    path=str(path),
                    claim_count=len(claims),
                    verified_count=sum(1 for claim in claims if claim.verdict is not None),
                    body=markdown,
                )
            )
            document.reported_at = generated_at
            session.commit()

            written += 1
            logger.info("report: %s → %s (%d claims)", document.canonical_url, path, len(claims))

    logger.info("report-documents: %d written", written)
    return {"reports": written}


if __name__ == "__main__":
    report_documents()
