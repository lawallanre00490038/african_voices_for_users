from fastapi import APIRouter, Depends, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from src.db.db import get_session
from src.auth.utils import get_current_user
from src.auth.schemas import TokenUser
from src.download.service import DownloadService
from src.download.schemas import AudioPreviewResponse

download_router = APIRouter()
download_service = DownloadService()



@download_router.get("/zip/{language}/{pct}", response_class=StreamingResponse)
async def download_zip(
    language: str,
    pct: int,
    background_tasks: BackgroundTasks,
    as_excel: bool = True,
    current_user: TokenUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Download a zip file of audio datasets for a given language and percentage.
    """
    return await download_service.download_zip_with_metadata(language, pct, session, background_tasks, current_user, as_excel=as_excel)



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
    session: AsyncSession = Depends(get_session),
    _current: TokenUser = Depends(get_current_user),
):
    
    """
     Get a list of audio samples for preview.
    """
    return await download_service.preview_audio_samples(
        session, language, limit, gender, age, education, domain
    )
