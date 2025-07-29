from pydantic import BaseModel
from typing import List, Optional


class AudioSamplePreview(BaseModel):
    id: str
    audio_path: str
    transcript: str
    transcript_id: str
    speaker_id: str
    sample_rate: int
    gender: str
    duration: float
    education: Optional[str] = None
    domain: str
    age: int
    snr: float


class AudioPreviewResponse(BaseModel):
    samples: List[AudioSamplePreview]
