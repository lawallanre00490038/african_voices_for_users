from fastapi import HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import select, and_
from src.db.models import AudioSample, DownloadLog, Categroy
from src.auth.schemas import TokenUser
from sqlalchemy.ext.asyncio import AsyncSession
from .utils import fetch_subset
from src.download.s3_config import BUCKET, SUPPORTED_LANGUAGES, VALID_PERCENTAGES, create_presigned_url
import requests
from src.download.utils import stream_zip_with_metadata, estimate_total_size


class DownloadService:
    def __init__(self, s3_bucket_name: str = BUCKET):
    
        self.s3_bucket_name = s3_bucket_name
    
    async def preview_audio_samples(
        self,
        session: AsyncSession,
        language: str,
        limit: int = 10,
        category: str = Categroy.read,
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
        if category:
            filters.append(AudioSample.category == category)
        if age_group:
            filters.append(AudioSample.age_group == age_group)
        if education:
            filters.append(AudioSample.edu_level == education)
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
                "annotator_id": s.annotator_id,
                "sentence_id": s.sentence_id,
                "sentence": s.sentence,
                "storage_link": s.storage_link,
                "gender": s.gender,
                "age_group": s.age_group,
                "edu_level": s.edu_level,
                "durations": s.durations,
                "language": s.language,
                "edu_level": s.edu_level,
                "snr": s.snr,
                "domain": s.domain,
                "category": s.category,

            }
            for s in samples
        ]
        return {"samples": urls}


    async def estimate_zip_size_only(
        self,
        language: str,
        pct: int,
        session: AsyncSession
    ) -> dict:
        if language not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Unsupported language: {language}. Only Naija and Yoruba")
        # if pct not in VALID_PERCENTAGES:
        #     raise HTTPException(400, f"Invalid percentage: {pct}")

        samples = await fetch_subset(session, language, pct)
        if not samples:
            raise HTTPException(404, "No samples available")

        est_size_bytes = estimate_total_size(samples)
        return {
            "estimated_size_bytes": est_size_bytes,
            "estimated_size_mb": round(est_size_bytes / (1024**2), 2),
            "sample_count": len(samples),
        }



  
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
        # est_size_bytes = estimate_total_size(samples, self.s3_bucket_name)
        # print(f"Estimated ZIP size: {round(est_size_bytes / (1024**2), 2)} MB")

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