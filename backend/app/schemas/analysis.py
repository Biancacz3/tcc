from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, computed_field


class DetectionRead(BaseModel):
    label: str
    confidence: float
    xyxy: list[float] = Field(min_length=4, max_length=4)


class CandidateRead(BaseModel):
    id: int
    xyxy: list[float] = Field(min_length=4, max_length=4)
    status: Literal["detected", "unidentified"]


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
    analysis_state: Literal["legacy", "positive", "review", "no_detections", "inconclusive"] = "legacy"
    candidates: list[CandidateRead] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    method: str = "legacy_full_image"
    plant_bbox: list[float] | None = None

    @computed_field
    @property
    def candidate_count(self) -> int:
        return len(self.candidates)

    @computed_field
    @property
    def unidentified_count(self) -> int:
        return sum(item.status == "unidentified" for item in self.candidates)

    @computed_field
    @property
    def result(self) -> str:
        # Preserve the documented legacy result field for old API clients.
        if self.analysis_state == "legacy":
            return "possible_aphids" if self.detection_count else "no_detections"
        return self.analysis_state

    @computed_field
    @property
    def display_status(self) -> str:
        if self.detection_count:
            return "Praga detectada"
        return {"review": "Revisar imagem", "inconclusive": "Imagem inconclusiva"}.get(
            self.analysis_state, "Nenhuma detecção")

    @computed_field
    @property
    def model_scope(self) -> str:
        # Rotulo abrangente de interface nao amplia o treinamento do modelo.
        return "praga" if self.model_name == "praga-v2-regioes" else "pulgao"
