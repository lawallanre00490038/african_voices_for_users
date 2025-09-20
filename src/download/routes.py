from fastapi import APIRouter, Depends, BackgroundTasks, Query
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi.responses import StreamingResponse
from src.db.db import get_session
from src.auth.utils import get_current_user
from src.auth.schemas import TokenUser
from src.download.service import DownloadService
from src.download.schemas import AudioPreviewResponse, EstimatedSizeResponse
from src.db.models import  Category, GenderEnum
from typing import Optional
from src.config import settings

download_router = APIRouter()
download_service = DownloadService(
    s3_bucket_name=settings.OBS_BUCKET_NAME
)


from typing import Optional

def map_all_to_none(value: Optional[str], language: Optional[str] = None) -> Optional[str]:
    if not value:
        return None

    val = value.lower()
    lang = (language or "").lower()

    if val == "all":
        return None

    if val == "read":
        if lang == "naija":
            print(f"Mapping the category {val} to read")
            return "read"
        if lang in ["igbo", "yoruba", "hausa"]:
            print(f"Mapping the category {val} to read_with_spontaneous")
            return "read_with_spontaneous"

    if val in ["read_as_spontaneous", "spontaneous"]:
        if val == "read_as_spontaneous":
            print(f"Mapping the category {val} to read_with_spontaneous")
            return "read_with_spontaneous"
        if lang in ["naija", "igbo", "yoruba"]:
            return "spontaneous"
        if lang == "hausa":
            print(f"Mapping the category {val} to read_with_spontaneous")
            return "read_with_spontaneous"

    return val



def map_EV_to_EV(category: str | None, language: str | None = None) -> str | None:
    if language.lower() in ["hausa"]:
        if category is None:
            return None
        if category.lower() == "all":
            return None
        if category.lower() == "ec":
            print("Mapping EC to EV for Hausa")
            return "EV"
    return category



@download_router.get(
    "/samples/{language}/preview",
    response_model=AudioPreviewResponse,
    summary="Preview audio samples",
    description="Returns a list of audio samples with presigned URLs for playback.",
)
async def preview_audio_samples(
    language: str,
    limit: int = Query(10, ge=1, le=50),
    gender: str | None = Query(None, alias="gender"),
    age: str | None = Query(None),
    education: str | None = Query(None),
    domain: str | None = Query(None),
    category: str | None = Query(None),

    session: AsyncSession = Depends(get_session),
):
    gender = map_all_to_none(gender)
    age = map_all_to_none(age)
    education = map_all_to_none(education)
    domain = map_EV_to_EV(domain, language)
    category = map_all_to_none(category, language)

    gender = GenderEnum(gender) if gender else None
    category = Category(category) if category else None


    print("This is the category after the mapping: ", category)
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
    gender: str | None = Query(None),
    age: str | None = Query(None),
    education: str | None = Query(None),
    domain: str | None = Query(None),
    category: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
):

    gender = map_all_to_none(gender)
    age = map_all_to_none(age)
    education = map_all_to_none(education)
    domain = map_EV_to_EV(domain, language)
    category = map_all_to_none(category, language)

    gender = GenderEnum(gender) if gender else None
    category = Category(category) if category else None

    print("This is the category after the mapping: ", category)

    return await download_service.estimate_zip_size_only(
        session=session,
        language=language,
        pct=pct,
        category=category,
        gender=gender,
        age_group=age,
        education=education,
        domain=domain,
        
    )


@download_router.get("/zip/{language}/{pct}", response_class=StreamingResponse)
async def download_zip(
    language: str,
    background_tasks: BackgroundTasks,
    pct: int | float,
    
    gender: str | None = Query(None),
    age: str | None = Query(None),
    education: str | None = Query(None),
    domain: str | None = Query(None),
    category: str | None = Query(None),
    
    as_excel: bool = True,
    current_user: TokenUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):

    gender = map_all_to_none(gender)
    age = map_all_to_none(age)
    education = map_all_to_none(education)
    domain = map_EV_to_EV(domain, language)
    category = map_all_to_none(category, language)

    gender = GenderEnum(gender) if gender else None
    category = Category(category) if category else None

    return await download_service.download_zip_with_metadata(
        language=language, 
        pct=pct, 
        session=session, 
        background_tasks=background_tasks, 
        current_user=current_user, 
        as_excel=as_excel,
        gender=gender, 
        age_group=age, 
        education=education, 
        domain=domain, 
        category=category
    )









@download_router.post("/zip/{language}/{pct}/celery", include_in_schema=False)
async def start_download(
    language: str,
    pct: int | float,
    current_user: TokenUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    gender: str | None = Query(None),
    age: str | None = Query(None),
    education: str | None = Query(None),
    domain: str | None = Query(None),
    category: str | None = Query(None),
    as_excel: bool = True,
):

    gender = map_all_to_none(gender)
    age = map_all_to_none(age)
    education = map_all_to_none(education)
    domain = map_EV_to_EV(domain, language)
    category = map_all_to_none(category)

    gender = GenderEnum(gender) if gender else None
    category = Category(category) if category else None

    
    return await download_service.start_zip_job(
        session=session,
        language=language,
        pct=pct,
        current_user=current_user,
        gender=gender,
        age_group=age,
        education=education,
        domain=domain,
        category=category,
        as_excel=as_excel,
    )


@download_router.get("/zip/status/{request_id}", include_in_schema=False)
async def get_zip_status(request_id: str, session: AsyncSession = Depends(get_session)):
    return await download_service.get_zip_status(session, request_id)
