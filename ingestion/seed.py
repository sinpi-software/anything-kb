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
    from memberships import (
        DEFAULT_ENTITY_TYPES,
        DEFAULT_INTERESTS,
        DEFAULT_RELATIONSHIP_TYPES,
        create_knowledge_base,
    )
    from models import KnowledgeBase, KnowledgeBaseConfig, KnowledgeBaseUser, KnowledgeBaseUserRole, User

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

        knowledge_base = (
            session.query(KnowledgeBase).filter(KnowledgeBase.name == "Default Knowledge Base").one_or_none()
        )
        org_created = knowledge_base is None
        if knowledge_base is None:
            knowledge_base = create_knowledge_base(
                session, admin, "Default Knowledge Base", charter="This is the default knowledge base."
            )
            membership_created = config_created = True
        else:
            # The knowledge base already existed, but its membership or config may not
            # have — e.g. a prior run was interrupted between the three inserts that
            # create_knowledge_base makes together. Repair whichever piece is missing
            # instead of assuming the knowledge base's existence implies the rest does.
            membership = (
                session.query(KnowledgeBaseUser)
                .filter(
                    KnowledgeBaseUser.knowledge_base_id == knowledge_base.id,
                    KnowledgeBaseUser.user_id == admin.id,
                )
                .one_or_none()
            )
            membership_created = membership is None
            if membership is None:
                session.add(
                    KnowledgeBaseUser(
                        knowledge_base_id=knowledge_base.id,
                        user_id=admin.id,
                        role=KnowledgeBaseUserRole.OWNER.value,
                        created_by_id=admin.id,
                        updated_by_id=admin.id,
                    )
                )

            config = (
                session.query(KnowledgeBaseConfig)
                .filter(KnowledgeBaseConfig.knowledge_base_id == knowledge_base.id)
                .one_or_none()
            )
            config_created = config is None
            if config is None:
                session.add(
                    KnowledgeBaseConfig(
                        knowledge_base_id=knowledge_base.id,
                        interests=DEFAULT_INTERESTS,
                        discover_types=True,
                        entity_types=DEFAULT_ENTITY_TYPES,
                        relationship_types=DEFAULT_RELATIONSHIP_TYPES,
                    )
                )

        from auth import generate_api_key, hash_key
        from models import ApiKey

        existing_key = (
            session.query(ApiKey)
            .filter(ApiKey.knowledge_base_id == knowledge_base.id, ApiKey.revoked_at.is_(None))
            .first()
        )
        if existing_key is None:
            api_key_plaintext = generate_api_key()
            session.add(ApiKey(knowledge_base_id=knowledge_base.id, key_hash=hash_key(api_key_plaintext)))

        session.commit()

    for label, created in [
        (f"admin user {admin_email!r}", admin_created),
        ("default knowledge_base", org_created),
        ("knowledge_base membership", membership_created),
        ("knowledge_base config", config_created),
    ]:
        print(f"  {'created' if created else 'exists '}  {label}")
    if api_key_plaintext is not None:
        print(f"\n  API KEY (shown once): {api_key_plaintext}\n")


if __name__ == "__main__":
    seed_database()
