import hashlib
import logging
import time
from io import BytesIO
from typing import Annotated
from uuid import UUID

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from PIL import Image
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.captures import STORAGE_DIR, MAX_BYTES, MAX_PIXELS
from app.db.session import get_session
from app.inference import CONFIDENCE, IOU, MODEL_NAME, analysis_lock, get_detector
from app.models.analysis import Analysis
from app.models.capture import Capture
from app.schemas.analysis import AnalysisRead
from app.security import require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/captures", tags=["Analises"], dependencies=[Depends(require_admin)])
DatabaseSession = Annotated[Session, Depends(get_session)]


@router.get("/{capture_id}/analyses", response_model=list[AnalysisRead])
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


@router.post("/{capture_id}/analyses", response_model=AnalysisRead, status_code=201)
def analyze(capture_id: UUID, session: DatabaseSession, response: Response):
    if not analysis_lock.acquire(blocking=False):
        raise HTTPException(503, "Uma analise esta em andamento. Aguarde e tente novamente.", headers={"Retry-After": "3"})
    try:
        capture = session.get(Capture, capture_id)
        if capture is None:
            raise HTTPException(404, "Captura nao encontrada.")
        try:
            detector = get_detector()
        except Exception:
            logger.exception("Modelo de IA indisponivel.")
            raise HTTPException(503, "Modelo de IA indisponivel. Confira a instalacao do arquivo ONNX e reinicie a API.") from None
        statement = select(Analysis).where(Analysis.capture_id == capture_id,
                                           Analysis.configuration_sha256 == detector.configuration_sha256)
        existing = session.scalar(statement)
        if existing is not None:
            response.status_code = 200
            return AnalysisRead.model_validate(existing)
        path = (STORAGE_DIR / capture.storage_key).resolve()
        if not path.is_relative_to(STORAGE_DIR.resolve()):
            raise HTTPException(503, "Caminho da imagem invalido.")
        started = time.perf_counter()
        try:
            with path.open("rb") as file:
                data = file.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != capture.sha256:
                raise ValueError("Integridade da imagem divergente")
            with Image.open(BytesIO(data), formats=["JPEG"]) as image:
                if image.size != (capture.width, capture.height) or image.width * image.height > MAX_PIXELS:
                    raise ValueError("Dimensoes divergentes")
                if image.getexif().get(274, 1) != 1:
                    raise HTTPException(422, "Esta foto possui rotacao EXIF. Normalize a orientacao antes de enviar uma nova captura.")
                rgb = np.array(image.convert("RGB"))
            boxes = detector.predict(rgb)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Falha ao analisar captura %s.", capture_id)
            raise HTTPException(503, "Nao foi possivel analisar esta imagem. Confira o arquivo e os logs da API.") from None
        row = Analysis(capture_id=capture_id, model_name=MODEL_NAME, model_sha256=detector.sha256,
                       configuration_sha256=detector.configuration_sha256, image_sha256=capture.sha256,
                       confidence_threshold=CONFIDENCE, iou_threshold=IOU,
                       processing_ms=round((time.perf_counter()-started)*1000),
                       width=capture.width, height=capture.height, detection_count=len(boxes), detections=boxes)
        session.add(row)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(statement)
            if existing is None:
                raise
            response.status_code = 200
            return AnalysisRead.model_validate(existing)
        result = AnalysisRead.model_validate(row)
        session.commit()
        return result
    except SQLAlchemyError:
        session.rollback()
        logger.exception("Falha ao persistir analise.")
        raise HTTPException(503, "Nao foi possivel salvar a analise. Tente novamente.") from None
    finally:
        analysis_lock.release()
