from src.db.models import AudioSample
import  io
import pandas as pd
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from .s3_config import s3
import datetime

async def fetch_subset(session: AsyncSession, language: str, pct: int):
    # Count total number of samples
    total_stmt = select(func.count()).select_from(AudioSample).where(AudioSample.language == language)
    total_result = await session.execute(total_stmt)
    total = total_result.scalar_one()

    if total == 0:
        return []

    count = max(1, int(total * pct / 100))

    # Now fetch only the required number of samples
    stmt = (
        select(AudioSample)
        .where(AudioSample.language == language)
        .order_by(AudioSample.id)
        .limit(count)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


def estimate_total_size(samples: list, bucket: str) -> int:
    """Estimate total size of ZIP content in bytes."""
    total = 0
    for s in samples:
        head = s3.head_object(Bucket=bucket, Key=s.audio_path)
        total += head['ContentLength']
    return total


def generate_metadata_buffer(samples, as_excel=True):
    """Create metadata buffer in either Excel or CSV."""
    df = pd.DataFrame([{
        "speaker_id": s.speaker_id,
        "transcript": s.transcript,
        "transcript_id": s.transcript_id,
        "audio_path": f"audio/{idx+1:04d}_clip.wav",
        "sample_rate": s.sample_rate,
        "language": s.language,
        "gender": s.gender,
        "duration": s.duration,
        "education": s.education,
        "domain": s.domain,
        "age": s.age,
        "snr": s.snr,
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
            ├── 0001_clip.wav
            ├── 0002_clip.wav
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



def stream_zip_with_metadata(samples, bucket: str, as_excel=True, language='yoruba', pct=10):
    import zipstream
    import datetime

    today = datetime.datetime.now().strftime("%Y-%m-%d")
    zip_folder = f"{language}_{pct}pct_{today}"
    zip_name = f"{zip_folder}_dataset.zip"

    z = zipstream.ZipFile(mode="w", compression=zipstream.ZIP_DEFLATED)

    # 1. Add audio files into /audio/
    for idx, s in enumerate(samples):
        audio_filename = f"{zip_folder}/audio/{idx+1:04d}_clip.wav"
        s3_stream = s3.get_object(Bucket=bucket, Key=s.audio_path)['Body']
        # s3_stream = create_presigned_url(s.audio_path)
        z.write_iter(audio_filename, s3_stream)

    # 2. Add metadata (Excel or CSV)
    metadata_buf, metadata_filename = generate_metadata_buffer(samples, as_excel=as_excel)
    metadata_buf.seek(0)
    z.write_iter(f"{zip_folder}/{metadata_filename}", metadata_buf)

    # 3. Add README
    readme_text = generate_readme(language, pct, as_excel, len(samples))
    z.write_iter(f"{zip_folder}/README.txt", io.BytesIO(readme_text.encode("utf-8")))

    return z, zip_name