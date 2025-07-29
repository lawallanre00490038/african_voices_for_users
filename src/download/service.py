from fastapi import HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import select, and_
from src.db.models import AudioSample, DownloadLog
from src.auth.schemas import TokenUser
from sqlalchemy.ext.asyncio import AsyncSession
from .utils import fetch_subset
from src.download.s3_config import BUCKET, SUPPORTED_LANGUAGES, VALID_PERCENTAGES, create_presigned_url

from src.download.utils import stream_zip_with_metadata, estimate_total_size


class DownloadService:
    def __init__(self, s3_bucket_name: str = BUCKET):
    
        self.s3_bucket_name = s3_bucket_name
    
    async def preview_audio_samples(
        self,
        session: AsyncSession,
        language: str,
        limit: int = 10,
        gender: str | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,
    ):
        if language not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Unsupported language: {language}")

        filters = [AudioSample.language == language]

        if gender:
            filters.append(AudioSample.gender == gender)
        if age_group:
            filters.append(AudioSample.age == age_group)
        if education:
            filters.append(AudioSample.education == education)
        if domain:
            filters.append(AudioSample.domain == domain)

        stmt = (
            select(AudioSample)
            .where(and_(*filters))
            .order_by(AudioSample.id)
            .limit(limit)
        )

        result = await session.execute(stmt)
        samples = result.scalars().all()

        if not samples:
            raise HTTPException(404, "No audio samples found")

        urls = [
            {
                "id": str(s.id),
                "transcript": s.transcript,
                "transcript_id": s.transcript_id,
                "speaker_id": s.speaker_id,
                "sample_rate": s.sample_rate,
                "gender": s.gender,
                "duration": s.duration,
                "education": s.education,
                "domain": s.domain,
                "age": s.age,
                "snr": s.snr,
                "audio_path": create_presigned_url(s.audio_path),
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