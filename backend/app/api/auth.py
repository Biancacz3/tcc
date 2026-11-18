"""Same-origin cookie sessions; device uploads continue to use device tokens."""
import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import case, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.user import LoginAttempt, User, UserSession
from app.passwords import DUMMY_PASSWORD_HASH, normalize_email, verify_password
from app.security import SESSION_COOKIE, _as_utc, browser_identity, hash_device_token, require_same_origin

router = APIRouter(prefix="/auth", tags=["Acesso"])
logger = logging.getLogger(__name__)
DatabaseSession = Annotated[Session, Depends(get_session)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize(cls, value: str) -> str:
        return normalize_email(value)


def session_days() -> int:
    value = int(os.environ.get("SESSION_DAYS", "7"))
    if not 1 <= value <= 30:
        raise RuntimeError("SESSION_DAYS deve ficar entre 1 e 30.")
    return value


def cookie_secure() -> bool:
    return os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"


def _payload(user: User, record: UserSession) -> dict:
    return {
        "user": {"id": str(user.id), "email": user.email, "name": user.name, "role": user.role},
        "csrf_token": record.csrf_token,
        "expires_at": _as_utc(record.expires_at).isoformat(),
    }


def _reserve_attempt(session: Session, email: str, ip: str, now: datetime) -> None:
    """Atomic counters persist across worker/restart; no forwarded-IP trust."""
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif session.bind.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise RuntimeError("Banco nao suportado para limite de login.")
    cutoff = now - timedelta(minutes=15)
    session.execute(delete(LoginAttempt).where(LoginAttempt.window_started < now - timedelta(days=1)))
    exceeded = False
    for source, limit in (("account:" + email, 10), ("ip:" + ip, 30)):
        bucket = hashlib.sha256(source.encode()).hexdigest()
        statement = insert(LoginAttempt).values(bucket=bucket, attempts=1, window_started=now)
        expired = LoginAttempt.window_started <= cutoff
        statement = statement.on_conflict_do_update(
            index_elements=[LoginAttempt.bucket],
            set_={"attempts": case((expired, 1), else_=LoginAttempt.attempts + 1),
                  "window_started": case((expired, now), else_=LoginAttempt.window_started)},
        ).returning(LoginAttempt.attempts)
        exceeded = session.scalar(statement) > limit or exceeded
    session.commit()  # failed login must also consume the reservation
    if exceeded:
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos.", headers={"Retry-After": "900"})


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, session: DatabaseSession) -> dict:
    require_same_origin(request)
    now = datetime.now(timezone.utc)
    try:
        _reserve_attempt(session, payload.email, request.client.host if request.client else "unknown", now)
        # Serialize password verification/session creation with password resets
        # and account deactivation. Otherwise a login using the old hash could
        # create a session after the reset has already revoked existing ones.
        user = session.scalar(
            select(User).where(User.email == payload.email)
            .with_for_update().execution_options(populate_existing=True)
        )
        valid = verify_password(payload.password, user.password_hash if user and user.is_active else DUMMY_PASSWORD_HASH)
        if not valid or user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="E-mail ou senha invalidos.")
        # Replace the presented session, so an earlier browser session is not
        # fixed into a new account; remove expired entries opportunistically.
        previous = request.cookies.get(SESSION_COOKIE)
        if previous:
            session.execute(delete(UserSession).where(UserSession.token_hash == hash_device_token(previous)))
        session.execute(delete(UserSession).where(UserSession.expires_at <= now))
        token = secrets.token_urlsafe(32)
        record = UserSession(token_hash=hash_device_token(token), user_id=user.id,
                             csrf_token=secrets.token_urlsafe(32), created_at=now,
                             expires_at=now + timedelta(days=session_days()))
        session.add(record)
        session.commit()
        result = _payload(user, record)
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Falha ao abrir sessao.")
        raise HTTPException(status_code=503, detail="Banco de dados indisponivel") from None
    response.set_cookie(SESSION_COOKIE, token, httponly=True, secure=cookie_secure(),
                        samesite="lax", max_age=session_days() * 86400, path="/api/v1")
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/me")
def me(request: Request, response: Response, session: DatabaseSession) -> dict:
    try:
        identity = browser_identity(request, session)
        response.headers["Cache-Control"] = "no-store"
        return _payload(identity.user, identity.session)
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="Banco de dados indisponivel") from None


@router.post("/logout", status_code=204)
def logout(request: Request, session: DatabaseSession) -> Response:
    try:
        identity = browser_identity(request, session)  # includes Origin + CSRF
        session.delete(identity.session)
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(status_code=503, detail="Banco de dados indisponivel") from None
    response = Response(status_code=204, headers={"Cache-Control": "no-store"})
    response.delete_cookie(SESSION_COOKIE, path="/api/v1", secure=cookie_secure(), httponly=True, samesite="lax")
    return response
