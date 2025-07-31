from src.db.models import AudioSample
import  io
import pandas as pd
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from .s3_config import s3
from typing import Optional
import datetime
import requests
import requests
from io import BytesIO
from math import floor
import aiohttp
import asyncio
from fastapi import HTTPException
from src.db.models import AudioSample, Categroy
from src.download.s3_config import  SUPPORTED_LANGUAGES
from sqlmodel import select, and_



# =========================================================================
async def fetch_audio(session, sample):
    async with session.get(sample.storage_link) as resp:
        if resp.status == 200:
            return sample.sentence_id, await resp.read()
        return sample.sentence_id, None

async def fetch_all(samples):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_audio(session, s) for s in samples]
        return await asyncio.gather(*tasks)
# =========================================================================


async def fetch_subset(
    session: AsyncSession, 
    language: str, 
    pct: int | float,

    category: str | None = Categroy.read,
    gender: str | None = None,
    age_group: str | None = None,
    education: str | None = None,
    domain: str | None = None,
        
    ):
    # Count total number of samples
    if language not in SUPPORTED_LANGUAGES:
            raise HTTPException(400, f"Unsupported language: {language}. Only 'Naija' and 'Yoruba' are supported")
    if category == Categroy.spontaneous:
        raise HTTPException(400, f"Unavailable category: {category}. Only 'Read' and 'Read_as_Spontanueos' are available")

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
    print(response)
    return response



def estimate_total_size(samples: list) -> int:
    """
    Estimate total size of audio files in bytes from their public storage_link URLs.
    """
    total = 0
    for s in samples:
        try:
            response = requests.head(s.storage_link, allow_redirects=True, timeout=5)
            response.raise_for_status()
            total += int(response.headers.get("Content-Length", 0))

        except Exception as e:
            print(f"Failed to fetch size for {s.storage_link}: {e}")
    return total



def generate_metadata_buffer(samples, as_excel=True):
    """Create metadata buffer in either Excel or CSV."""
    df = pd.DataFrame([{
        "speaker_id": s.annotator_id,
        "transcript_id": s.sentence_id,
        "transcript": s.sentence,
        "storage_link": s.storage_link,
        "audio_path": f"audio/{s.sentence_id}",
        "gender": s.gender,
        "age_group": s.age_group,
        "edu_level": s.edu_level,
        "durations": s.durations,
        "language": s.language,
        "edu_level": s.edu_level,
        "snr": s.snr,
        "domain": s.domain,
        "category": s.category,
    } for idx, s in enumerate(samples)])

    buf = io.BytesIO()
    if as_excel:
        df.to_excel(buf, index=False)
        return buf, "metadata.xlsx"
    else:
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        return io.BytesIO(buf.getvalue().encode()), "metadata.csv"



def generate_readme(language: str, pct: int, as_excel: bool, num_samples: int) -> str:
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
            ├── hau_m_HS1M2_AK1_001.wav
            ├── hau_m_HS1M2_AK1_002.wav
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



async def stream_zip_with_metadata(samples, bucket: str, as_excel=True, language='hausa', pct=10, category: Optional[str] = "read"):
    import zipstream
    import datetime

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    zip_folder = f"{language}_{pct}pct_{today}"
    zip_name = f"{zip_folder}_dataset.zip"

    z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)
    
    # for s in samples:
    #     audio_filename = f"{zip_folder}/audio/{s.sentence_id}"
    #     resp = requests.get(s.storage_link, stream=True)
    #     if resp.status_code == 200:
    #         z.write_iter(audio_filename, resp.iter_content(chunk_size=4096))

    audio_contents = await fetch_all(samples)
    z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)
    for sentence_id, audio_data in audio_contents:
        if audio_data:
            print("This is the audio data", audio_data)
            z.write_iter(f"{zip_folder}/audio/{sentence_id}.wav", [audio_data])

    # 2. Add metadata (Excel or CSV)
    metadata_buf, metadata_filename = generate_metadata_buffer(samples, as_excel=as_excel)
    metadata_buf.seek(0)
    z.write_iter(f"{zip_folder}/{metadata_filename}", metadata_buf)

    # 3. Add README
    readme_text = generate_readme(language, pct, as_excel, len(samples))
    z.write_iter(f"{zip_folder}/README.txt", io.BytesIO(readme_text.encode("utf-8")))

    return z, zip_name