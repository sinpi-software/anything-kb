"""`verify-claims`: check a claim against the web and score it.

Three calls per claim. Two are web-enabled and deliberately opposed — one looks for
evidence, one is briefed to refute — because search here is OpenRouter's web plugin
rather than a standalone search API: searching is fused into the model's own
reasoning, and a single call asked to "check this claim" is a call that mostly
confirms it. The opposed pair is what recovers the adversarial structure.

The third call, the judge, runs with NO web access. It can only reason over evidence
that exists as rows in claims_evidence, so no rationale can rest on something the
reader cannot click.

Evidence and the verdict commit together: a crash rolls back to "not yet verified".
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from prefect import flow, get_run_logger, task
from prefect.exceptions import MissingContextError
from prefect.task_runners import ThreadPoolTaskRunner
from pydantic import BaseModel, Field
from sqlalchemy import select

import config
import llm as llm  # re-exported: tests patch verify.llm.complete
from db import get_postgres_session
from fetch import canonicalize_url
from models import Claim, Document, Evidence

_fallback_logger = logging.getLogger("claims.verify")


def _logger() -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
    try:
        return get_run_logger()
    except MissingContextError:
        return _fallback_logger


class EvidenceItem(BaseModel):
    url: str
    title: str | None = None
    snippet: str | None = None
    stance: str  # supports | contradicts | context
    published_at: str | None = None


class Research(BaseModel):
    evidence: list[EvidenceItem] = []


class Judgment(BaseModel):
    verdict: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


_RESEARCH_SYSTEM = """You are a fact-checker researching one claim. Search the web and return the
sources that bear on it.

For each source: the `url` you actually consulted, its `title`, a `snippet` quoted from it that
carries the relevant fact, its `published_at` date if you can determine it (ISO 8601), and a
`stance`:
- "supports" — it corroborates the claim
- "contradicts" — it cuts against the claim
- "context" — it bears on the claim without settling it (a different time window, a narrower
  population, a definitional difference)

Prefer primary sources over aggregators. Quote snippets verbatim; never paraphrase into them.
Return only sources you actually consulted. If you find nothing relevant, return an empty list —
an empty list is a useful answer and a fabricated source is not."""

_REFUTATION_SYSTEM = """You are a skeptical fact-checker. Your job is to find the strongest case
AGAINST one claim: evidence that it is false, that it is misleading as stated, or that it omits
context which changes its meaning.

Look specifically for: contradicting figures from primary sources, a different time window or
population than the claim implies, later corrections or retractions, and definitional sleight of
hand.

Return sources in the same shape as any research task: `url`, `title`, a verbatim `snippet`,
`published_at` if determinable, and a `stance` of "contradicts" or "context". If after genuine
effort you cannot find anything against the claim, return an empty list and say so by returning
nothing — do not manufacture a weak objection, and never invent a source."""

_JUDGE_SYSTEM = """You are adjudicating one claim on the evidence below. You have no web access:
reason ONLY over the evidence given. If it does not settle the question, say so.

`verdict`:
- "supported" — the evidence backs the claim as stated
- "disputed" — credible evidence cuts both ways
- "refuted" — the evidence shows the claim is false or materially misleading as stated
- "unverifiable" — the evidence is absent, thin, or does not actually bear on the claim

`confidence`: 0.0-1.0, your confidence IN THE VERDICT — not the probability the claim is true.
High confidence in "unverifiable" is entirely coherent: it means you are sure the evidence does
not settle it. Thin evidence means "unverifiable", never a low-confidence "refuted".

