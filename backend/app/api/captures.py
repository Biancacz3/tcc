import errno
import hashlib
import logging
import os
import tempfile
from datetime import UTC, date, datetime, time, timedelta
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import AwareDatetime
from sqlalchemy import select, case, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, aliased

from app.db.session import get_session
from app.models.capture import Capture
from app.models.analysis import Analysis
from app.models.analysis_job import AnalysisJob
from app.models.device import Device
from app.jobs import enqueue
from app.schemas.capture import CaptureRead
from app.security import get_authenticated_device, require_user, require_admin
from app.provenance import MANUAL_DEVICE_ID, MANUAL_DEVICE_NAME


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/captures", tags=["Capturas"])
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/app/storage"))
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
DatabaseSession = Annotated[Session, Depends(get_session)]
AuthenticatedDevice = Annotated[Device, Depends(get_authenticated_device)]


async def read_jpeg(request: Request, device: AuthenticatedDevice) -> bytes:
    # A dependencia autentica o dispositivo antes de ler a imagem.
    return await read_jpeg_body(request)


async def read_manual_jpeg(request: Request, identity=Depends(require_admin)) -> bytes:
    # Browser sessions must pass Origin + CSRF before the body is consumed.
    return await read_jpeg_body(request)


async def read_jpeg_body(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "image/jpeg":
        raise HTTPException(415, "Envie o corpo como image/jpeg.")
    declared_size = request.headers.get("content-length")
    if declared_size is not None:
        try:
            size = int(declared_size)
        except ValueError:
            raise HTTPException(400, "Content-Length invalido.") from None
        if size < 0:
            raise HTTPException(400, "Content-Length invalido.")
        if size > MAX_BYTES:
            raise HTTPException(413, "A imagem deve ter no maximo 10 MiB.")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BYTES:
            raise HTTPException(413, "A imagem deve ter no maximo 10 MiB.")
        body.extend(chunk)
    if not body:
        raise HTTPException(422, "Envie uma imagem JPEG.")
    return bytes(body)


def jpeg_dimensions(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(data), formats=["JPEG"]) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise HTTPException(413, "A imagem deve ter no maximo 20 megapixels.")
            # Decodifica para rejeitar arquivos truncados, nao apenas extensoes falsas.
            image.load()
            return width, height
    except Image.DecompressionBombError:
        raise HTTPException(413, "Resolucao da imagem acima do limite.") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(422, "O arquivo nao e um JPEG valido e completo.") from None


def save_image(key: str, data: bytes) -> None:
    destination = STORAGE_DIR / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, destination)
        # O servidor roda em Linux dentro do Docker. Sincroniza tambem o diretorio.
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            try:
                os.fsync(directory_fd)
            except OSError as error:
                # Alguns bind mounts do Docker Desktop nao sincronizam diretorios.
                if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
                logger.warning("O sistema de arquivos nao suporta fsync de diretorio.")
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def capture_reads(session: Session, captures) -> list[CaptureRead]:
    """Carrega status/resultados em lote para a galeria, sem N+1 consultas."""
    ids = [capture.id for capture in captures]
    if not ids:
        return []
    jobs = {job.capture_id: job for job in session.scalars(
        select(AnalysisJob).where(AnalysisJob.capture_id.in_(ids)))}
    latest = {}
    for row in session.scalars(select(Analysis).where(Analysis.capture_id.in_(ids))
                              .order_by(Analysis.created_at.desc(), Analysis.id.desc())):
        latest.setdefault(row.capture_id, row)
    result = []
    for capture in captures:
        job, analysis = jobs.get(capture.id), latest.get(capture.id)
        result.append(CaptureRead.model_validate(capture).model_copy(update={
            "analysis_status": job.status if job else ("completed" if analysis else "not_scheduled"),
            "detection_count": analysis.detection_count if analysis else None,
            "analysis_state": effective_state(analysis) if analysis else None,
            "candidate_count": len(analysis.candidates or []) if analysis else None,
            "unidentified_count": sum(item.get("status") == "unidentified" for item in (analysis.candidates or [])) if analysis else None,
            "analysis_warnings": list(analysis.warnings or []) if analysis else [],
            "analysis_method": analysis.method if analysis else None,
        }))
    return result


def effective_state(analysis: Analysis) -> str:
    if analysis.analysis_state == "legacy":
        return "positive" if analysis.detection_count else "no_detections"
    return analysis.analysis_state


def latest_analysis_id():
    other = aliased(Analysis)
    return select(other.id).where(other.capture_id == Capture.id).order_by(
        other.created_at.desc(), other.id.desc()).limit(1).correlate(Capture).scalar_subquery()


def state_expression():
    return case((Analysis.analysis_state == "legacy",
                 case((Analysis.detection_count > 0, "positive"), else_="no_detections")),
                else_=Analysis.analysis_state)


