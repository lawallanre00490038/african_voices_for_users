# app/schemas/export.py

from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime

# Schema for creating a job (validates incoming data)
class ExportJobCreate(BaseModel):
    user_id: str
    language: str
    percentage: float = Field(..., gt=0, le=100) # Percentage must be between 1-100

# Schema for returning job status (formats outgoing data)
class ExportJobStatus(BaseModel):
    id: str
    status: str
    language: Optional[str] = None
    percentage: float
    progress_pct: Optional[int] = None
    download_url: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True