from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict


class AnalysisJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    capture_id: UUID
    status: str
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str | None = None
    analysis_id: UUID | None = None
