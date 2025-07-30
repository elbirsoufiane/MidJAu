import boto3
import os
from botocore.exceptions import ClientError
from io import BytesIO

# Load from env variables (set in Fly.io secrets)
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_ENDPOINT_URL = os.getenv("AWS_ENDPOINT_URL_S3")
BUCKET_NAME = os.getenv("BUCKET_NAME")
REGION = os.getenv("AWS_REGION", "auto")

# Create reusable S3 client
s3 = boto3.client(
    "s3",
    region_name=REGION,
    endpoint_url=AWS_ENDPOINT_URL,
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
)

def upload_file_obj(obj: BytesIO, key: str) -> bool:
    """Upload in-memory file (BytesIO) to Tigris with given key."""
    try:
        s3.upload_fileobj(obj, BUCKET_NAME, key)
        return True
    except ClientError as e:
        print("❌ Upload error:", e)
        return False

def upload_file_path(file_path: str, key: str) -> bool:
    """Upload file from disk to Tigris."""
    try:
        s3.upload_file(file_path, BUCKET_NAME, key)
        return True
    except ClientError as e:
        print("❌ Upload error:", e)
        return False

def download_file_obj(key: str) -> BytesIO:
    """Download file from Tigris to memory (BytesIO)."""
    obj = BytesIO()
    try:
        s3.download_fileobj(BUCKET_NAME, key, obj)
        obj.seek(0)
        return obj
    except ClientError as e:
        print("❌ Download error:", e)
        return None

def download_file_to_path(key: str, file_path: str) -> bool:
    """Download file from Tigris and save to local path."""
    try:
        s3.download_file(BUCKET_NAME, key, file_path)
        return True
    except ClientError as e:
        print("❌ Download error:", e)
        return False

def delete_file(key: str) -> bool:
    """Delete file from Tigris bucket."""
    try:
        s3.delete_object(Bucket=BUCKET_NAME, Key=key)
        return True
    except ClientError as e:
        print("❌ Delete error:", e)
        return False

def generate_presigned_url(key: str, expiration=3600) -> str:
    """Generate a temporary public URL for download (default: 1h)."""
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=expiration,
        )
    except ClientError as e:
        print("❌ URL generation error:", e)
        return None
