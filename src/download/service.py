from fastapi import HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import select
from src.db.models import AudioSample, DownloadLog
from src.auth.schemas import TokenUser
from sqlalchemy.ext.asyncio import AsyncSession
from .utils import fetch_subset
from src.download.s3_config import BUCKET, SUPPORTED_LANGUAGES, VALID_PERCENTAGES
import boto3

from src.download.utils import stream_zip_with_metadata, estimate_total_size


class DownloadService:
  def __init__(self, s3_bucket_name: str = BUCKET):
      self.s3_bucket_name = s3_bucket_name

  async def preview_audio_samples(
          self,  
          session: AsyncSession, 
          language: str, 
          limit: int = 10
        ):
    
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Unsupported language: {language}")
    
    stmt = (
        select(AudioSample)
        .where(AudioSample.language == language)
        .order_by(AudioSample.id)
        .limit(limit)
    )

    result = await session.execute(stmt)
    samples = result.scalars().all()

    if not samples:
        raise HTTPException(404, "No audio samples found")
    
    # Generate presigned URLs for playback
    urls = [
        {
            "id": s.id,
            "transcription": s.transcription,
            "sample_rate": s.sample_rate,
            "snr": s.snr,
            "url": boto3.client("s3").generate_presigned_url(
                "get_object", Params={"Bucket": self.s3_bucket_name, "Key": s.file_path}, ExpiresIn=3600
            )
        }
        for s in samples
    ]
    return {"samples": urls}
  
  async def download_zip_with_metadata(
    self, 
    language: str, 
    pct: int, 
    session: AsyncSession, 
    background_tasks: BackgroundTasks, 
    current_user: TokenUser,
    as_excel: bool = True
  ):
    
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, f"Unsupported language: {language}")
    if pct not in VALID_PERCENTAGES:
        raise HTTPException(400, f"Invalid percentage: {pct}")

    samples = await fetch_subset(session, language, pct)
    if not samples:
        raise HTTPException(404, "No samples available")

    background_tasks.add_task(
        session.add,
        DownloadLog(
            user_id=current_user.id,
            dataset_id=samples[0].dataset_id,
            percentage=pct,
        ),
    )
    await session.commit() 

    # Optional: Estimate ZIP size (e.g., for showing on frontend)
    est_size_bytes = estimate_total_size(samples, self.s3_bucket_name)
    print(f"Estimated ZIP size: {round(est_size_bytes / (1024**2), 2)} MB")

    # Stream ZIP
    zip_stream, zip_filename = stream_zip_with_metadata(
        samples, self.s3_bucket_name, as_excel=as_excel, language=language, pct=pct
    )

    return StreamingResponse(
        zip_stream,
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename={zip_filename}"
        }
    )