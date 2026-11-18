from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.analysis import Analysis
from app.models.analysis_job import AnalysisJob
from app.models.capture import Capture
from app.schemas.analysis_job import AnalysisJobRead
from app.security import require_admin, require_user

router = APIRouter(prefix="/captures", tags=["Fila de analise"])
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get("/{capture_id}/analysis-job", response_model=AnalysisJobRead, dependencies=[Depends(require_user)])
def read_job(capture_id: UUID, session: DatabaseSession):
    try:
        if session.get(Capture, capture_id) is None:
            raise HTTPException(404, "Captura nao encontrada.")
        job = session.get(AnalysisJob, capture_id)
        if job is not None:
            return job
        analysis = session.scalar(select(Analysis).where(Analysis.capture_id == capture_id)
            .order_by(Analysis.created_at.desc(), Analysis.id.desc()).limit(1))
        return AnalysisJobRead(capture_id=capture_id,
            status="completed" if analysis else "not_scheduled",
            analysis_id=analysis.id if analysis else None)
    except SQLAlchemyError:
        raise HTTPException(503, "Fila indisponivel.") from None


@router.post("/{capture_id}/analysis-job/retry", response_model=AnalysisJobRead,
             dependencies=[Depends(require_admin)])
def retry_job(capture_id: UUID, session: DatabaseSession):
    try:
        # Bloqueia captura antes de criar job inexistente para serializar retries.
        capture = session.scalar(select(Capture).where(Capture.id == capture_id).with_for_update())
        if capture is None:
            raise HTTPException(404, "Captura nao encontrada.")
        job = session.get(AnalysisJob, capture_id)
        if job is None:
            job = AnalysisJob(capture_id=capture_id, status="queued", attempts=0)
            session.add(job)
        elif job.status == "failed":
            job.status = "queued"
            job.attempts = 0
            job.next_attempt_at = datetime.now(UTC)
            job.last_error = None
            job.lease_until = None
            job.lease_token = None
            job.updated_at = datetime.now(UTC)
        session.flush()
        result = AnalysisJobRead.model_validate(job)
        session.commit()
        return result
    except (SQLAlchemyError, IntegrityError):
        session.rollback()
        raise HTTPException(503, "Nao foi possivel agendar analise.") from None


@router.post("/{capture_id}/reanalyze", response_model=AnalysisJobRead,
             dependencies=[Depends(require_admin)])
def reanalyze_capture(capture_id: UUID, session: DatabaseSession):
    """Queue the deployed pipeline; retained analysis rows remain historical."""
    try:
        capture = session.scalar(select(Capture).where(Capture.id == capture_id).with_for_update())
        if capture is None:
            raise HTTPException(404, "Captura nao encontrada.")
        job = session.scalar(select(AnalysisJob).where(AnalysisJob.capture_id == capture_id).with_for_update())
        if job is not None and job.status == "processing":
            raise HTTPException(409, "Esta foto ja esta em analise. Aguarde a conclusao.")
        if job is None:
            job = AnalysisJob(capture_id=capture_id, status="queued", attempts=0)
            session.add(job)
        elif job.status not in ("queued", "retry"):
            job.status = "queued"
            job.attempts = 0
            job.next_attempt_at = datetime.now(UTC)
            job.last_error = None
            job.lease_until = None
            job.lease_token = None
            job.analysis_id = None
            job.updated_at = datetime.now(UTC)
        session.flush()
        result = AnalysisJobRead.model_validate(job)
        session.commit()
        return result
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "Nao foi possivel agendar nova analise.") from None
