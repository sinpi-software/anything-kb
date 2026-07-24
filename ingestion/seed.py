import os
from typing import Any

import dotenv
from sqlalchemy.orm import DeclarativeBase, Session

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
    from models import Org, OrgUser, OrgUserRole, User

    ph = PasswordHasher()
    admin_email = os.getenv("INGESTION_ADMIN_EMAIL", "admin@sinpi.software")
    api_key_plaintext: str | None = None

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
            admin.created_by_id = admin.id
            admin.updated_by_id = admin.id
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

        from auth import generate_api_key, hash_key
        from models import ApiKey, OrgConfig

        _cfg, config_created = get_or_create(
            session,
            OrgConfig,
            defaults={
                "relevance_prompt": "Is this content about technology, science, or business news?",
                "entity_types": ["Person", "Organization", "Place", "Topic"],
                "relationship_types": ["WORKS_AT", "LOCATED_IN", "RELATED_TO", "FOUNDED"],
            },
            org_id=org.id,
        )

        existing_key = session.query(ApiKey).filter(ApiKey.org_id == org.id, ApiKey.revoked_at.is_(None)).first()
        if existing_key is None:
            api_key_plaintext = generate_api_key()
            session.add(ApiKey(org_id=org.id, key_hash=hash_key(api_key_plaintext)))

        session.commit()

    for label, created in [
        (f"admin user {admin_email!r}", admin_created),
        ("default org", org_created),
        ("org membership", membership_created),
        ("org config", config_created),
    ]:
        print(f"  {'created' if created else 'exists '}  {label}")
    if api_key_plaintext is not None:
        print(f"\n  API KEY (shown once): {api_key_plaintext}\n")


if __name__ == "__main__":
    seed_database()
