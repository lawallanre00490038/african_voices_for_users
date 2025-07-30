from fastapi import APIRouter, Depends, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from src.db.db import get_session
from src.auth.utils import get_current_user
from src.auth.schemas import TokenUser
from src.download.service import DownloadService
from src.download.schemas import AudioPreviewResponse, EstimatedSizeResponse
from src.db.models import  Categroy
download_router = APIRouter()
download_service = DownloadService()




@download_router.get(
    "/samples/{language}/preview",
    response_model=AudioPreviewResponse,
    summary="Preview audio samples",
    description="Returns a list of audio samples with presigned URLs for playback.",
)
async def preview_audio_samples(
    language: str,
    limit: int = Query(10, ge=1, le=50),
    gender: str | None = Query(None),
    age: str | None = Query(None),
    education: str | None = Query(None),
    domain: str | None = Query(None),
    category: Categroy | None = Query(Categroy.read, alias="category"),
    session: AsyncSession = Depends(get_session),
):
    
    """
     Get a list of audio samples for preview.
    """
    return await download_service.preview_audio_samples(
        session=session, 
        language=language, 
        limit=limit, 
        gender=gender, 
        age_group=age, 
        education=education, 
        domain=domain, 
        category=category
    )



@download_router.get("/zip/estimate-size/{language}/{pct}", response_model=EstimatedSizeResponse)
async def estimate_zip_size(
    language: str,
    pct: int | float,
    session: AsyncSession = Depends(get_session),
):
    return await download_service.estimate_zip_size_only(
        language=language,
        pct=pct,
        session=session
    )



@download_router.get("/zip/{language}/{pct}", response_class=StreamingResponse)
async def download_zip(
    language: str,
    pct: int | float,
    background_tasks: BackgroundTasks,
    as_excel: bool = True,
    current_user: TokenUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Download a zip file of audio datasets for a given language and percentage.
    """
    return await download_service.download_zip_with_metadata(language, pct, session, background_tasks, current_user, as_excel=as_excel)


