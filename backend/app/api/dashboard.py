"""Global counts independent of the current gallery page."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.analysis import Analysis
from app.models.analysis_job import AnalysisJob
from app.models.capture import Capture
from app.models.device import Device
from app.provenance import MANUAL_DEVICE_ID
from app.security import require_user
from app.api.captures import latest_analysis_id, state_expression, needs_review_expression

router = APIRouter(prefix="/dashboard", tags=["Resumo"], dependencies=[Depends(require_user)])


@router.get("/summary")
def summary(session: Annotated[Session, Depends(get_session)]):
    try:
        counts = session.execute(select(func.count(Capture.id), func.max(Capture.received_at))).one()
        analysis_counts = dict(session.execute(
            select(state_expression().label("state"), func.count(Capture.id))
            .select_from(Capture).join(Analysis, Analysis.id == latest_analysis_id())
            .group_by("state")).all())
        jobs = dict(session.execute(select(AnalysisJob.status, func.count(AnalysisJob.capture_id))
                                    .group_by(AnalysisJob.status)).all())
        return dict(
            captures=counts[0], last_received_at=counts[1],
            positive_captures=analysis_counts.get("positive", 0),
            review_captures=session.scalar(select(func.count(Capture.id)).select_from(Capture)
                .join(Analysis, Analysis.id == latest_analysis_id()).where(needs_review_expression())),
            queued_captures=sum(jobs.get(state, 0) for state in ("queued", "retry", "processing")),
            failed_captures=jobs.get("failed", 0),
            devices=session.scalar(select(func.count(Device.id)).where(Device.id != MANUAL_DEVICE_ID)),
        )
    except SQLAlchemyError:
        raise HTTPException(503, "Resumo indisponivel.") from None
