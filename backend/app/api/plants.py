"""Historico de observacoes positivas, preservando datas e rotulos do modelo."""
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.db.session import get_session
from app.models.analysis import Analysis
from app.models.capture import Capture
from app.models.device import Device
from app.security import require_user

router = APIRouter(prefix="/plants", tags=["Historico de plantas"], dependencies=[Depends(require_user)])
DatabaseSession = Annotated[Session, Depends(get_session)]
logger = logging.getLogger(__name__)


@router.get("/positive-history")
def positive_history(
    session: DatabaseSession,
    from_date: date | None = None,
    to_date: date | None = None,
    plant_id: Annotated[str | None, Query(max_length=80)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "Data inicial deve ser anterior ou igual a data final.")
    # Uma linha por foto positiva; varios modelos na mesma foto nao duplicam a lista.
    other = aliased(Analysis)
    latest_positive = select(other.id).where(other.capture_id == Capture.id, other.detection_count > 0)\
        .order_by(other.created_at.desc(), other.id.desc()).limit(1).correlate(Capture).scalar_subquery()
    base = select(Capture, Analysis, Device).select_from(Capture).join(Analysis, Analysis.id == latest_positive)\
        .join(Device, Device.id == Capture.device_id)
    local = ZoneInfo("America/Sao_Paulo")
    if from_date:
        base = base.where(Capture.captured_at >= datetime.combine(from_date, time.min, local).astimezone(UTC))
    if to_date:
        exclusive_end = datetime.combine(to_date + timedelta(days=1), time.min, local).astimezone(UTC)
        base = base.where(Capture.captured_at < exclusive_end)
    if plant_id is not None:
        base = base.where(Capture.plant_id == plant_id)
    try:
        total = session.scalar(select(func.count()).select_from(base.subquery()))
        rows = session.execute(base.order_by(Capture.captured_at.desc(), Capture.id.desc())
                               .offset(offset).limit(limit)).all()
        items = []
        for capture, analysis, device in rows:
            items.append(dict(
                capture_id=capture.id, analysis_id=analysis.id, plant_id=capture.plant_id,
                plant_name=capture.plant_name, marker_id=capture.marker_id,
                mission_id=capture.mission_id, plant_side=capture.plant_side,
                device_id=device.id, device_name=device.name,
                captured_at=capture.captured_at, analyzed_at=analysis.created_at,
                detection_count=analysis.detection_count, status="Praga detectada",
                model_name=analysis.model_name,
                analysis_state=analysis.analysis_state, analysis_method=analysis.method,
                historical=True,
                model_labels=sorted({item["label"] for item in analysis.detections}),
            ))
        return dict(items=items, total=total, limit=limit, offset=offset,
                    date_timezone="America/Sao_Paulo", scope="historical_positive",
                    note="Historico de resultados positivos; nao representa o estado atual da planta.")
    except SQLAlchemyError:
        logger.exception("Falha ao consultar historico de plantas.")
        raise HTTPException(503, "Historico indisponivel.") from None
