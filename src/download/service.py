from fastapi import HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse, FileResponse
from sqlmodel import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from src.db.db import get_session, get_sync_session
from typing import List, Optional
import os, uuid, math, datetime, zipfile, aiohttp, boto3, botocore
from botocore.exceptions import NoCredentialsError
import zipstream, aiohttp, datetime, tempfile
from botocore.exceptions import BotoCoreError
import io
from src.db.models import AudioSample, DownloadLog, Category, GenderEnum
from src.auth.schemas import TokenUser
from src.config import settings
from src.download.s3_config import (
    SUPPORTED_LANGUAGES,
    generate_obs_signed_url,
    map_sentence_id_to_transcript_obs,
    s3_aws,
)
from src.download.utils import (
    stream_zip_with_metadata,
    estimate_total_size,
    stream_zip_with_metadata_links,
    prepare_zip_file,
    generate_metadata_buffer,
    generate_readme,
)
from src.download.tasks import create_dataset_zip_gcp
import aioboto3


def upload_to_s3(local_path: str, bucket_name: str, object_name: str):
    """Upload file to S3 and return a signed URL."""
    try:
        s3_aws.upload_file(local_path, bucket_name, object_name)
        print(f"✅ Uploaded {local_path} to s3://{bucket_name}/{object_name}")
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to S3: {e}")

    # Generate signed URL (valid for 1 hour)
    url = s3_aws.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": object_name},
        ExpiresIn=3600
    )
    return url