def needs_review_expression():
    # A positive prediction can coexist with another unidentified region.
    return or_(state_expression().in_(["review", "inconclusive"]), Analysis.unidentified_count > 0)


def repeat_capture(
    capture: Capture, device: Device, captured_at: datetime,
    digest: str, data: bytes, response: Response, session: Session, metadata: dict,
) -> CaptureRead:
    if (capture.device_id != device.id or capture.sha256 != digest
            or utc(capture.captured_at) != utc(captured_at)
            or any(value is not None and getattr(capture, name) is not None
                   and getattr(capture, name) != value for name, value in metadata.items())):
        raise HTTPException(409, "Este X-Capture-Id ja foi usado com outros dados.")
    # Atualizacao de clientes antigos: preenche somente campos antes ausentes.
    # Nunca troca uma planta ja identificada e ausencia de header nao apaga dados.
    for name, value in metadata.items():
        if value is not None and getattr(capture, name) is None:
            setattr(capture, name, value)
    path = STORAGE_DIR / capture.storage_key
    # Permite recuperar uma imagem perdida em disco por reenvio identico.
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        save_image(capture.storage_key, data)
    enqueue(session, capture.id)
    session.flush()
    result = capture_reads(session, [capture])[0]
    session.commit()
    response.status_code = 200
    return result


