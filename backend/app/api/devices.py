import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.device import Device
from app.schemas.device import DeviceCreate, DeviceRead
from app.security import require_admin


logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/devices",
    tags=["Dispositivos"],
    dependencies=[Depends(require_admin)],
)

DatabaseSession = Annotated[Session, Depends(get_session)]


@router.post(
    "",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
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