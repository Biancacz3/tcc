from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, computed_field


class DetectionRead(BaseModel):
    label: str
    confidence: float
    xyxy: list[float] = Field(min_length=4, max_length=4)


class AnalysisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    capture_id: UUID
    created_at: datetime
    model_name: str
    model_sha256: str
    image_sha256: str
    confidence_threshold: float
    iou_threshold: float
    processing_ms: int
    width: int
    height: int
    detection_count: int
    detections: list[DetectionRead]

    @computed_field
    @property
    def result(self) -> str:
        return "possible_aphids" if self.detection_count else "no_detections"
