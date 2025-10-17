from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional


from src.db.db import get_session
from src.db.models import  GenderEnum, Category, DownloadStatusEnum
from src.auth.utils import get_current_user
from src.auth.schemas import TokenUser
from src.schemas.export import ExportJobCreate, ExportJobStatus
from src.crud.crud_export import create_export_job, get_export_job
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
from src.schemas.export import ExportJobStatus

# logger
import logging
logger = logging.getLogger(__name__)

def map_all_to_none(value: Optional[str], language: Optional[str] = None) -> Optional[str]:
    if not value:
        return None
    val = value.lower()
    lang = language.lower()

    if val == "all":
        return None

    # For value and language going into DB
    if val == "read":
        if lang in ["yoruba"]:
            print(f"Mapping the category {val} to spontaneous")
            return "spontaneous"
    
    print(f"This is the language coming from the backend")
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



celery_router = APIRouter()


@celery_router.post(
    "/exports/{language}/{pct}/second",
    response_model=ExportJobStatus,
    status_code=202, # Accepted
    summary="Enqueue a dataset export job"
)
async def enqueue_export_job(
    language: str,
    pct: int | float,
    gender: str | None = Query(None),
    age: str | None = Query(None),
    education: str | None = Query(None),
    domain: str | None = Query(None),
    category: str | None = Query(None),
    split: str | None = Query(None),
    current_user: TokenUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Enqueue export job and return job ID for tracking."""
    
    # Sanitize inputs
    gender = map_all_to_none(gender)
    age = map_all_to_none(age)
    education = map_all_to_none(education)
    domain = map_EV_to_EV(domain, language)
    category = map_all_to_none(category, language)

    gender = GenderEnum(gender) if gender else None
    category = Category(category) if category else None

    # Create job record
    user_id = current_user.id
    job_create = ExportJobCreate(
        user_id=user_id, 
        language=language, 
        percentage=pct,
    )
    job = await create_export_job(session=session, job_create=job_create)

    # Enqueue task with job ID
    from src.tasks.export_worker import create_dataset_zip_s3_task_new
    task = create_dataset_zip_s3_task_new.delay(
        job_id=str(job.id), 
        language=language, 
        pct=pct,
        gender=str(gender),
        age_group=age,
        education=education,
        domain=domain,
        category=str(category),
        split=split,
    )

    logger.info(f"Enqueued job {job.id} with task_id {task.id}")
    
    return ExportJobStatus.model_validate(job, from_attributes=True)



@celery_router.get(
    "/exports/status/{request_id}",
    response_model=ExportJobStatus,
    summary="Get the status of an export job"
)
async def get_export_status(
    request_id: UUID | str, 
    session: AsyncSession = Depends(get_session)
):
    """
    Poll this endpoint to get the status and download URL of an export job.
    """
    job = await get_export_job(session, str(request_id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@celery_router.websocket("/ws/export-status/{job_id}")
async def export_status_ws(websocket: WebSocket, job_id: str):
    """
    WebSocket for real-time export progress.
    Clients connect here to see live updates.
    """
    await websocket.accept()
    
    from src.tasks.export_worker import get_async_session_maker
    
    session_maker = get_async_session_maker()
    
    try:
        last_sent_progress = -1
        
        while True:
            async with session_maker() as session:
                job = await get_export_job(session, job_id)
                
                if not job:
                    await websocket.send_json({
                        "status": "NOT_FOUND",
                        "error": "Job not found"
                    })
                    break

                current_progress = job.progress_pct or 0
                
                # Only send if progress changed
                if current_progress != last_sent_progress:
                    status_data = {
                        "job_id": str(job.id),
                        "status": job.status.value,
                        "progress": current_progress,
                        "download_url": job.download_url,
                        "error_message": job.error_message,
                        "created_at": job.created_at.isoformat(),
                        "updated_at": job.updated_at.isoformat(),
                    }
                    await websocket.send_json(status_data)
                    last_sent_progress = current_progress

                # Close when done
                if job.status in [DownloadStatusEnum.READY, DownloadStatusEnum.FAILED]:
                    break
                
                # Poll every 1 second (adjust as needed)
                await asyncio.sleep(1)
                
    except WebSocketDisconnect:
        logger.info(f"Client disconnected from job {job_id}")
    except Exception as e:
        logger.error(f"WebSocket error for job {job_id}: {e}")
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
    finally:
        await websocket.close()