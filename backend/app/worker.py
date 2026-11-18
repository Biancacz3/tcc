"""python -m app.worker: fila persistente de fotos, independente do navegador."""
import logging
import os
import signal
import threading
from pathlib import Path

from sqlalchemy import select
from app.analysis_service import AnalysisFailure, analyze_capture
from app.db.session import SessionLocal
from app.inference import analysis_lock, get_detector
from app.jobs import claim, complete, record_failure
from app.models.analysis_job import AnalysisJob

logger = logging.getLogger(__name__)
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/app/storage"))


def run_once(session_factory=SessionLocal, detector_factory=get_detector,
             storage_dir=STORAGE_DIR):
    """Uma foto. Retorno False indica fila vazia, True indica trabalho tentado."""
    with session_factory() as session:
        work = claim(session)
    if work is None:
        return False
    capture_id, token = work
    try:
        with analysis_lock:
            try:
                detector = detector_factory()
            except Exception as error:
                raise AnalysisFailure("Modelo de IA indisponivel. Confira o arquivo ONNX e reinicie o worker.") from error
            with session_factory() as session:
                row, created = analyze_capture(session, capture_id, detector, storage_dir)
                job = session.scalar(select(AnalysisJob).where(
                    AnalysisJob.capture_id == capture_id).with_for_update())
                if job is not None and job.status == "processing" and job.lease_token == token:
                    complete(job, row.id)
                    session.commit()
                    logger.info("ANALISE_CONCLUIDA captura=%s deteccoes=%s", capture_id, row.detection_count)
                else:
                    # Outro worker recuperou o lease; nao confirme um lease antigo.
                    session.rollback()
    except Exception as error:
        logger.exception("Falha na analise da captura %s", capture_id)
        message = str(error) if isinstance(error, AnalysisFailure) else "Falha temporaria no processamento; confira os logs do worker."
        with session_factory() as session:
            record_failure(session, capture_id, token, message)
    return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("Worker iniciado: novas fotos sao analisadas automaticamente.")
    while not stop.is_set():
        try:
            busy = run_once()
        except Exception:
            logger.exception("Fila indisponivel; nova consulta em 5 segundos.")
            stop.wait(5)
        else:
            if not busy:
                stop.wait(1)


if __name__ == "__main__":
    main()
