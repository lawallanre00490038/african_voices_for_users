import boto3
from dotenv import load_dotenv
from src.config import settings

load_dotenv()


s3 = boto3.client("s3")
BUCKET = settings.S3_BUCKET_NAME
SUPPORTED_LANGUAGES = {"yoruba", "hausa", "pidgin", "igbo"}
COLUMNS=[
        "transcript", "sample_rate", "snr",
        "audio_path", "language", "gender", "duration"
    ]
VALID_PERCENTAGES = {5, 20, 40, 60, 80, 100}


# s3 = boto3.client("s3",
#     aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
#     aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
#     region_name=os.getenv("AWS_REGION")
# )

def create_presigned_url(file_path: str, expiration: int = 3600) -> str:
    try:
        response = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": file_path},
            ExpiresIn=expiration
        )
        return response
    except Exception as e:
        raise Exception(f"Failed to generate URL: {e}")
