import boto3
import zipstream
import asyncio
from celery import Celery
from sqlalchemy.orm import Session
from src.db.models import DownloadLog
from src.db.db import get_session, get_sync_session
from sqlalchemy.ext.asyncio import AsyncSession
from src.config import settings
import requests
from src.download.s3_config import BUCKET_AWS

celery = Celery("worker", broker="redis://localhost:6379/0")
s3 = boto3.client("s3")





@celery.task
def create_dataset_zip_s3(
    request_id, language, pct, category, gender, age_group, education, domain, as_excel
):
    from src.download.service import DownloadService
    download_service = DownloadService(s3_bucket_name=BUCKET_AWS)
    session: AsyncSession = get_session()
    db: Session = get_sync_session()
    try:
        # 1. Get subset
        samples = run_async(download_service.filter_core(
            session=session,
            language=language,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
            pct=pct,
        )
    )

        # 2. Create streaming zip
        z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)

        for s in samples:
            filename = s.storage_link.split("/")[-1]
            print(f"⏳ Downloading {filename}")

            if s.storage_link.startswith("https://storage.googleapis.com"):
                # Fetch from GCP
                resp = requests.get(s.storage_link, stream=True, timeout=60)
                print(f"⚠️ Skipped {filename}, status={resp.status_code}")
                if resp.status_code == 200:
                    z.write_iter(filename, resp.iter_content(chunk_size=8192))
                else:
                    print(f"⚠️ Skipped {filename}, status={resp.status_code}")
            else:
                # Assume S3 key
                body = s3.get_object(Bucket=BUCKET_AWS, Key=s.storage_link)["Body"]
                print(f"✅ Fetched {filename} from S3")
                z.write_iter(filename, body.iter_chunks())

        # 3. Upload zip to S3 (multipart) safely
        zip_key = f"exports/{request_id}.zip"
        multipart_upload = s3.create_multipart_upload(Bucket=BUCKET_AWS, Key=zip_key)
        print(f"✅ Created multipart upload for {zip_key}")

        MIN_PART_SIZE = 5 * 1024 * 1024  # 5 MB
        buffer = bytearray()
        parts, part_number = [], 1

        for chunk in z:  # stream chunks from zipstream
            buffer.extend(chunk)

            # Upload whenever buffer reaches 5MB
            while len(buffer) >= MIN_PART_SIZE:
                part_data = bytes(buffer[:MIN_PART_SIZE])
                resp = s3.upload_part(
                    Bucket=BUCKET_AWS,
                    Key=zip_key,
                    PartNumber=part_number,
                    UploadId=multipart_upload["UploadId"],
                    Body=part_data,
                )
                parts.append({"ETag": resp["ETag"], "PartNumber": part_number})
                part_number += 1
                buffer = buffer[MIN_PART_SIZE:]

        # Final flush (last part can be < 5MB)
        if buffer:
            resp = s3.upload_part(
                Bucket=BUCKET_AWS,
                Key=zip_key,
                PartNumber=part_number,
                UploadId=multipart_upload["UploadId"],
                Body=bytes(buffer),
            )
            parts.append({"ETag": resp["ETag"], "PartNumber": part_number})

        # 4. Complete upload
        s3.complete_multipart_upload(
            Bucket=BUCKET_AWS,
            Key=zip_key,
            UploadId=multipart_upload["UploadId"],
            MultipartUpload={"Parts": parts},
        )

        # 5. Presigned URL
        url = s3.generate_presigned_url(
            "get_object", Params={"Bucket": BUCKET_AWS, "Key": zip_key}, ExpiresIn=3600 * 24
        )

        # 6. Update DB
        log = db.query(DownloadLog).get(request_id)
        log.status = "ready"
        log.download_url = url
        db.commit()

    except Exception as e:
        # If something fails, abort multipart upload
        if "multipart_upload" in locals():
            s3.abort_multipart_upload(
                Bucket=BUCKET_AWS, Key=zip_key, UploadId=multipart_upload["UploadId"]
            )
        log = db.query(DownloadLog).get(request_id)
        log.status = "failed"
        db.commit()
        raise
    finally:
        db.close()





