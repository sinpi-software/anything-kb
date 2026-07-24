"""User authentication: password hashing, session cookies, email tokens, and the
FastAPI dependencies that guard the cookie-authenticated routes.

Ported from anything_blog's `auth.py`, renamed to avoid colliding with this
engine's existing `auth.py` (Bearer API-key auth for /content, /config, /graphql —
a separate, unrelated mechanism that this module must not touch)."""

import hashlib
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, Response, status
from sqlalchemy.orm import Session as OrmSession

from db import get_postgres_session
from models import AuthSession, EmailToken, User

_hasher = PasswordHasher()


def hash_password(pw: str) -> str:
    return _hasher.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


SESSION_COOKIE = "session"
SESSION_TTL = timedelta(days=30)
VERIFY_TTL = timedelta(hours=24)
RESET_TTL = timedelta(hours=1)
_EMAIL_TOKEN_TTL = {"verify": VERIFY_TTL, "reset": RESET_TTL}

_COOKIE_KWARGS: dict[str, Any] = {"httponly": True, "secure": True, "samesite": "lax", "path": "/"}


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(dt: datetime) -> datetime:
    """Our TIMESTAMP columns store no tz, so a value read back is naive; every value
    this module writes is UTC, so treat a naive read as UTC rather than local time."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(SESSION_COOKIE, token, max_age=int(SESSION_TTL.total_seconds()), **_COOKIE_KWARGS)


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def create_session(session: OrmSession, user_id: Any) -> str:
    token = secrets.token_urlsafe(32)
    session.add(AuthSession(token_hash=_hash_token(token), user_id=user_id, expires_at=datetime.now(UTC) + SESSION_TTL))
    session.commit()
    return token


def resolve_session_user(session: OrmSession, token: str) -> User | None:
    """The user for a live session token, or None if it's missing/expired. Touches
    `last_seen_at` on success."""
    row = session.query(AuthSession).filter(AuthSession.token_hash == _hash_token(token)).one_or_none()
    if row is None or _aware(row.expires_at) <= datetime.now(UTC):
        return None
    row.last_seen_at = datetime.now(UTC)
    session.commit()
    return session.get(User, row.user_id)


def delete_session(session: OrmSession, token: str) -> None:
    row = session.query(AuthSession).filter(AuthSession.token_hash == _hash_token(token)).one_or_none()
    if row is not None:
        session.delete(row)
        session.commit()


def create_email_token(session: OrmSession, user_id: Any, purpose: str) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        EmailToken(
            user_id=user_id,
            purpose=purpose,
            token_hash=_hash_token(token),
            expires_at=datetime.now(UTC) + _EMAIL_TOKEN_TTL[purpose],
        )
    )
    session.commit()
    return token


def consume_email_token(session: OrmSession, token: str, purpose: str) -> User | None:
    """Redeem a single-use email token, or return None if it's unknown, already used,
    expired, or for the wrong purpose."""
    row = (
        session.query(EmailToken)
        .filter(EmailToken.token_hash == _hash_token(token), EmailToken.purpose == purpose)
        .one_or_none()
    )
    if row is None or row.used_at is not None or _aware(row.expires_at) <= datetime.now(UTC):
        return None
    row.used_at = datetime.now(UTC)
    session.commit()
    return session.get(User, row.user_id)


def current_user(request: Request) -> User:
    """FastAPI dependency: the user for the `session` cookie. 401s if it's absent,
    unknown, or expired."""
    token = request.cookies.get(SESSION_COOKIE)
    user = None
    if token:
        with get_postgres_session() as session:
            user = resolve_session_user(session, token)
            if user is not None:
                session.expunge(user)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user


def home_knowledge_base_id(session: OrmSession, user_id: Any) -> str | None:
    """The knowledge base a user's cookie-authenticated actions belong to: the earliest one
    they were added to (registration auto-creates one, so normally their only knowledge base)."""
    from models import KnowledgeBaseUser

    row = (
        session.query(KnowledgeBaseUser.knowledge_base_id)
        .filter(KnowledgeBaseUser.user_id == user_id)
        .order_by(KnowledgeBaseUser.created_at.asc())
        .first()
    )
    return str(row[0]) if row is not None else None


# Dev: any localhost/127.0.0.1 origin. Prod: APP_ORIGINS (comma-separated).
_LOCALHOST_ORIGIN_RE = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?$")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _app_origins() -> set[str]:
    return {o.strip().rstrip("/") for o in os.environ.get("APP_ORIGINS", "").split(",") if o.strip()}


def _origin_allowed(origin: str) -> bool:
    origin = origin.rstrip("/")
    return bool(_LOCALHOST_ORIGIN_RE.match(origin)) or origin in _app_origins()


def require_csrf(request: Request) -> None:
    """CSRF defense for cookie-authenticated routes: a state-changing request must carry
    an Origin/Referer matching an allowed app origin. Safe methods are exempt, as are
    routes with no ambient session cookie to forge (register/login/email-token flows —
    the credential travels in the request body, not a cookie)."""
    if request.method in _SAFE_METHODS:
        return
    source = request.headers.get("origin") or request.headers.get("referer")
    if source and request.headers.get("referer") and not request.headers.get("origin"):
        source = f"{urlparse(source).scheme}://{urlparse(source).netloc}"
    if not source or not _origin_allowed(source):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="bad origin")
