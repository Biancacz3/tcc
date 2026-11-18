from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Capture(Base):
    __tablename__ = "captures"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    device_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("devices.id"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    storage_key: Mapped[str] = mapped_column(String(100), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'received'")
    )
    # Snapshot enviado pelo robo. Capturas antigas permanecem sem identidade.
    plant_id: Mapped[str | None] = mapped_column(String(80), index=True)
    plant_name: Mapped[str | None] = mapped_column(String(120))
    marker_id: Mapped[int | None] = mapped_column(Integer)
    mission_id: Mapped[str | None] = mapped_column(String(80), index=True)
    passage: Mapped[int | None] = mapped_column(Integer)
    plant_side: Mapped[str | None] = mapped_column(String(40))