`rationale`: two to four sentences. Say what the evidence shows and which source shows it. Where
the claim is technically true but misleading, say exactly what it omits. Never cite anything
that is not in the evidence below."""


def _stringify(items: list[EvidenceItem]) -> str:
    if not items:
        return "(no evidence was found)"
    return "\n\n".join(
        f"[{item.stance}] {item.title or 'untitled'} — {item.url}"
        + (f" ({item.published_at})" if item.published_at else "")
        + f"\n{item.snippet or ''}"
        for item in items
    )


def ground_evidence(
    items: list[EvidenceItem], citation_urls: frozenset[str], had_annotations: bool
) -> list[EvidenceItem]:
    """Drop evidence whose URL OpenRouter did not report visiting.

    The research calls use json_schema output AND the web plugin, so the model emits URL
    strings into a JSON field — strings it can invent. OpenRouter reports its genuine
    citations separately, as url_citation annotations. Comparison is on canonicalized
    URLs, since the model may echo a tracking-tagged variant of a URL it really visited.

    When a response carries no annotations at all there is nothing to check against, so
    everything is kept: "we cannot tell what was cited" is not "nothing was cited", and
    dropping here would silently discard every source from a provider that does not
    report citations.
    """
    if not had_annotations:
        if items:
            _logger().warning(
                "verify: %d evidence items kept unchecked — the response carried no annotations, "
                "so there is nothing to verify their URLs against",
                len(items),
            )
        return items
    allowed = {canonicalize_url(url) for url in citation_urls}
    kept = [item for item in items if canonicalize_url(item.url) in allowed]
    if len(kept) != len(items):
        dropped = [item.url for item in items if canonicalize_url(item.url) not in allowed]
        _logger().warning("verify: dropped %d ungrounded evidence item(s): %s", len(dropped), ", ".join(dropped))
    return kept


def dedupe_evidence(items: list[EvidenceItem]) -> list[EvidenceItem]:
    """Collapse by (canonicalized url, stance), preserving order.

    The same URL under OPPOSING stances is kept as two rows on purpose: a source that
    genuinely cuts both ways is signal the judge should see.
    """
    seen: set[tuple[str, str]] = set()
    kept = []
    for item in items:
        key = (canonicalize_url(item.url), item.stance)
        if key in seen:
            continue
        seen.add(key)
        kept.append(item)
    return kept


def _normalize_stance(item: EvidenceItem) -> EvidenceItem:
    """Coerce an out-of-vocabulary stance to "context", once, at parse time.

    Done here rather than at write time so every downstream consumer — the judge's
    rendered evidence, the dedup key, and the stored row — agrees on the same value.
    A miscategorized source is still a real source, not a rejected one.
    """
    if item.stance not in config.STANCES:
        item.stance = "context"
    return item


def research(
    claim_text: str, attribution: str, context: str, adversarial: bool
) -> tuple[list[EvidenceItem], frozenset[str], bool]:
    """One web-enabled call. `adversarial=True` uses the refutation brief."""
    user = (
        f"CLAIM: {claim_text}\n"
        f"ATTRIBUTED TO: {attribution}\n\n"
        f"SURROUNDING CONTEXT FROM THE DOCUMENT (for disambiguation only — it is not evidence):\n{context}"
    )
    result = llm.complete(
        model=config.RESEARCH_MODEL,
        system=_REFUTATION_SYSTEM if adversarial else _RESEARCH_SYSTEM,
        user=user,
        schema_name="research",
        schema=Research,
        web=True,
    )
    parsed = Research.model_validate(json.loads(result.content))
    evidence = [_normalize_stance(item) for item in parsed.evidence]
    return evidence, result.citation_urls, result.had_annotations


def judge(claim_text: str, attribution: str, evidence: list[EvidenceItem]) -> Judgment:
    """The verdict. No web plugin: this call may only reason over `evidence`."""
    result = llm.complete(
        model=config.JUDGE_MODEL,
        system=_JUDGE_SYSTEM,
        user=f"CLAIM: {claim_text}\nATTRIBUTED TO: {attribution}\n\nEVIDENCE:\n{_stringify(evidence)}",
        schema_name="judgment",
        schema=Judgment,
        web=False,
    )
    judgment = Judgment.model_validate(json.loads(result.content))
    if judgment.verdict not in config.VERDICTS:
        raise ValueError(f"model returned an unknown verdict: {judgment.verdict!r}")
    return judgment


def _context_for(full_text: str | None, quote: str | None) -> str:
    """The window of document text around the claim's quote, for disambiguation."""
    if not full_text:
        return ""
    half = config.CLAIM_CONTEXT_CHARS // 2
    position = full_text.find(quote) if quote else -1
    if position < 0:
        return full_text[: config.CLAIM_CONTEXT_CHARS]
    return full_text[max(0, position - half) : position + half]