@router.post(
    "", response_model=CaptureRead, status_code=201,
    openapi_extra={"requestBody": {"required": True, "content": {
        "image/jpeg": {"schema": {"type": "string", "format": "binary"}}
    }}},
)
def create_capture(
    response: Response,
    device: AuthenticatedDevice,
    session: DatabaseSession,
    data: Annotated[bytes, Depends(read_jpeg)],
    capture_id: Annotated[UUID, Header(alias="X-Capture-Id")],
    captured_at: Annotated[AwareDatetime, Header(alias="X-Captured-At")],
    plant_id: Annotated[str | None, Header(alias="X-Plant-Id", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")] = None,
    plant_name: Annotated[str | None, Header(alias="X-Plant-Name", min_length=1, max_length=120)] = None,
    marker_id: Annotated[int | None, Header(alias="X-Marker-Id", ge=0, le=1023)] = None,
    mission_id: Annotated[str | None, Header(alias="X-Mission-Id", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")] = None,
    passage: Annotated[int | None, Header(alias="X-Passage", ge=1, le=10000)] = None,
    plant_side: Annotated[str | None, Header(alias="X-Plant-Side", min_length=1, max_length=40)] = None,
) -> CaptureRead:
    width, height = jpeg_dimensions(data)
    digest = hashlib.sha256(data).hexdigest()
    device_id = device.id
    metadata = dict(plant_id=plant_id, plant_name=plant_name, marker_id=marker_id,
                    mission_id=mission_id, passage=passage, plant_side=plant_side)
    try:
        existing = session.get(Capture, capture_id)
        if existing is not None:
            return repeat_capture(existing, device, captured_at, digest, data, response, session, metadata)
        capture = Capture(
            id=capture_id, device_id=device_id, captured_at=utc(captured_at),
            storage_key=f"{device_id}/{capture_id}.jpg", sha256=digest,
            size_bytes=len(data), width=width, height=height,
            **metadata,
        )
        session.add(capture)
        try:
            # A chave primaria tambem protege contra reenvios simultaneos.
            session.flush()
        except IntegrityError:
            session.rollback()
            existing = session.get(Capture, capture_id)
            if existing is None:
                raise
            return repeat_capture(existing, device, captured_at, digest, data, response, session, metadata)
        save_image(capture.storage_key, data)
        enqueue(session, capture.id)
        session.flush()
        result = capture_reads(session, [capture])[0]
        session.commit()
        # Confirma recebimento somente depois de persistir arquivo e registro.
        return result
    except (SQLAlchemyError, OSError):
        session.rollback()
        # Nao apaga o JPEG: uma falha de comunicacao no commit pode ser ambigua.
        # O reenvio com o mesmo ID recupera a operacao sem duplicar registros.
        logger.exception("Falha ao persistir captura %s.", capture_id)
        raise HTTPException(503, "Nao foi possivel persistir a captura. Tente novamente com o mesmo ID.") from None


@router.post("/manual", response_model=CaptureRead, status_code=201,
             openapi_extra={"requestBody": {"required": True, "content": {
                 "image/jpeg": {"schema": {"type": "string", "format": "binary"}}
             }}})
def create_manual_capture(
    response: Response,
    session: DatabaseSession,
    data: Annotated[bytes, Depends(read_manual_jpeg)],
    capture_id: Annotated[UUID, Header(alias="X-Capture-Id")],
    captured_at: Annotated[AwareDatetime, Header(alias="X-Captured-At")],
    plant_id: Annotated[str | None, Header(alias="X-Plant-Id", min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.:-]+$")] = None,
    plant_name: Annotated[str | None, Header(alias="X-Plant-Name", min_length=1, max_length=1440)] = None,
    plant_name_encoding: Annotated[str | None, Header(alias="X-Plant-Name-Encoding")] = None,
    marker_id: Annotated[int | None, Header(alias="X-Marker-Id", ge=0, le=1023)] = None,
) -> CaptureRead:
    if plant_name_encoding not in (None, "percent-utf8"):
        raise HTTPException(422, "Codificacao do nome da planta invalida.")
    if plant_name is not None:
        if plant_name_encoding == "percent-utf8":
            try:
                plant_name = unquote(plant_name, encoding="utf-8", errors="strict")
            except UnicodeError:
                raise HTTPException(422, "Nome da planta invalido.") from None
        if not 1 <= len(plant_name.strip()) <= 120 or any(ord(char) < 32 for char in plant_name):
            raise HTTPException(422, "Nome da planta deve ter entre 1 e 120 caracteres.")
    # Validate before creating a virtual source. Never borrow a Raspberry ID or token.
    jpeg_dimensions(data)
    try:
        device = session.get(Device, MANUAL_DEVICE_ID)
        if device is None:
            try:
                with session.begin_nested():
                    device = Device(id=MANUAL_DEVICE_ID, name=MANUAL_DEVICE_NAME,
                                    is_active=False, api_token_hash=None)
                    session.add(device)
                    session.flush()
            except IntegrityError:
                device = session.get(Device, MANUAL_DEVICE_ID)
                if device is None:
                    raise
        return create_capture(response=response, device=device, session=session, data=data,
                              capture_id=capture_id, captured_at=captured_at,
                              plant_id=plant_id, plant_name=plant_name, marker_id=marker_id,
                              mission_id=None, passage=None, plant_side=None)
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "Nao foi possivel preparar o envio manual.") from None


@router.get("", response_model=list[CaptureRead], dependencies=[Depends(require_user)])
def list_captures(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    plant_id: Annotated[str | None, Query(max_length=80)] = None,
    analysis_state: Annotated[str | None, Query(pattern=r"^(positive|review|no_detections|inconclusive|pending|failed)$")] = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[CaptureRead]:
    if from_date and to_date and from_date > to_date:
        raise HTTPException(422, "Data inicial deve ser anterior ou igual a data final.")
    try:
        statement = select(Capture)
        if plant_id is not None:
            statement = statement.where(Capture.plant_id == plant_id)
        local = ZoneInfo("America/Sao_Paulo")
        if from_date:
            statement = statement.where(Capture.captured_at >= datetime.combine(from_date, time.min, local).astimezone(UTC))
        if to_date:
            statement = statement.where(Capture.captured_at < datetime.combine(to_date + timedelta(days=1), time.min, local).astimezone(UTC))
        if analysis_state in ("pending", "failed"):
            statement = statement.join(AnalysisJob, AnalysisJob.capture_id == Capture.id).where(
                AnalysisJob.status.in_(["queued", "retry", "processing"] if analysis_state == "pending" else ["failed"]))
        elif analysis_state is not None:
            statement = statement.join(Analysis, Analysis.id == latest_analysis_id()).where(
                needs_review_expression() if analysis_state == "review" else state_expression() == analysis_state)
        captures = session.scalars(statement.order_by(Capture.received_at.desc(), Capture.id.desc())
                                   .offset(offset).limit(limit)).all()
        return capture_reads(session, captures)
    except SQLAlchemyError:
        logger.exception("Falha ao listar capturas.")
        raise HTTPException(503, "Banco de dados indisponivel.") from None


@router.get("/{capture_id}", response_model=CaptureRead, dependencies=[Depends(require_user)])
def get_capture(capture_id: UUID, session: DatabaseSession):
    try:
        capture = session.get(Capture, capture_id)
        if capture is None:
            raise HTTPException(404, "Captura nao encontrada.")
        return capture_reads(session, [capture])[0]
    except SQLAlchemyError:
        raise HTTPException(503, "Banco de dados indisponivel.") from None


@router.get("/{capture_id}/image", dependencies=[Depends(require_user)])
def get_capture_image(capture_id: UUID, session: DatabaseSession) -> FileResponse:
    try:
        capture = session.get(Capture, capture_id)
    except SQLAlchemyError:
        logger.exception("Falha ao consultar captura.")
        raise HTTPException(503, "Banco de dados indisponivel.") from None
    if capture is None:
        raise HTTPException(404, "Captura nao encontrada.")
    path = STORAGE_DIR / capture.storage_key
    if not path.is_file():
        raise HTTPException(503, "Arquivo da captura indisponivel.")
    return FileResponse(
        path, media_type="image/jpeg", filename=f"{capture_id}.jpg",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )
