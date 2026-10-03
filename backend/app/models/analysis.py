from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Analysis(Base):
    __tablename__ = "analyses"
    __table_args__ = (UniqueConstraint("capture_id", "configuration_sha256", name="uq_analysis_configuration"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    capture_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("captures.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    model_name: Mapped[str] = mapped_column(String(80))
    model_sha256: Mapped[str] = mapped_column(String(64))
    configuration_sha256: Mapped[str] = mapped_column(String(64))
    image_sha256: Mapped[str] = mapped_column(String(64))
    confidence_threshold: Mapped[float] = mapped_column(Float)
    iou_threshold: Mapped[float] = mapped_column(Float)
    processing_ms: Mapped[int] = mapped_column(Integer)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    detection_count: Mapped[int] = mapped_column(Integer)
    detections: Mapped[list] = mapped_column(JSON)

