
import logging
from typing import List, Tuple
import zipstream
import asyncio
from typing import Iterable, Optional


from src.core.celery_app import celery_app
from src.db.db import get_async_session_maker
from src.db.models import DownloadStatusEnum
from src.crud.crud_export import get_export_job, update_export_job_status
from src.download.s3_config import  s3_obs
from src.config import settings



logger = logging.getLogger(__name__)
MIN_PART_SIZE = 5 * 1024 * 1024

SAMPLE_RATE = 48000
CHANNELS = 1
BYTES_PER_SAMPLE = 2

s3_aws = s3_obs

import nest_asyncio
nest_asyncio.apply()

def s3_stream_bytes(s3_body, chunk_size=64 * 1024) -> Iterable[bytes]:
    """Generator to yield bytes from S3 StreamingBody."""
    while True:
        chunk = s3_body.read(chunk_size)
        if not chunk:
            break
        yield chunk

def buffered_zip_chunks(zip_gen, min_size=5*1024*1024):
    """Yield fixed-size byte chunks from a zipstream generator for S3 multipart upload."""
    buf = bytearray()
    for chunk in zip_gen:
        buf.extend(chunk)
        while len(buf) >= min_size:
            yield bytes(buf[:min_size])
            buf = buf[min_size:]
    if buf:
        yield bytes(buf)

def zip_bytes_generator(zs, min_size=5*1024*1024):
    """Yield contiguous byte chunks suitable for S3 multipart."""
    buf = bytearray()
    for chunk in zs:
        buf.extend(chunk)
        while len(buf) >= min_size:
            yield bytes(buf[:min_size])
            buf = buf[min_size:]
    if buf:
        yield bytes(buf)



def stream_zip_to_s3_blocking(zip_gen, bucket: str, key: str):
    """Upload a zip generator to S3 safely, skipping empty chunks."""
    resp = s3_aws.create_multipart_upload(Bucket=bucket, Key=key)
    upload_id = resp['UploadId']
    parts = []
    part_number = 1

    try:
        for part_bytes in zip_bytes_generator(zip_gen, MIN_PART_SIZE):
            if not part_bytes:  # Skip empty chunks
                continue

            resp = s3_aws.upload_part(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=part_number,
                Body=part_bytes,
                ContentLength=len(part_bytes)
            )
            parts.append({'PartNumber': part_number, 'ETag': resp['ETag']})
            part_number += 1

        # Only complete upload if at least one part was uploaded
        if parts:
            s3_aws.complete_multipart_upload(
                Bucket=bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={'Parts': parts}
            )
        else:
            # No parts uploaded, abort the upload
            s3_aws.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
            raise ValueError(f"No valid parts to upload for S3 key={key}")

    except Exception as e:
        s3_aws.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise






# src/download/tasks.py
import asyncio
from celery import shared_task
from src.core.celery_app import celery_app
from src.db.db import get_async_session_maker
from src.db.models import DownloadStatusEnum
from src.crud.crud_export import get_export_job, update_export_job_status
from src.config import settings
import logging

logger = logging.getLogger(__name__)



