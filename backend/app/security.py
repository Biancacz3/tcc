import hashlib
import logging
import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.device import Device


logger = logging.getLogger(__name__)

admin_token = os.environ["ADMIN_API_TOKEN"]

if len(admin_token) < 32 or admin_token.startswith("SUBSTITUA_"):
    raise RuntimeError(
        "Configure ADMIN_API_TOKEN com a chave gerada para este ambiente."
    )


admin_bearer = HTTPBearer(
    scheme_name="AdminToken",
    description="Chave administrativa configurada no arquivo .env.",
    auto_error=False,
)

device_bearer = HTTPBearer(
    scheme_name="DeviceToken",
    description="Chave individual emitida para o dispositivo.",
    auto_error=False,
)


def hash_device_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def require_admin(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(admin_bearer),
    ],
) -> None:
    if credentials is None or not secrets.compare_digest(
        credentials.credentials.encode("utf-8"),
        admin_token.encode("utf-8"),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credencial administrativa ausente ou invalida.",
            headers={"WWW-Authenticate": "Bearer"},
        )


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