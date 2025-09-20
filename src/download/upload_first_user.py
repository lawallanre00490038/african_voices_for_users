import os
import boto3
from fastapi import HTTPException
from fastapi import BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import DownloadLog, GenderEnum
from botocore.exceptions import NoCredentialsError
from src.auth.schemas import TokenUser
from src.config import settings
from src.download.utils import prepare_zip_file


s3_client = boto3.client("s3")

def upload_to_s3(local_path: str, bucket_name: str, object_name: str):
    """Upload file to S3 and return a signed URL."""
    try:
        s3_client.upload_file(local_path, bucket_name, object_name)
        print(f"✅ Uploaded {local_path} to s3://{bucket_name}/{object_name}")
    except NoCredentialsError:
        raise HTTPException(status_code=500, detail="AWS credentials not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload to S3: {e}")

    # Generate signed URL (valid for 1 hour)
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket_name, "Key": object_name},
        ExpiresIn=3600
    )
    return url


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

    if not samples:
        raise HTTPException(404, "No audio samples found. There might not be enough data for the selected filters")

    # Log download in background
    background_tasks.add_task(
        session.add,
        DownloadLog(
            user_id=current_user.id,
            dataset_id=samples[0].dataset_id,
            percentage=pct,
        ),
    )
    await session.commit() 

    try:
        # Build local ZIP
        zip_path, zip_name = await prepare_zip_file(samples, language=language, pct=pct, as_excel=as_excel)

        # Upload to S3
        object_key = f"exports/{zip_name}"   # store inside `exports/` folder
        signed_url = upload_to_s3(zip_path, self.s3_bucket_name, object_key)

        # Cleanup local temp file
        try:
            os.remove(zip_path)
            print(f"🧹 Cleaned up {zip_path}")
        except Exception as e:
            print(f"⚠️ Failed to remove temp zip {zip_path}: {e}")

        return {"download_url": signed_url}

    except Exception as e:
        raise HTTPException(500, f"Failed to generate ZIP: {e}")
