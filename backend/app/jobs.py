"""Operacoes da fila no banco. Sem threads de inferencia dentro do upload."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session
from app.models.analysis_job import AnalysisJob

MAX_ATTEMPTS = 5
LEASE_SECONDS = 180


def enqueue(session: Session, capture_id: UUID):
    job = session.get(AnalysisJob, capture_id)
    if job is None:
        job = AnalysisJob(capture_id=capture_id, status="queued", attempts=0)
        session.add(job)
    return job


def claim(session: Session, now: datetime | None = None):
    """Lease persistente, SKIP LOCKED no Postgres; commit antes da inferencia."""
    now = now or datetime.now(UTC)
    eligible = or_(
        and_(AnalysisJob.status.in_(("queued", "retry")), AnalysisJob.next_attempt_at <= now),
        and_(AnalysisJob.status == "processing", AnalysisJob.lease_until <= now))
    while True:
        job = session.scalar(select(AnalysisJob).where(eligible)
            .order_by(AnalysisJob.next_attempt_at, AnalysisJob.capture_id)
            .with_for_update(skip_locked=True).limit(1))
        if job is None:
            session.rollback()
            return None
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.last_error = "Limite de tentativas atingido. Confira modelo/worker e solicite nova tentativa."
            job.lease_token = None
            job.lease_until = None
            job.updated_at = now
            session.commit()
            continue
        job.status = "processing"
        job.attempts += 1
        job.lease_token = uuid4()
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        job.updated_at = now
        result = (job.capture_id, job.lease_token)
        session.commit()
        return result


def complete(job: AnalysisJob, analysis_id: UUID):
    job.status = "completed"
    job.analysis_id = analysis_id
    job.last_error = None
    job.lease_until = None
    job.lease_token = None
    job.updated_at = datetime.now(UTC)


def record_failure(session: Session, capture_id: UUID, token: UUID, message: str):
    job = session.scalar(select(AnalysisJob).where(
        AnalysisJob.capture_id == capture_id).with_for_update())
    if job is None or job.lease_token != token or job.status != "processing":
        session.rollback()
        return
    now = datetime.now(UTC)
    job.status = "failed" if job.attempts >= MAX_ATTEMPTS else "retry"
    job.next_attempt_at = now + timedelta(seconds=min(300, 5 * (2 ** (job.attempts - 1))))
    job.last_error = message[:400]
    job.lease_until = None
    job.lease_token = None
    job.updated_at = now
    session.commit()
