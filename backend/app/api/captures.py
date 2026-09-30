import errno
import hashlib
import logging
import os
import tempfile
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, UnidentifiedImageError
from pydantic import AwareDatetime
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.session import get_session
from app.models.capture import Capture
from app.models.device import Device
from app.schemas.capture import CaptureRead
from app.security import get_authenticated_device, require_admin


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/captures", tags=["Capturas"])
STORAGE_DIR = Path(os.environ.get("STORAGE_DIR", "/app/storage"))
MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000
DatabaseSession = Annotated[Session, Depends(get_session)]
AuthenticatedDevice = Annotated[Device, Depends(get_authenticated_device)]


async def read_jpeg(request: Request, device: AuthenticatedDevice) -> bytes:
    # A dependencia autentica o dispositivo antes de ler a imagem.
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
    return value.astimezone(UTC)


def repeat_capture(
    capture: Capture, device: Device, captured_at: datetime,
    digest: str, data: bytes, response: Response,
) -> CaptureRead:
    if (capture.device_id != device.id or capture.sha256 != digest
            or utc(capture.captured_at) != utc(captured_at)):
        raise HTTPException(409, "Este X-Capture-Id ja foi usado com outros dados.")
    path = STORAGE_DIR / capture.storage_key
    # Permite recuperar uma imagem perdida em disco por reenvio identico.
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        save_image(capture.storage_key, data)
    response.status_code = 200
    return CaptureRead.model_validate(capture)


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
) -> CaptureRead:
    width, height = jpeg_dimensions(data)
    digest = hashlib.sha256(data).hexdigest()
    device_id = device.id
    try:
        existing = session.get(Capture, capture_id)
        if existing is not None:
            return repeat_capture(existing, device, captured_at, digest, data, response)
        capture = Capture(
            id=capture_id, device_id=device_id, captured_at=utc(captured_at),
            storage_key=f"{device_id}/{capture_id}.jpg", sha256=digest,
            size_bytes=len(data), width=width, height=height,
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
            return repeat_capture(existing, device, captured_at, digest, data, response)
        save_image(capture.storage_key, data)
        result = CaptureRead.model_validate(capture)
        session.commit()
        # Confirma recebimento somente depois de persistir arquivo e registro.
        return result
    except (SQLAlchemyError, OSError):
        session.rollback()
        # Nao apaga o JPEG: uma falha de comunicacao no commit pode ser ambigua.
        # O reenvio com o mesmo ID recupera a operacao sem duplicar registros.
        logger.exception("Falha ao persistir captura %s.", capture_id)
        raise HTTPException(503, "Nao foi possivel persistir a captura. Tente novamente com o mesmo ID.") from None


@router.get("", response_model=list[CaptureRead], dependencies=[Depends(require_admin)])
def list_captures(
    session: DatabaseSession,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CaptureRead]:
    try:
        captures = session.scalars(
            select(Capture).order_by(Capture.received_at.desc(), Capture.id.desc())
            .offset(offset).limit(limit)
        ).all()
        return [CaptureRead.model_validate(capture) for capture in captures]
    except SQLAlchemyError:
        logger.exception("Falha ao listar capturas.")
        raise HTTPException(503, "Banco de dados indisponivel.") from None


@router.get("/{capture_id}/image", dependencies=[Depends(require_admin)])
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
