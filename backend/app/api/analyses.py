import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.captures import STORAGE_DIR
from app.analysis_service import AnalysisFailure, analyze_capture
from app.db.session import get_session
from app.inference import analysis_lock, get_detector
from app.jobs import complete
from app.models.analysis import Analysis
from app.models.analysis_job import AnalysisJob
from app.models.capture import Capture
from app.schemas.analysis import AnalysisRead
from app.security import require_admin, require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/captures", tags=["Analises"])
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get("/{capture_id}/analyses", response_model=list[AnalysisRead], dependencies=[Depends(require_user)])
def list_analyses(capture_id: UUID, session: DatabaseSession,
                  limit: Annotated[int, Query(ge=1, le=100)] = 20):
    try:
        if session.get(Capture, capture_id) is None:
            raise HTTPException(404, "Captura nao encontrada.")
        return session.scalars(select(Analysis).where(Analysis.capture_id == capture_id)
                               .order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(limit)).all()
    except SQLAlchemyError:
        logger.exception("Falha ao consultar analises.")
        raise HTTPException(503, "Banco de dados indisponivel.") from None


@router.post("/{capture_id}/analyses", response_model=AnalysisRead, status_code=201,
             dependencies=[Depends(require_admin)])
def analyze(capture_id: UUID, session: DatabaseSession, response: Response):
    # Endpoint legado permanece disponivel; upload novo usa fila automaticamente.
    if not analysis_lock.acquire(blocking=False):
        raise HTTPException(503, "Uma analise esta em andamento. Aguarde.", headers={"Retry-After": "3"})
    try:
        if session.get(Capture, capture_id) is None:
            raise HTTPException(404, "Captura nao encontrada.")
        try:
            detector = get_detector()
        except Exception:
            logger.exception("Modelo de IA indisponivel.")
            raise HTTPException(503, "Modelo de IA indisponivel. Confira o ONNX e reinicie a API.") from None
        row, created = analyze_capture(session, capture_id, detector, STORAGE_DIR)
        job = session.scalar(select(AnalysisJob).where(AnalysisJob.capture_id == capture_id).with_for_update())
        if job is not None and job.status != "processing":
            complete(job, row.id)
        result = AnalysisRead.model_validate(row)
        session.commit()
        response.status_code = 201 if created else 200
        return result
    except AnalysisFailure as error:
        session.rollback()
        logger.warning("Falha na analise %s: %s", capture_id, error)
        raise HTTPException(error.status_code, str(error)) from None
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Falha ao persistir analise.")
        raise HTTPException(503, "Nao foi possivel salvar a analise. Tente novamente.") from None
    finally:
        analysis_lock.release()
