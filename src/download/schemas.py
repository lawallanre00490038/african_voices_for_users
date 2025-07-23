from pydantic import BaseModel
from typing import List


class AudioSamplePreview(BaseModel):
    id: int
    transcription: str
    sample_rate: int
    snr: float
    url: str


class AudioPreviewResponse(BaseModel):
    samples: List[AudioSamplePreview]
