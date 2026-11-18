"""Browser sessions and the existing independent device Bearer authentication."""
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.device import Device
from app.models.user import User, UserSession

logger = logging.getLogger(__name__)
SESSION_COOKIE = "tcc_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
admin_bearer = HTTPBearer(scheme_name="AdminToken", auto_error=False)
device_bearer = HTTPBearer(scheme_name="DeviceToken", auto_error=False)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _origin(value: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def require_same_origin(request: Request) -> None:
    # No X-Forwarded-* trust here. With a reverse proxy, set the canonical
    # APP_ORIGIN and configure its trusted host/proxy separately.
    allowed = _origin(os.environ.get("APP_ORIGIN", "") or str(request.base_url))
    supplied = request.headers.get("origin") or request.headers.get("referer")
    if not allowed or not supplied or _origin(supplied) != allowed:
        raise HTTPException(status_code=403, detail="Origem da solicitacao invalida.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(status_code=403, detail="Solicitacao entre sites recusada.")


@dataclass
class Identity:
    user: User | None
    session: UserSession | None
    role: str


def browser_identity(request: Request, session: Session) -> Identity:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token or len(token) > 128:
        raise HTTPException(status_code=401, detail="Entre com e-mail e senha.")
    record = session.get(UserSession, hash_device_token(token))
    if record is None or _as_utc(record.expires_at) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Sessao expirada ou invalida.")
    user = session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Sessao expirada ou invalida.")
    if request.method not in SAFE_METHODS:
        require_same_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not secrets.compare_digest(supplied.encode(), record.csrf_token.encode()):
            raise HTTPException(status_code=403, detail="Verificacao de sessao falhou. Atualize a pagina.")
    return Identity(user=user, session=record, role=user.role)


def require_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(admin_bearer)],
    session: Annotated[Session, Depends(get_session)],
) -> Identity:
    if credentials is not None:
        legacy = os.environ.get("ALLOW_LEGACY_ADMIN_TOKEN", "false").lower() == "true"
        token = os.environ.get("ADMIN_API_TOKEN", "")
        if legacy and len(token) >= 32 and not token.startswith("SUBSTITUA_") and secrets.compare_digest(credentials.credentials.encode(), token.encode()):
            return Identity(user=None, session=None, role="admin")
        raise HTTPException(status_code=401, detail="Credencial administrativa invalida.")
    try:
        return browser_identity(request, session)
    except SQLAlchemyError:
        logger.exception("Falha ao autenticar sessao.")
        raise HTTPException(status_code=503, detail="Banco de dados indisponivel") from None


def require_admin(identity: Annotated[Identity, Depends(require_user)]) -> Identity:
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="Esta operacao exige uma conta administradora.")
    return identity


def get_authenticated_device(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(device_bearer),
    ],
    session: Annotated[Session, Depends(get_session)],
) -> Device:
    authentication_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credencial de dispositivo ausente ou invalida.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if credentials is None:
        raise authentication_error

    token_hash = hash_device_token(credentials.credentials)

    statement = select(Device).where(
        Device.api_token_hash == token_hash,
        Device.is_active.is_(True),
    )

    try:
        device = session.scalar(statement)
    except SQLAlchemyError:
        logger.exception("Falha ao autenticar dispositivo.")

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Banco de dados indisponivel",
        ) from None

    if device is None:
        raise authentication_error

    return device
