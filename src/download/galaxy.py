import boto3
import asyncio
from botocore.exceptions import ClientError
from enum import Enum
from src.db.models import Categroy

# Assuming you already have this configured in s3_config.py
from .s3_config import s3  

def build_obs_key(language: str, category: str | Enum, sentence_id: str) -> str:
    """
    Construct the S3 object key for a given sample.
    """
    category_str = category.value if isinstance(category, Enum) else category
    return f"{language.lower()}/{category_str}/{sentence_id}.wav"


def generate_presigned_url(bucket: str, key: str, expiry: int = 3600) -> str:
    """
    Generate a presigned URL to access the audio file in OBS.
    """
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry
        )
        return url
    except ClientError as e:
        raise RuntimeError(f"Failed to generate presigned URL for {key}: {e}")


def get_storage_link(bucket: str, language: str, category: str | Enum, sentence_id: str, expiry: int = 3600) -> str:
    """
    Build the OBS key and return a presigned URL (storage_link replacement).
    """
    key = build_obs_key(language, category, sentence_id)
    return generate_presigned_url(bucket, key, expiry)


def fetch_audio_bytes(bucket: str, language: str, category: str | Enum, sentence_id: str) -> bytes:
    """
    Directly fetch audio bytes from OBS without presigned URL.
    """
    key = build_obs_key(language, category, sentence_id)
    try:
        response = s3.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()
    except ClientError as e:
        raise RuntimeError(f"Failed to fetch audio {sentence_id}: {e}")



from src.download.s3_config import create_presigned_url, BUCKET

async def fetch_audio_stream_galaxy(session, sample, retries=3):
    print(f"Fetching {sample.sentence_id}")

    # Build OBS path: e.g. hausa/read/hau_f_HP2F1_PO1_001.wav
    audio_path = f"{sample.language.lower()}/{sample.category}/{sample.sentence_id}.wav"
    presigned_url = create_presigned_url(audio_path)

    for attempt in range(1, retries + 1):
        try:
            async with session.get(presigned_url, timeout=10) as resp:
                if resp.status == 200:
                    audio_data = bytearray()
                    async for chunk in resp.content.iter_chunked(1024):
                        audio_data.extend(chunk)
                    print(f"✅ Fetched {sample.sentence_id}")
                    return sample.sentence_id, bytes(audio_data)
                else:
                    print(f"❌ Non-200 status for {sample.sentence_id}: {resp.status}")
        except Exception as e:
            print(f"[Attempt {attempt}] Error streaming {sample.sentence_id}: {e}")
            await asyncio.sleep(2 ** attempt)
    print(f"❌ Failed to fetch {sample.sentence_id} after {retries} attempts")
    return sample.sentence_id, None
