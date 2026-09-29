import os
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


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