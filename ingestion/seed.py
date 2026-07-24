import os
from typing import Any

import dotenv
from sqlalchemy.orm import DeclarativeBase, Session

# Load env from the project root (same order as main.py), so the admin
# credentials and INGESTION_POSTGRES_URL are available when run standalone.
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.local")
dotenv.load_dotenv(dotenv_path=f"{_project_root}/.env.sample")


def get_or_create[ModelT: DeclarativeBase](
    session: Session,
    model: type[ModelT],
    defaults: dict[str, Any] | None = None,
    **filters: Any,
) -> tuple[ModelT, bool]:
    instance = session.query(model).filter_by(**filters).one_or_none()
    if instance is not None:
        return instance, False
    instance = model(**{**filters, **(defaults or {})})
    session.add(instance)
    session.flush()
    return instance, True


def seed_database() -> None:
    from argon2 import PasswordHasher

    from db import get_postgres_session
    from models import Org, OrgSettings, OrgUser, OrgUserRole, RssFeed, Transformation, TransformationType, User
    from transformations import validate_transform_config

    ph = PasswordHasher()
    admin_email = os.getenv("INGESTION_ADMIN_EMAIL", "admin@sinpi.software")

    with get_postgres_session() as session:
        admin, admin_created = get_or_create(
            session,
            User,
            defaults={
                "name": os.getenv("INGESTION_ADMIN_NAME", "Admin User"),
                "password_hash": ph.hash(os.getenv("INGESTION_ADMIN_PASSWORD", "adminpassword")),
                "email_verified": True,
                "is_admin": True,
            },
            email=admin_email,
        )
        if admin_created:
            # The bootstrap admin has no prior creator, so it is its own.
            admin.created_by_id = admin.id
            admin.updated_by_id = admin.id

        # Every other seeded row is attributed to the admin.
        audit = {"created_by": admin, "updated_by": admin}

        org, org_created = get_or_create(
            session,
            Org,
            defaults=dict(charter="This is the default organization.", **audit),
            name="Default Organization",
        )

        _membership, membership_created = get_or_create(
            session,
            OrgUser,
            defaults=dict(role=OrgUserRole.OWNER.value, **audit),
            org_id=org.id,
            user_id=admin.id,
        )

        _settings, settings_created = get_or_create(
            session,
            OrgSettings,
            defaults=dict(setting_value="default_value", **audit),
            org_id=org.id,
            setting_name="default_setting",
        )

        _rss_feed, rss_feed_created = get_or_create(
            session,
            RssFeed,
            defaults=dict(active=True, **audit),
            org_id=org.id,
            url="https://news.ycombinator.com/rss",
            title="Hacker News",
        )

        # Transform chain for the org — every step reads the source article (fan-out); a step's
        # outgoing gate checks its own output and halts the later steps when unmet. Here the
        # newsworthiness gate stops the chain (so knowledge never runs) when score < 5.
        transform_model = "openai/gpt-5-nano"
        transform_chain = [
            (
                0,
                TransformationType.SUMMARIZE.value,
                "Summarize this article in 3 concise sentences.",
                None,
                "summary",
                None,
            ),
            (
                1,
                TransformationType.SCORE.value,
                "Is this story newsworthy? Score 0-10 with a short rationale.",
                None,
                "newsworthiness",
                {"field": "score", "op": "gte", "value": 5},
            ),
            (
                2,
                TransformationType.KNOWLEDGE.value,
                "Extract the notable entities and how they relate from this article.",
                {"entity_types": ["Person", "Place", "Organization", "Topic", "Story"]},
                "knowledge",
                None,
            ),
        ]
        transforms_created = []
        for position, transform_type, prompt, params, name, gate in transform_chain:
            validate_transform_config(transform_type, transform_model, prompt, params, name=name, gate=gate)
            transform, created = get_or_create(
                session,
                Transformation,
                defaults=dict(
                    type=transform_type,
                    model=transform_model,
                    prompt=prompt,
                    params=params,
                    name=name,
                    gate=gate,
                    **audit,
                ),
                org_id=org.id,
                position=position,
            )
            # Update name and gate even if transform already existed
            if not created:
                transform.name = name
                transform.gate = gate
            transforms_created.append((f"transform[{position}] {transform_type}", created))

        session.commit()

    for label, created in [
        (f"admin user {admin_email!r}", admin_created),
        ("default org", org_created),
        ("org membership", membership_created),
        ("org settings", settings_created),
        ("rss feed", rss_feed_created),
        *transforms_created,
    ]:
        print(f"  {'created' if created else 'exists '}  {label}")


if __name__ == "__main__":
    seed_database()
