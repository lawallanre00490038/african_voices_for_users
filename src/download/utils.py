from src.db.models import AudioSample
import  io
import pandas as pd
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from typing import Optional
import datetime
import requests
import requests
from io import BytesIO
from math import floor
import aiohttp
import asyncio, os
from enum import Enum
import botocore.exceptions
from fastapi import HTTPException
from src.db.models import AudioSample, Category
from src.download.s3_config import  SUPPORTED_LANGUAGES
from src.download.s3_config import s3
from src.download.s3_config import generate_obs_signed_url, map_sentence_id_to_transcript_obs
from sqlmodel import select, and_
from src.config import settings



# ================================================================================
semaphore = asyncio.Semaphore(5)

async def fetch_audio_stream(session, sample, retries=3):
    print(f"Fetching {sample.get('sentence_id')}")
    for attempt in range(1, retries + 1):
        try:
            async with session.get(sample.get('storage_link'), timeout=10) as resp:
                if resp.status == 200:
                    audio_data = bytearray()
                    async for chunk in resp.content.iter_chunked(1024):
                        audio_data.extend(chunk)
                    print(f"✅ Fetched {sample.get('sentence_id')}")
                    return sample.get('sentence_id'), bytes(audio_data)
                else:
                    print(f"❌ Non-200 status for {sample.get('sentence_id')}: {resp.status}")
        except Exception as e:
            print(f"[Attempt {attempt}] Error streaming {sample.get('sentence_id')}: {e}")
            await asyncio.sleep(2 ** attempt)  # exponential backoff
    print(f"❌ Failed to fetch {sample.get('sentence_id')} after {retries} attempts")
    return sample.get('sentence_id'), None


async def fetch_audio_limited(session, sample):
    async with semaphore:
        return await fetch_audio_stream(session, sample)
    
async def fetch_all(samples):
    connector = aiohttp.TCPConnector(limit=10)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [fetch_audio_limited(session, s) for s in samples]
        print(f"Downloading {len(samples)} samples")
        return await asyncio.gather(*tasks)

# =========================================================================


async def fetch_subset(
    session: AsyncSession, 
    language: str, 
    pct: int | float,

    category: str | None = Category.read_with_spontaneous,
    gender: str | None = None,
    age_group: str | None = None,
    education: str | None = None,
    domain: str | None = None,
        
    ):
    # Count total number of samples
    if language not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Unsupported language: {language}. Only 'Naija', Yoruba', 'Igbo', and 'Hausa' are supported")

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

    total_stmt = select(func.count()).select_from(AudioSample).where(and_(*filters))
    total_result = await session.execute(total_stmt)
    total = total_result.scalar_one()

    print(f"Total samples for {language}: {total}")

    if total == 0:
        return []

    count = max(1, int(floor(total * pct / 100)))

    stmt = (
        select(AudioSample)
        .where(and_(*filters))
        .order_by(AudioSample.id)
        .limit(count)
    )
    result = await session.execute(stmt)
    response = result.scalars().all()

    if not response:
        raise HTTPException(404, "No audio samples found. There might not be enough data for the selected filters")
    
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
        
        for s in response
    ]
    print(response)
    return urls




async def fetch_size(session, url):
    try:
        async with session.head(url, timeout=5) as resp:
            size = int(resp.headers.get("Content-Length", 0))
            return size
    except Exception:
        return 0

async def estimate_total_size(urls):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_size(session, url) for url in urls]
        sizes = await asyncio.gather(*tasks)
        return sum(sizes)



def generate_metadata_buffer(samples, as_excel=True):
    """Create metadata buffer in either Excel or CSV."""
    df = pd.DataFrame([{
        "speaker_id": s.get('annotator_id'),
        "transcript_id": s.get('sentence_id'),
        "transcript": s.get('sentence') or "",
        "audio_path": f"audio/{s.get('sentence_id')}.wav",
        "gender": s.get('gender'),
        "age_group": s.get('age_group'),
        "edu_level": s.get('edu_level'),
        "durations": s.get('durations'),
        "language": s.get('language'),
        "edu_level": s.get('edu_level'),
        "snr": s.get('snr'),
        "domain": s.get('domain'),
        # "category": s.category,
    } for idx, s in enumerate(samples)])

    buf = io.BytesIO()
    if as_excel:
        df.to_excel(buf, index=False)
        return buf, "metadata.xlsx"
    else:
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return io.BytesIO(buf.getvalue().encode()), "metadata.csv"