class DownloadService:
    def __init__(self, s3_bucket_name: str = settings.S3_BUCKET_NAME):
    
        self.s3_bucket_name = s3_bucket_name
    
    async def preview_audio_samples(
        self,
        session: AsyncSession,
        language: str,
        limit: int = 10,
        category: str = None,
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
        limit: Optional[int] = None,   # absolute count
        pct: Optional[float] = None,   # percentage (0–100)
        category: str = None,
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

        # Always fetch total first
        total_stmt = select(AudioSample.id).where(and_(*filters))
        total_result = await session.execute(total_stmt)
        total = len(total_result.scalars().all())

        if total == 0:
            raise HTTPException(
                404,
                "No audio samples found. There might not be enough data for the selected filters",
            )

        # Compute effective limit
        effective_limit = None
        if pct is not None:
            if not (0 < pct <= 100):
                raise HTTPException(400, "Percentage must be between 0 and 100")
            effective_limit = math.ceil((pct / 100) * total)
        elif limit is not None:
            effective_limit = limit

        stmt = (
            select(AudioSample)
            .where(and_(*filters))
            .order_by(AudioSample.id)
        )
        if effective_limit:
            stmt = stmt.limit(effective_limit)

        result = await session.execute(stmt)
        samples = result.scalars().all()

        return samples, total

    

    async def estimate_zip_size_only(
        self,
        session: AsyncSession,
        language: str,
        pct: int | float,
        category: str = None,
        gender: GenderEnum | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,

    ) -> dict:

        samples,  total = await self.filter_core(
            session=session, 
            language=language,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
            pct=pct
        )

        print("the samples are ", samples)

        try:
            total_size = await estimate_total_size([s.storage_link for s in samples])
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

        category: str = None,
        gender: GenderEnum | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,

        as_excel: bool = True
    ):

        samples, _ = await self.filter_core(
            session=session, 
            language=language,
            category=category,
            gender=gender,
            age_group=age_group,
            education=education,
            domain=domain,
            pct=pct
        )

        if samples:
            print("the samples are ", samples[0])

        if not samples:
            raise HTTPException(404, "No audio samples found. There might not be enough data for the selected filters")
        background_tasks.add_task(
            session.add,
            DownloadLog(
                user_id=current_user.id,
                dataset_id=samples[0].dataset_id,
                percentage=pct,
            ),
        )
        await session.commit() 

        # Stream ZIP
        try:
            # zip_stream, zip_filename = await stream_zip_with_metadata_links(
            #         samples, self.s3_bucket_name, as_excel=as_excel, language=language, pct=pct, category=category
            #     )
             zip_path, zip_name = await prepare_zip_file(samples, language=language, pct=pct, as_excel=as_excel)
        except Exception as e:
            raise HTTPException(500, f"Failed to generate ZIP: {e}")

        # return StreamingResponse(
        #     zip_stream,
        #     media_type="application/zip",
        #     headers={
        #         "Content-Disposition": f"attachment; filename={zip_filename}"
        #     }
        # )
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=zip_name
        )




    # async def download_zip_with_metadata_s3(
    #     self,
    #     language: str, 
    #     pct: int | float, 

    #     session: AsyncSession, 
    #     background_tasks: BackgroundTasks, 
    #     current_user: TokenUser,

    #     category: str = None,
    #     gender: GenderEnum | None = None,
    #     age_group: str | None = None,
    #     education: str | None = None,
    #     domain: str | None = None,

    #     as_excel: bool = True,
    # ):

    #     samples, total = await self.filter_core(
    #         session=session, 
    #         language=language,
    #         category=category,
    #         gender=gender,
    #         age_group=age_group,
    #         education=education,
    #         domain=domain,
    #         pct=pct
    #     )

    #     if samples:
    #         print("the total number of samples is ", total)
    #         print("the samples are ", samples[0])

    #     if not samples:
    #         raise HTTPException(404, "No audio samples found. There might not be enough data for the selected filters")
    #     background_tasks.add_task(
    #         session.add,
    #         DownloadLog(
    #             user_id=current_user.id,
    #             dataset_id=samples[0].dataset_id,
    #             percentage=pct,
    #         ),
    #     )
    #     await session.commit() 

    #     if not samples:
    #         raise HTTPException(404, "No audio samples found.")

    #     today = datetime.datetime.now().strftime("%Y-%m-%d")
    #     zip_folder = f"{language}_{pct}pct_{today}"
    #     zip_name = f"{zip_folder}_dataset.zip"
    #     object_key = f"exports/{zip_name}"

    #     # Stream directly into memory buffer
    #     buffer = io.BytesIO()

    #     with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
    #         async with aiohttp.ClientSession() as session:
    #             for s in samples:
    #                 if not getattr(s, "storage_link", None):
    #                     continue

    #                 audio_filename = f"{zip_folder}/audio/{s.sentence_id}.wav"
    #                 print(f"Downloading {audio_filename}")

    #                 try:
    #                     async with session.get(s.storage_link) as resp:
    #                         if resp.status == 200:
    #                             audio_bytes = await resp.read()
    #                             z.writestr(audio_filename, audio_bytes)
    #                             print(f"The request is {resp}")
    #                             print(f"The storage link is {s.storage_link}")
    #                             print(f"✅ Added {audio_filename}")
    #                         else:
    #                             print(f"The storage link is {s.storage_link}")
    #                             print(f"⚠️ Skipping {s.sentence_id}, HTTP {resp.status}")
    #                 except Exception as e:
    #                     print(f"❌ Error fetching {s.sentence_id}: {e}")

    #         # Add metadata
    #         metadata_buf, metadata_filename = generate_metadata_buffer(samples, as_excel=as_excel)
    #         metadata_buf.seek(0)
    #         z.writestr(f"{zip_folder}/{metadata_filename}", metadata_buf.read())

    #         # Add README
    #         last_id = samples[-1].sentence_id if samples else None
    #         readme_text = generate_readme(language, pct, as_excel, len(samples), last_id)
    #         z.writestr(f"{zip_folder}/README.txt", readme_text)

    #     # Upload to S3 (buffer → S3 directly)
    #     buffer.seek(0)
    #     self.s3.upload_fileobj(buffer, self.bucket, object_key)

    #     # Generate pre-signed URL
    #     signed_url = self.s3.generate_presigned_url(
    #         "get_object",
    #         Params={"Bucket": self.bucket, "Key": object_key},
    #         ExpiresIn=3600,  # 1 hour
    #     )

    #     print(f"✅ Uploaded to s3://{self.bucket}/{object_key}")
    #     print(f"🔗 Signed URL: {signed_url}")
    #     return {"download_url": signed_url}




    async def download_zip_with_metadata_s3(
        self,
        language: str,
        pct: int | float,
        session: AsyncSession,
        background_tasks: BackgroundTasks,
        current_user: TokenUser,
        category: str = None,
        gender: GenderEnum | None = None,
        age_group: str | None = None,
        education: str | None = None,
        domain: str | None = None,
        as_excel: bool = True,
    ):
        # 1. Fetch samples
        samples, total = await self.filter_core(
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
            raise HTTPException(404, "No audio samples found for selected filters")

        print("the total number of samples is ", total)
        # 2. Log download
        background_tasks.add_task(
            session.add,
            DownloadLog(
                user_id=current_user.id,
                dataset_id=samples[0].dataset_id,
                percentage=pct,
            ),
        )
        await session.commit()

        # 3. Prepare ZIP stream
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        zip_folder = f"{language}_{pct}pct_{today}"
        zip_name = f"{zip_folder}_dataset.zip"
        object_key = f"exports/{zip_name}"

        zs = zipstream.ZipFile(mode='w', compression=zipstream.ZIP_DEFLATED)

        async with aiohttp.ClientSession() as http_session:
            for s in samples:
                if not getattr(s, "storage_link", None):
                    continue

                audio_filename = f"{zip_folder}/audio/{s.sentence_id}.wav"

                # Convert async audio generator to bytes
                audio_bytes = b""
                try:
                    async with http_session.get(s.storage_link) as resp:
                        if resp.status == 200:
                            while True:
                                chunk = await resp.content.read(1024 * 1024)
                                if not chunk:
                                    break
                                audio_bytes += chunk
                                print(f"✅ Added {audio_filename}")
                        else:
                            print(f"⚠️ Skipping {s.sentence_id}, HTTP {resp.status}")
                            continue
                except Exception as e:
                    print(f"❌ Error fetching {s.sentence_id}: {e}")
                    continue

                zs.write_iter(audio_filename, iter([audio_bytes]))

        # Add metadata
        metadata_buf, metadata_filename = generate_metadata_buffer(samples, as_excel=as_excel)
        metadata_buf.seek(0)
        zs.write_iter(f"{zip_folder}/{metadata_filename}", iter([metadata_buf.read()]))

        # Add README
        last_id = samples[-1].sentence_id if samples else None
        readme_text = generate_readme(language, pct, as_excel, len(samples), last_id)
        zs.write_iter(f"{zip_folder}/README.txt", iter([readme_text.encode()]))

        # 4. Write ZIP to temporary file and upload
        session_aioboto = aioboto3.Session()
        try:
            with tempfile.NamedTemporaryFile() as tmp:
                for chunk in zs:
                    tmp.write(chunk)
                tmp.seek(0)

                async with session_aioboto.client(
                    "s3",
                    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                    region_name=settings.AWS_REGION,
                    endpoint_url=settings.AWS_ENDPOINT_URL
                ) as s3_client:
                    await s3_client.upload_fileobj(
                        Fileobj=tmp,
                        Bucket=self.s3_bucket_name,
                        Key=object_key
                    )

                    # Generate presigned URL
                    signed_url = await s3_client.generate_presigned_url(
                        ClientMethod="get_object",
                        Params={"Bucket": self.s3_bucket_name, "Key": object_key},
                        ExpiresIn=3600
                    )
                    print(f"✅ Uploaded to s3://{self.s3_bucket_name}/{object_key}")
        except Exception as e:
            raise HTTPException(500, f"Failed to upload to S3: {e}")

        return {"download_url": signed_url}






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