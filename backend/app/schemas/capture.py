from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, computed_field
from app.provenance import MANUAL_DEVICE_ID


class CaptureRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    device_id: UUID
    captured_at: datetime
    received_at: datetime
    sha256: str
    size_bytes: int
    width: int
    height: int
    status: str
    plant_id: str | None = None
    plant_name: str | None = None
    marker_id: int | None = None
    mission_id: str | None = None
    passage: int | None = None
    plant_side: str | None = None
    analysis_status: str = "not_scheduled"
    detection_count: int | None = None
    analysis_state: str | None = None
    candidate_count: int | None = None
    unidentified_count: int | None = None
    analysis_warnings: list[str] = Field(default_factory=list)
    analysis_method: str | None = None

    @computed_field
    @property
    def source(self) -> str:
        return "manual" if self.device_id == MANUAL_DEVICE_ID else "device"

    @computed_field
    @property
    def image_url(self) -> str:
        return f"/api/v1/captures/{self.id}/image"