def generate_readme(language: str, pct: int, as_excel: bool, num_samples: int, sentence_id: Optional[str]=None) -> str:
    return f"""\

        📘 Dataset Export Summary
        =========================
        Language         : {language.upper()}
        Percentage       : {pct}%
        Total Samples    : {num_samples}
        File Format      : {"Excel (.xlsx)" if as_excel else "CSV (.csv)"}
        Date             : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

        📁 Folder Structure
        ===================
        {language}_{pct}pct_<date>/
        ├── metadata.{"xlsx" if as_excel else "csv"}   - Tabular data with metadata
        ├── README.txt                                 - This file
        └── audio/                                     - Folder with audio clips
            ├── {sentence_id}.wav
            ├── {sentence_id}.wav
            └── ...

        📌 Notes
        ========
        - All audio filenames match the metadata rows.
        - File and folder names include language code, percentage, and date.
        - Use Excel or CSV-compatible software to open metadata.
        - If Excel is not supported, a CSV fallback will be provided.

        ✅ Contact
        ==========
        For feedback or support, reach out to the dataset team.
        """



async def stream_zip_with_metadata_links(samples, bucket: str, as_excel=True, language='hausa', pct=10, category: Optional[str] = "read"):
    import zipstream
    import datetime

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    zip_folder = f"{language}_{pct}pct_{today}"
    zip_name = f"{zip_folder}_dataset.zip"

    audio_contents = await fetch_all(samples)
    valid_results = [(sid, data) for sid, data in audio_contents if data is not None]
    print(f"✅ Fetched {len(valid_results)} / {len(samples)}")

    valid_ids = {sid for sid, _ in valid_results}
    filtered_samples = [s for s in samples if s.get('sentence_id') in valid_ids]

    z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)
    
    sentence_id_new = None
    for sentence_id, audio_data in valid_results:
        z.write_iter(f"{zip_folder}/audio/{sentence_id}.wav", [audio_data])
        sentence_id_new=sentence_id

    metadata_buf, metadata_filename = generate_metadata_buffer(filtered_samples, as_excel=as_excel)
    metadata_buf.seek(0)
    z.write_iter(f"{zip_folder}/{metadata_filename}", metadata_buf)

    readme_text = generate_readme(language, pct, as_excel, len(filtered_samples), sentence_id_new)
    z.write_iter(f"{zip_folder}/README.txt", io.BytesIO(readme_text.encode("utf-8")))

    return z, zip_name



async def stream_zip_with_metadata(samples, bucket: str, as_excel=True, language='hausa', pct=10, category: Optional[str] = "read"):
    import zipstream
    import datetime

    sentence_id = None
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    zip_folder = f"{language}_{pct}pct_{today}"
    zip_name = f"{zip_folder}_dataset.zip"

    z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)

    # global 
    # # 1. Add audio files into /audio/
    for idx, s in enumerate(samples):
        audio_filename = f"{zip_folder}/audio/{s.get('sentence_id')}.wav"
        print("the language is: ", language, "\n\n")
        print("the category is: ", category, "\n\n")

        category = s.get('category') or "read_with_spontaneous"

        key = f"{language.lower()}/{category.lower()}/{s.get('sentence_id')}.wav"
        print(f"\nDownloading {audio_filename}", "\n", key)
        s3_stream = s3.get_object(Bucket=settings.OBS_BUCKET_NAME, Key=key)['Body']
        print(f"\nDownloading {audio_filename}", "\n", s3_stream)
        z.write_iter(audio_filename, s3_stream)
        sentence_id=s.get('sentence_id')

    # 2. Add metadata (Excel or CSV)
    metadata_buf, metadata_filename = generate_metadata_buffer(samples, as_excel=as_excel)
    metadata_buf.seek(0)
    z.write_iter(f"{zip_folder}/{metadata_filename}", metadata_buf)

    # 3. Add README
    readme_text = generate_readme(language, pct, as_excel, len(samples), sentence_id)
    z.write_iter(f"{zip_folder}/README.txt", io.BytesIO(readme_text.encode("utf-8")))

    return z, zip_name