from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field


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

    @computed_field
    @property
    def image_url(self) -> str:
        return f"/api/v1/captures/{self.id}/image"
