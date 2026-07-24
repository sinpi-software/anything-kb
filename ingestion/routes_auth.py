"""Cookie-authenticated user-account endpoints: register/login/logout, email
verification, and password reset. Session-cookie auth only — the engine's
Bearer API-key auth (routes_content.py, routes_config.py, /graphql) is untouched."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

import mailer
from accounts import (
    SESSION_COOKIE,
    clear_session_cookie,
    consume_email_token,
    create_email_token,
    create_session,
    current_user,
    delete_session,
    hash_password,
    require_csrf,
    set_session_cookie,
    verify_password,
)
from db import get_postgres_session
from models import AuthSession, Org, OrgUser, OrgUserRole, User
from schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    MeResponse,
    OrgMembership,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
)

router = APIRouter(prefix="/api/auth", tags=["Accounts"])

MIN_PASSWORD_LENGTH = 8


def _me_payload(session: OrmSession, user: User) -> MeResponse:
    rows = (
        session.query(OrgUser.org_id, Org.name, OrgUser.role)
        .join(Org, Org.id == OrgUser.org_id)
        .filter(OrgUser.user_id == user.id)
        .order_by(Org.name)
        .all()
    )
    return MeResponse(
        id=str(user.id),
        email=user.email,
        name=user.name,
        email_verified=user.email_verified,
        is_admin=user.is_admin,
        orgs=[OrgMembership(org_id=str(org_id), org_name=name, role=role) for org_id, name, role in rows],
    )


def _send_verification_email(session: OrmSession, user: User) -> None:
    """Best-effort: a failed send must never break the caller's action."""
    try:
        token = create_email_token(session, user.id, "verify")
        mailer.send_email(
            user.email,
            "Verify your email",
            f'<p>Confirm your email:</p><p><a href="{mailer.link(f"/verify-email/{token}")}">Verify email</a></p>',
        )
    except Exception:  # email is best-effort: a send failure must never break the caller's action
        session.rollback()


@router.post("/register", response_model=MeResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, response: Response) -> MeResponse:
    """Create a user, auto-create an org owned by them (so they immediately have
    somewhere to hold API keys), log them in, and best-effort send a verification email."""
    email = payload.email.strip().lower()
    if not email or len(payload.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"email is required and password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    with get_postgres_session() as session:
        # User.name is NOT NULL (unlike the blog's, which allows it) — default to "".
        user = User(email=email, password_hash=hash_password(payload.password), name=payload.name or "")
        session.add(user)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="an account with this email already exists"
            ) from None
        user.created_by_id = user.id
        user.updated_by_id = user.id

        org_name = (payload.org_name or "").strip() or "My workspace"
        org = Org(name=org_name, created_by_id=user.id, updated_by_id=user.id)
        session.add(org)
        session.flush()
        session.add(
            OrgUser(
                org_id=org.id,
                user_id=user.id,
                role=OrgUserRole.OWNER.value,
                created_by_id=user.id,
                updated_by_id=user.id,
            )
        )
        session.commit()
        session.refresh(user)

        token = create_session(session, user.id)
        _send_verification_email(session, user)
        me = _me_payload(session, user)
    set_session_cookie(response, token)
    return me


@router.post("/login", response_model=MeResponse)
def login(payload: LoginRequest, response: Response) -> MeResponse:
    with get_postgres_session() as session:
        user = session.query(User).filter(User.email == payload.email.strip().lower()).one_or_none()
        if user is None or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
        token = create_session(session, user.id)
        me = _me_payload(session, user)
    set_session_cookie(response, token)
    return me


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_csrf)])
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with get_postgres_session() as session:
            delete_session(session, token)
    clear_session_cookie(response)


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(current_user)) -> MeResponse:  # noqa: B008 — FastAPI dependency idiom
    with get_postgres_session() as session:
        fresh = session.get(User, user.id)
        if fresh is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
        return _me_payload(session, fresh)


@router.post("/verify-email", response_model=MeResponse)
def verify_email(payload: TokenRequest) -> MeResponse:
    with get_postgres_session() as session:
        user = consume_email_token(session, payload.token, "verify")
        if user is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired verification link")
        user.email_verified = True
        session.commit()
        return _me_payload(session, user)


@router.post("/resend-verification", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_csrf)])
def resend_verification(user: User = Depends(current_user)) -> None:  # noqa: B008 — FastAPI dependency idiom
    with get_postgres_session() as session:
        fresh = session.get(User, user.id)
        if fresh is not None and not fresh.email_verified:
            _send_verification_email(session, fresh)


@router.post("/forgot-password", status_code=status.HTTP_200_OK)
def forgot_password(payload: ForgotPasswordRequest) -> dict[str, Any]:
    """Always 200, regardless of whether the email exists — don't leak account existence."""
    email = payload.email.strip().lower()
    with get_postgres_session() as session:
        user = session.query(User).filter(User.email == email).one_or_none() if email else None
        if user is not None:
            try:
                token = create_email_token(session, user.id, "reset")
                mailer.send_email(
                    user.email,
                    "Reset your password",
                    f"<p>Reset your password:</p><p>"
                    f'<a href="{mailer.link(f"/reset-password/{token}")}">Reset password</a></p>'
                    f"<p>This link expires in 1 hour.</p>",
                )
            except Exception:  # email is best-effort: a send failure must never break the caller's action
                session.rollback()
    return {"ok": True}


@router.post("/reset-password", response_model=MeResponse)
def reset_password(payload: ResetPasswordRequest, response: Response) -> MeResponse:
    if len(payload.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"password must be at least {MIN_PASSWORD_LENGTH} characters",
        )
    with get_postgres_session() as session:
        user = consume_email_token(session, payload.token, "reset")
        if user is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid or expired reset link")
        user.password_hash = hash_password(payload.password)
        # A reset invalidates all existing sessions.
        session.query(AuthSession).filter(AuthSession.user_id == user.id).delete()
        session.commit()
        token = create_session(session, user.id)  # log them in on the new password
        me = _me_payload(session, user)
    set_session_cookie(response, token)
    return me
