from fastapi import HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlmodel import select, and_
from src.db.models import AudioSample, DownloadLog, Category, GenderEnum
from src.auth.schemas import TokenUser
from sqlalchemy.ext.asyncio import AsyncSession
from .utils import fetch_subset
from src.download.s3_config import  SUPPORTED_LANGUAGES
from src.config import settings
from src.download.utils import stream_zip_with_metadata, estimate_total_size, stream_zip_with_metadata_links
from src.download.s3_config import generate_obs_signed_url, map_sentence_id_to_transcript_obs
from typing import List, Optional
from src.download.tasks import create_dataset_zip_gcp
import uuid



class DownloadService:
    def __init__(self, s3_bucket_name: str = settings.S3_BUCKET_NAME):
    
        self.s3_bucket_name = s3_bucket_name
    
    async def preview_audio_samples(
        self,
        session: AsyncSession,
        language: str,
        limit: int = 10,
        category: str = Category.read_with_spontaneous,
        gender: str | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,
    ):
        samples, _ = await self.filter_core(
            session=session,
            language=language,
            limit=limit,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
        )

        urls = [
            {
                "id": str(s.id),
                "annotator_id": s.annotator_id,
                "sentence_id": s.sentence_id,
                "sentence": s.sentence,
                "storage_link": s.storage_link,
                "gender": s.gender,
                "audio_url_obs": generate_obs_signed_url(
                    language=s.language.lower(),
                    category=s.category,
                    filename=f"{s.sentence_id}.wav",
                    storage_link=s.storage_link,
                ),
                "transcript_url_obs": map_sentence_id_to_transcript_obs(s.sentence_id, s.language, s.category, s.sentence),
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
        return {
            "samples": urls
        }


    async def filter_core(
        self,
        session: AsyncSession,
        language: str,
        limit: Optional[int] = None,
        category: str = Category.read_with_spontaneous,
        gender: str | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,
            
    ):

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

        if limit:
            stmt = (
                select(AudioSample)
                .where(and_(*filters))
                .order_by(AudioSample.id)
                .limit(limit)
            )
        else:
            stmt = (
                select(AudioSample)
                .where(and_(*filters))
                .order_by(AudioSample.id)
            )

        result = await session.execute(stmt)
        samples = result.scalars().all()

        if not samples:
            raise HTTPException(404, "No audio samples found. There might not be enough data for the selected filters")
        
        total = len(samples)
        
        return samples, total
    

    async def estimate_zip_size_only(
        self,
        session: AsyncSession,
        language: str,
        pct: int | float,
        category: str = Category.read_with_spontaneous,
        gender: GenderEnum | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,

    ) -> dict:

        samples = await fetch_subset(
            session=session, 
            language=language,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
            pct=pct
        )

        try:
            total_size = await estimate_total_size([s.get("storage_link") for s in samples])
        except Exception as e:
            raise HTTPException(500, f"Failed to estimate size: {e}")
        return {
            "estimated_size_bytes": total_size,
            "estimated_size_mb": round(total_size / (1024**2), 2),
            "sample_count": len(samples)
        }


  
    async def download_zip_with_metadata(
        self, 
        language: str, 
        pct: int | float, 

        session: AsyncSession, 
        background_tasks: BackgroundTasks, 
        current_user: TokenUser,

        category: str | None = Category.read_with_spontaneous,
        gender: GenderEnum | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,

        as_excel: bool = True
    ):

        samples = await fetch_subset(
            session=session, 
            language=language,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
            pct=pct
        )

        if not samples:
            raise HTTPException(404, "No audio samples found. There might not be enough data for the selected filters")
        background_tasks.add_task(
            session.add,
            DownloadLog(
                user_id=current_user.id,
                dataset_id=samples[0].get("dataset_id"),
                percentage=pct,
            ),
        )
        await session.commit() 

        # Stream ZIP
        try:
            zip_stream, zip_filename = await stream_zip_with_metadata_links(
                    samples, self.s3_bucket_name, as_excel=as_excel, language=language, pct=pct, category=category
                )
        except Exception as e:
            raise HTTPException(500, f"Failed to generate ZIP: {e}")

        return StreamingResponse(
            zip_stream,
            media_type="application/zip",
            headers={
                "Content-Disposition": f"attachment; filename={zip_filename}"
            }
        )







    async def start_zip_job(
        self,
        session: AsyncSession,
        language: str,
        pct: int | float,
        current_user: TokenUser,
        category: str | None = Category.read,
        gender: GenderEnum | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,
        as_excel: bool = True,
    ):
        request_id = str(uuid.uuid4())

        # Log request in DB
        download_log = DownloadLog(
            id=request_id,
            user_id=current_user.id,
            dataset_id=None,
            percentage=pct,
            status="processing"
        )
        session.add(download_log)
        await session.commit()

        # Enqueue async job
        create_dataset_zip_gcp(
            request_id, language, pct, category, gender, age_group, education, domain, as_excel
        )

        return {"request_id": request_id}


    async def get_zip_status(self, session: AsyncSession, request_id: str):
        result = await session.get(DownloadLog, request_id)
        if not result:
            raise HTTPException(404, "No such request")
        return {
            "status": result.status,
            "download_url": result.download_url if result.status == "ready" else None,
        }