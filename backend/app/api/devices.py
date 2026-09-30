import logging
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.device import Device
from app.schemas.device import (
    DeviceCreate,
    DeviceRead,
    DeviceTokenRead,
)
from app.security import (
    get_authenticated_device,
    hash_device_token,
    require_admin,
)


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/devices",
    tags=["Dispositivos"],
)

DatabaseSession = Annotated[Session, Depends(get_session)]

AuthenticatedDevice = Annotated[
    Device,
    Depends(get_authenticated_device),
]


@router.post(
    "",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
def create_device(
    payload: DeviceCreate,
    session: DatabaseSession,
) -> DeviceRead:
    device = Device(name=payload.name)

    try:
        session.add(device)
        session.flush()

        result = DeviceRead.model_validate(device)

        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Falha ao cadastrar dispositivo.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Nao foi possivel cadastrar o dispositivo.",
        ) from None

    return result


@router.get(
    "",
    response_model=list[DeviceRead],
    dependencies=[Depends(require_admin)],
)
def list_devices(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeviceRead]:
    statement = (
        select(Device)
        .order_by(Device.created_at, Device.id)
        .offset(offset)
        .limit(limit)
    )

    try:
        devices = session.scalars(statement).all()

        return [
            DeviceRead.model_validate(device)
            for device in devices
        ]
    except SQLAlchemyError:
        logger.exception("Falha ao listar dispositivos.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Nao foi possivel listar os dispositivos.",
        ) from None


@router.get(
    "/me",
    response_model=DeviceRead,
)
def identify_device(
    device: AuthenticatedDevice,
) -> DeviceRead:
    return DeviceRead.model_validate(device)


@router.post(
    "/{device_id}/token",
    response_model=DeviceTokenRead,
    dependencies=[Depends(require_admin)],
)
def issue_device_token(
    device_id: UUID,
    session: DatabaseSession,
    response: Response,
) -> DeviceTokenRead:
    try:
        device = session.get(Device, device_id)

        if device is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Dispositivo nao encontrado.",
            )

        if not device.is_active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="O dispositivo esta inativo.",
            )

        token = "tcc_dev_" + secrets.token_urlsafe(32)

        device.api_token_hash = hash_device_token(token)

        result = DeviceTokenRead(
            device_id=device.id,
            token=token,
        )

        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Falha ao emitir credencial do dispositivo.")

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Nao foi possivel emitir a credencial.",
        ) from None

    response.headers["Cache-Control"] = "no-store"

    return result