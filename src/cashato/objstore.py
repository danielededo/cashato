"""Object storage (MinIO / S3) helper shared by the services.

Uploaded statements are stored in an object bucket, NOT on a shared filesystem,
so the services stay **stateless** (no shared PVC, no same-node coupling):
- ``ingest-api`` streams the upload to a temp file, then ``fput`` it to the bucket;
- the NATS job carries the object **key** (a reference), never the bytes;
- ``etl-worker`` ``fget`` the object to a temp file and parses it.

Retention (keep raw files for reprocessing) is a bucket concern (lifecycle rule),
not a service concern. Config comes from env (endpoint/creds/bucket).
"""

from __future__ import annotations

import os

from minio import Minio

ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
SECURE = os.environ.get("MINIO_SECURE", "false").lower() == "true"
BUCKET = os.environ.get("MINIO_BUCKET", "cashato-uploads")

_client: Minio | None = None


def client() -> Minio:
    """Lazily build the shared MinIO client."""
    global _client
    if _client is None:
        _client = Minio(ENDPOINT, access_key=ACCESS_KEY, secret_key=SECRET_KEY, secure=SECURE)
    return _client


def ensure_bucket(bucket: str = BUCKET) -> None:
    """Create the bucket if it doesn't exist (idempotent; called at ingest startup)."""
    c = client()
    if not c.bucket_exists(bucket):
        c.make_bucket(bucket)


def fput(key: str, path: str, bucket: str = BUCKET) -> None:
    """Upload a local file to ``bucket/key``."""
    client().fput_object(bucket, key, path)


def fget(key: str, dest: str, bucket: str = BUCKET) -> None:
    """Download ``bucket/key`` to a local file ``dest``."""
    client().fget_object(bucket, key, dest)
