"""Inferencia compartilhada pelo worker duravel e endpoint manual legado."""
import hashlib
import time
from io import BytesIO
from pathlib import Path
from uuid import UUID

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.inference import CONFIDENCE, IOU, MODEL_NAME
from app.models.analysis import Analysis
from app.models.capture import Capture
from app.schemas.analysis import DetectionRead, CandidateRead

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000


class AnalysisFailure(Exception):
    def __init__(self, message: str, status_code: int = 503):
        self.status_code = status_code
        super().__init__(message)


class RegionResult(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    detections: list[DetectionRead]
    candidates: list[CandidateRead]
    status: Literal["positive", "review", "no_detections", "inconclusive"]
    warnings: list[str] = Field(default_factory=list)
    method: str = Field(min_length=1, max_length=100)
    plant_bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)


def analyze_capture(session: Session, capture_id: UUID, detector, storage_dir: Path):
    """Retorna (registro, criado). O chamador confirma a transacao."""
    capture = session.get(Capture, capture_id)
    if capture is None:
        raise AnalysisFailure("Captura nao encontrada.", 404)
    statement = select(Analysis).where(
        Analysis.capture_id == capture_id,
        Analysis.configuration_sha256 == detector.configuration_sha256)
    existing = session.scalar(statement)
    if existing is not None:
        return existing, False
    path = (storage_dir / capture.storage_key).resolve()
    if not path.is_relative_to(storage_dir.resolve()):
        raise AnalysisFailure("Caminho da imagem invalido.")
    started = time.perf_counter()
    try:
        with path.open("rb") as file:
            data = file.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != capture.sha256:
            raise AnalysisFailure("Integridade da imagem divergente; original preservado.")
        with Image.open(BytesIO(data), formats=["JPEG"]) as image:
            if image.size != (capture.width, capture.height) or image.width * image.height > MAX_PIXELS:
                raise AnalysisFailure("Dimensoes da imagem divergentes.")
            # Keep the original JPEG/hash intact; the analysis coordinates use
            # the displayed orientation, matching browsers and phone viewers.
            rgb = np.array(ImageOps.exif_transpose(image).convert("RGB"))
        if callable(getattr(detector, "analyze", None)):
            output = RegionResult.model_validate(detector.analyze(rgb, marker_id=capture.marker_id))
            boxes = [item.model_dump() for item in output.detections]
            candidates = [item.model_dump() for item in output.candidates]
            state = output.status
            # Candidate proposals are never counted as positive detections.
            if bool(boxes) != (state == "positive"):
                raise AnalysisFailure("Resultado inconsistente do detector; analise nao armazenada.")
            details = dict(analysis_state=state, candidates=candidates,
                           unidentified_count=sum(item["status"] == "unidentified" for item in candidates),
                           warnings=output.warnings, method=output.method,
                           plant_bbox=output.plant_bbox)
        else:
            boxes = detector.predict(rgb)
            details = dict(analysis_state="legacy", candidates=[], unidentified_count=0, warnings=[],
                           method="legacy_full_image", plant_bbox=None)
    except AnalysisFailure:
        raise
    except Exception as error:
        raise AnalysisFailure("Nao foi possivel analisar esta imagem; confira arquivo e modelo.") from error
    row = Analysis(
        capture_id=capture_id, model_name=MODEL_NAME, model_sha256=detector.sha256,
        configuration_sha256=detector.configuration_sha256, image_sha256=capture.sha256,
        confidence_threshold=CONFIDENCE, iou_threshold=IOU,
        processing_ms=round((time.perf_counter() - started) * 1000),
        width=int(rgb.shape[1]), height=int(rgb.shape[0]),
        detection_count=len(boxes), detections=boxes, **details)
    # Dois processos podem terminar a mesma foto; a chave unica e o savepoint
    # preservam um resultado sem invalidar a transacao do job.
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        existing = session.scalar(statement)
        if existing is None:
            raise
        return existing, False
    return row, True