def _parse_published(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class VerifiedClaim(BaseModel):
    """What one claim's three calls produced, before it is written."""

    claim_id: str
    evidence: list[EvidenceItem]
    judgment: Judgment


@task(name="verify-one-claim")
def verify_one_claim(claim_id: str, claim_text: str, attribution: str, context: str) -> VerifiedClaim:
    supporting, supporting_urls, supporting_annotated = research(claim_text, attribution, context, adversarial=False)
    against, against_urls, against_annotated = research(claim_text, attribution, context, adversarial=True)
    pooled = ground_evidence(supporting, supporting_urls, supporting_annotated) + ground_evidence(
        against, against_urls, against_annotated
    )
    evidence = dedupe_evidence(pooled)
    return VerifiedClaim(claim_id=claim_id, evidence=evidence, judgment=judge(claim_text, attribution, evidence))


@flow(name="verify-claims", task_runner=ThreadPoolTaskRunner(max_workers=config.VERIFY_CONCURRENCY))  # type: ignore[arg-type]
def verify_claims() -> dict[str, int]:
    logger = _logger()
    verified = 0
    failed = 0

    with get_postgres_session() as session:
        pending = list(
            session.scalars(
                select(Claim)
                .where(
                    Claim.selected_for_verification.is_(True),
                    Claim.verdict.is_(None),
                    Claim.attempts < config.MAX_VERIFY_ATTEMPTS,
                )
                .order_by(Claim.checkworthiness.desc())
                .limit(config.VERIFY_BATCH_SIZE)
            )
        )
        # Read what each call needs before fanning out; the session is not thread-safe.
        jobs = []
        for pending_claim in pending:
            document = session.get(Document, pending_claim.document_id)
            jobs.append(
                (
                    str(pending_claim.id),
                    pending_claim.text,
                    pending_claim.attributed_to or "the article",
                    _context_for(document.full_text if document else None, pending_claim.quote),
                )
            )

        futures = [(job[0], verify_one_claim.submit(*job)) for job in jobs]
        for claim_id, future in futures:
            claim = session.get(Claim, claim_id)
            if claim is None:  # deleted mid-run
                continue
            try:
                # The success-path writes live inside this same try: if the commit
                # itself fails (a constraint violation, a dropped connection), that
                # must dead-letter this claim too, not escape and sink the sweep.
                outcome = future.result()
                for item in outcome.evidence:
                    session.add(
                        Evidence(
                            claim_id=claim.id,
                            url=item.url,
                            title=item.title,
                            snippet=item.snippet,
                            stance=item.stance,
                            published_at=_parse_published(item.published_at),
                        )
                    )
                claim.verdict = outcome.judgment.verdict
                claim.confidence = outcome.judgment.confidence
                claim.rationale = outcome.judgment.rationale
                claim.verified_at = datetime.now(UTC)
                claim.error = None
                # Evidence and verdict commit together.
                session.commit()
                verified += 1
            except Exception as exc:  # one bad claim must never sink the sweep
                # Discard whatever this claim half-wrote before recording the failure,
                # so a bad commit cannot poison the session for the claims after it.
                session.rollback()
                claim.attempts += 1
                claim.error = str(exc)[: config.ERROR_MAX_CHARS]
                # At the cap the claim is dead-lettered with verdict still NULL —
                # deliberately not "unverifiable". "We searched and found nothing" and
                # "the call kept failing" are different facts, and the report says which.
                session.commit()
                failed += 1
                logger.warning("verify: claim %s failed (attempt %d): %s", claim_id, claim.attempts, exc)

    logger.info("verify-claims: %d verified, %d failures", verified, failed)
    return {"verified": verified, "failed": failed}


if __name__ == "__main__":
    verify_claims()
