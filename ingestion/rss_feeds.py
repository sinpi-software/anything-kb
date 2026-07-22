from uuid import UUID

import curl_cffi
import feedparser
import trafilatura
from prefect import flow, task
from prefect.logging import get_run_logger
from sqlalchemy import func, update

from db import get_postgres_session
from models import Artifact, RssFeed, RssFeedItem
from sanitize import sanitize


@task
def fetch_rss_feed_as_artifact(rss_feed_id: UUID, url: str) -> str:
    logger = get_run_logger()

    r = curl_cffi.get(url, impersonate="chrome")
    logger.info("Fetched %s -> %s (%d bytes)", url, r.status_code, len(r.content))

    with get_postgres_session() as session:
        artifact = Artifact(
            ref_table_name=RssFeed.__tablename__,
            ref_table_id=rss_feed_id,
            type="application/xml",
            data=sanitize(r.text),
        )
        session.add(artifact)
        session.flush()

        artifact_id = artifact.id
        logger.info("Created artifact %s for rss feed %s", artifact_id, rss_feed_id)

        session.execute(update(RssFeed).where(RssFeed.id == rss_feed_id).values(last_fetched_at=func.now()))
        session.commit()

    return artifact_id


@task
def parse_rss_feed_as_rss_feed_item(rss_feed_id: UUID, artifact_id: UUID) -> list[str]:
    logger = get_run_logger()

    with get_postgres_session() as session:
        artifact = session.get(Artifact, artifact_id)
        rss_feed = session.get(RssFeed, rss_feed_id)
        if artifact is None or rss_feed is None:
            logger.warning("Missing artifact %s or feed %s; nothing to parse", artifact_id, rss_feed_id)
            return []

        parsed = feedparser.parse(artifact.data)
        if parsed.bozo:
            logger.warning("Malformed feed for %s: %s", rss_feed_id, parsed.bozo_exception)

        title = parsed.feed.get("title")
        if title and rss_feed.title is None:
            rss_feed.title = title

        new_rss_feed_items = []
        for entry in parsed.entries:
            dedup_key = entry.get("id") or entry.get("link")
            if dedup_key is None:
                continue
            if session.query(RssFeedItem).filter_by(feed_id=rss_feed_id, dedup_key=dedup_key).first() is not None:
                continue
            item = RssFeedItem(
                feed_id=rss_feed_id,
                dedup_key=dedup_key,
                title=entry.get("title", ""),
                link=entry.get("link", ""),
                content=entry.get("content", ""),
            )
            session.add(item)
            new_rss_feed_items.append(item)

        session.flush()
        created = [str(item.id) for item in new_rss_feed_items]
        session.commit()

    logger.info("Parsed %d new items for feed %s", len(created), rss_feed_id)
    return created


@task(retries=2, retry_delay_seconds=5)
def fetch_rss_feed_item_as_artifact(rss_feed_item_id: str) -> str | None:
    logger = get_run_logger()

    with get_postgres_session() as session:
        rss_feed_item = session.get(RssFeedItem, rss_feed_item_id)
        if rss_feed_item is None:
            logger.warning("Missing RssFeedItem %s; nothing to extract", rss_feed_item_id)
            return None

        r = curl_cffi.get(rss_feed_item.link, impersonate="chrome", timeout=15)
        logger.info("Fetched %s -> %s (%d bytes)", rss_feed_item.link, r.status_code, len(r.content))

        with get_postgres_session() as session:
            artifact = Artifact(
                ref_table_name=RssFeedItem.__tablename__,
                ref_table_id=rss_feed_item_id,
                type="application/html",
                data=sanitize(r.text),
            )
            session.add(artifact)
            session.flush()
            artifact_id = artifact.id
            session.commit()

    return artifact_id


@task
def extract_rss_feed_item_artifact_as_markdown_artifact(rss_feed_item_id: str, artifact_id: str) -> str | None:
    logger = get_run_logger()

    with get_postgres_session() as session:
        html_artifact = session.get(Artifact, artifact_id)
        if html_artifact is None:
            logger.warning("Missing Artifact %s; nothing to extract", artifact_id)
            return None

        extracted = trafilatura.extract(
            html_artifact.data,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
        )

        if extracted is None:
            return None

        logger.info("Extracted %d bytes from %d bytes of html", len(extracted), len(html_artifact.data))

        with get_postgres_session() as session:
            markdown_artifact = Artifact(
                ref_table_name=RssFeedItem.__tablename__,
                ref_table_id=rss_feed_item_id,
                type="text/markdown",
                data=extracted,
            )
            session.add(markdown_artifact)
            session.flush()
            markdown_artifact_id = markdown_artifact.id
            session.commit()

    return markdown_artifact_id


@flow
def rss_feed_flow():
    logger = get_run_logger()

    with get_postgres_session() as session:
        rss_feeds = [(feed.id, feed.url) for feed in session.query(RssFeed).where(RssFeed.active).all()]

    if not rss_feeds:
        logger.info("No rss feeds configured/enabled")
        return

    extractions = []
    for rss_feed_id, url in rss_feeds:
        rss_feed_artifact_id = fetch_rss_feed_as_artifact.submit(rss_feed_id, url)
        rss_feed_item_ids = parse_rss_feed_as_rss_feed_item.submit(rss_feed_id, rss_feed_artifact_id).result()
        for rss_feed_item_id in rss_feed_item_ids:
            rss_feed_item_artifact_id = fetch_rss_feed_item_as_artifact.submit(rss_feed_item_id)
            extractions.append(
                extract_rss_feed_item_artifact_as_markdown_artifact.submit(rss_feed_item_id, rss_feed_item_artifact_id)
            )

    for extraction in extractions:
        extraction.wait()