@celery.task
def create_dataset_zip_gcp(
    request_id, language, pct, category, gender, age_group, education, domain, as_excel
):
    from src.download.service import DownloadService
    download_service = DownloadService(s3_bucket_name=settings.S3_BUCKET_NAME)
    session: AsyncSession = get_session()
    db: Session = get_sync_session()
    try:
        # 1. Get subset
        samples = run_async(download_service.filter_core(
            session=session,
            language=language,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
            pct=pct,
        )
    )

        # 2. Create streaming zip
        z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)

        for s in samples:
            filename = s.storage_link.split("/")[-1]

            if s.storage_link.startswith("https://storage.googleapis.com"):
                # Fetch from GCP
                resp = requests.get(s.storage_link, stream=True, timeout=60)
                if resp.status_code == 200:
                    z.write_iter(filename, resp.iter_content(chunk_size=8192))
                else:
                    print(f"⚠️ Skipped {filename}, status={resp.status_code}")
            else:
                # Assume S3 key
                body = s3.get_object(
                    Bucket=settings.S3_BUCKET_NAME, Key=s.storage_link
                )["Body"]
                z.write_iter(filename, body.iter_chunks())

        # 3. Upload zip to S3 (multipart with 5MB buffer)
        zip_key = f"exports/{request_id}.zip"
        multipart_upload = s3.create_multipart_upload(
            Bucket=settings.S3_BUCKET_NAME, Key=zip_key
        )

        MIN_PART_SIZE = 5 * 1024 * 1024
        buffer = bytearray()
        parts, part_number = [], 1

        for chunk in z:
            buffer.extend(chunk)

            while len(buffer) >= MIN_PART_SIZE:
                part_data = bytes(buffer[:MIN_PART_SIZE])
                resp = s3.upload_part(
                    Bucket=settings.S3_BUCKET_NAME,
                    Key=zip_key,
                    PartNumber=part_number,
                    UploadId=multipart_upload["UploadId"],
                    Body=part_data,
                )
                parts.append({"ETag": resp["ETag"], "PartNumber": part_number})
                part_number += 1
                buffer = buffer[MIN_PART_SIZE:]

        # Final flush (last part can be < 5MB)
        if buffer:
            resp = s3.upload_part(
                Bucket=settings.S3_BUCKET_NAME,
                Key=zip_key,
                PartNumber=part_number,
                UploadId=multipart_upload["UploadId"],
                Body=bytes(buffer),
            )
            parts.append({"ETag": resp["ETag"], "PartNumber": part_number})

        # 4. Complete upload
        s3.complete_multipart_upload(
            Bucket=settings.S3_BUCKET_NAME,
            Key=zip_key,
            UploadId=multipart_upload["UploadId"],
            MultipartUpload={"Parts": parts},
        )

        # 5. Presigned URL
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.S3_BUCKET_NAME, "Key": zip_key},
            ExpiresIn=3600 * 24,
        )

        # 6. Update DB
        log = db.query(DownloadLog).get(request_id)
        log.status = "ready"
        log.download_url = url
        db.commit()

    except Exception as e:
        if "multipart_upload" in locals():
            s3.abort_multipart_upload(
                Bucket=settings.S3_BUCKET_NAME,
                Key=zip_key,
                UploadId=multipart_upload["UploadId"],
            )
        log = db.query(DownloadLog).get(request_id)
        log.status = "failed"
        db.commit()
        raise
    finally:
        db.close()













import asyncio

def run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no loop running
        loop = None

    if loop and loop.is_running():
        # We're inside FastAPI event loop
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    else:
        # We're in Celery worker (no event loop running yet)
        return asyncio.run(coro)