@celery_app.task(bind=True, name="exports.create_dataset_zip_s3_task_new", acks_late=True)
def create_dataset_zip_s3_task_new(
    self, 
    job_id: str,
    language: str,
    pct: float | None = None,
    category: str | None = None,
    gender: str | None = None,
    age_group: str | None = None,
    education: str | None = None,
    split: str | None = None,
    domain: str | None = None
):
    """
    Synchronous wrapper that runs async logic with proper loop management.
    Reports progress back to Celery state.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        result = loop.run_until_complete(
            async_create_dataset_zip_s3_impl(
                self,
                job_id,
                language,
                pct,
                category,
                gender,
                age_group,
                education,
                split,
                domain
            )
        )
        return result
    finally:
        loop.close()


async def async_create_dataset_zip_s3_impl(
    task, 
    job_id: str, 
    language: str,
    pct: float | None = None,
    category: str | None = None,
    gender: str | None = None,
    age_group: str | None = None,
    education: str | None = None,
    split: str | None = None,
    domain: str | None = None
    ):
    """Main async implementation."""
    logger.info(f"🚀 Starting export job {job_id}")
    
    session_maker = get_async_session_maker()
    language = language
    pct = pct

    async with session_maker() as session:
        job = await get_export_job(session, job_id)
        if not job:
            logger.error(f"Job not found: {job_id}")
            task.update_state(state='FAILURE', meta={'error': 'Job not found'})
            return
        
        await update_export_job_status(
            session, job_id, DownloadStatusEnum.PROCESSING, progress_pct=0
        )

    export_filename = f"exports/{language}_{pct}pct_{job_id}.zip"
    
    try:
        from src.download.service import DownloadService
        download_service = DownloadService(s3_bucket_name=settings.OBS_BUCKET_NAME)
        
        async with session_maker() as session:
            samples_stream, total_to_process = await download_service.filter_core_stream(
                session=session,
                language=language,
                pct=pct,
                category=category,
                gender=gender,
                age_group=age_group,
                education=education,
                split=split,
                domain=domain
            )
            
            import zipstream
            zs = zipstream.ZipFile(mode='w', compression=zipstream.ZIP_DEFLATED)
            processed_count = 0
            last_sentence_id = "N/A"
            all_metadata_rows = [
                "speaker_id,transcript_id,transcript,audio_path,gender,age_group,education,duration,language,snr,domain\n"
            ]

            async for sample in samples_stream:
                last_sentence_id = sample.sentence_id
                arcname = f"audio/{sample.sentence_id}.wav"
                
                category_str = category or sample.category.value if sample.category else 'read'
                folder = map_category_to_folder(category_str, language)
                key = f"{language.lower()}-test/{folder}/{sample.sentence_id}.wav"
                
                try:
                    obj = s3_obs.get_object(Bucket=settings.OBS_BUCKET_NAME, Key=key)
                    zs.write_iter(arcname, s3_stream_bytes(obj["Body"]))
                except Exception as e:
                    logger.warning(f"Skipping missing audio for job {job_id}: {key} - {e}")
                    continue
                
                row = (
                    f'"{sample.speaker_id}","{sample.sentence_id}","{sample.sentence or ""}","{arcname}",'
                    f'"{sample.gender}","{sample.age_group}","{sample.edu_level}","{sample.duration}",'
                    f'"{sample.language}","{sample.snr}","{sample.domain}"\n'
                )
                all_metadata_rows.append(row)
                
                processed_count += 1
                
                # Update progress every 10 samples
                if processed_count % 10 == 0:
                    progress = int((processed_count / total_to_process) * 95)
                    
                    # Update both Celery state AND database
                    task.update_state(
                        state='PROGRESS',
                        meta={
                            'current': processed_count,
                            'total': total_to_process,
                            'status': f'Processing {processed_count}/{total_to_process}',
                            'job_id': job_id
                        }
                    )
                    
                    async with session_maker() as progress_session:
                        await update_export_job_status(
                            progress_session, job_id, 
                            DownloadStatusEnum.PROCESSING,
                            progress_pct=progress
                        )

            # Finalize zip
            metadata_content = "".join(all_metadata_rows).encode('utf-8')
            zs.write_iter("metadata.csv", [metadata_content])
            
            from .export_helpers import generate_readme
            readme_content = generate_readme(language, pct, False, processed_count, last_sentence_id)
            zs.write_iter("README.txt", [readme_content.encode("utf-8")])
            
            stream_zip_to_s3_blocking(zs, bucket=settings.OBS_BUCKET_NAME, key=export_filename)

        # Generate presigned URL
        download_url = s3_obs.generate_presigned_url(
            'get_object',
            Params={'Bucket': settings.OBS_BUCKET_NAME, 'Key': export_filename},
            ExpiresIn=86400
        )
        
        async with session_maker() as session:
            await update_export_job_status(
                session, job_id, DownloadStatusEnum.READY, 
                download_url=download_url, progress_pct=100
            )

        logger.info(f"✅ Job {job_id} completed: {download_url}")
        return {'job_id': job_id, 'download_url': download_url, 'total_samples': processed_count}

    except Exception as e:
        logger.exception(f"❌ Job {job_id} failed: {e}")
        async with session_maker() as session:
            await update_export_job_status(
                session, job_id, DownloadStatusEnum.FAILED, 
                error_message=str(e), progress_pct=0
            )
        task.update_state(state='FAILURE', meta={'error': str(e)})
        raise





def map_category_to_folder(language: str, category: Optional[str] = None) -> str:
    """
    Maps a given category and language to the corresponding folder name.

    Args:
        category (str): The recording category (e.g., 'spontaneous', 'read').
        language (str): The language of the recording (e.g., 'yoruba', 'english').

    Returns:
        str: The folder name to use for S3 storage.
    """

    language = language.lower()
    category = category.lower()

    # For both Language and Category
    if category == "spontaneous":
        if language in ["yoruba", "naija", "hausa"]:
            return "read-as-spontaneous"
    elif category == "read" and language in ["yoruba"]:
        return "read-as-spontaneous"
    elif category == "read":
        return "read"
    
    return category


