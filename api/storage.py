"""File binary storage. Local disk in dev; DigitalOcean Spaces (S3 API) in prod.

The database's `files` table stores metadata + storage_key; this module owns
the bytes. Keys are namespaced by company: <company_id>/<uuid>.<ext>
"""
import base64
import mimetypes
import re
import uuid
from pathlib import Path

from . import config

_DATA_URL_RE = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,(.*)$", re.S)

MIME_KIND = {
    "application/pdf": "pdf",
    "image/jpeg": "image", "image/png": "image", "image/gif": "image",
    "image/webp": "image",
}


def parse_data_url(data_url: str):
    """-> (mime, bytes) or None if not a base64 data URL."""
    m = _DATA_URL_RE.match(data_url or "")
    if not m:
        return None
    try:
        return m.group(1), base64.b64decode(m.group(2), validate=False)
    except Exception:
        return None


def _ext_for(mime: str) -> str:
    return mimetypes.guess_extension(mime) or ".bin"


class LocalDiskStorage:
    def __init__(self, root: str):
        self.root = Path(root)

    def save(self, company_id: str, mime: str, data: bytes) -> str:
        key = f"{company_id}/{uuid.uuid4()}{_ext_for(mime)}"
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def url_for(self, key: str, expires_s: int = 900) -> str:
        return f"file://{self.root / key}"


class SpacesStorage:
    """DigitalOcean Spaces via the S3 API (boto3). Used when STORAGE_BACKEND=s3."""

    def __init__(self):
        import boto3  # only required in production

        self.bucket = config.SPACES_BUCKET
        self.client = boto3.client(
            "s3",
            region_name=config.SPACES_REGION,
            endpoint_url=f"https://{config.SPACES_REGION}.digitaloceanspaces.com",
            aws_access_key_id=config.SPACES_KEY,
            aws_secret_access_key=config.SPACES_SECRET,
        )

    def save(self, company_id: str, mime: str, data: bytes) -> str:
        key = f"{company_id}/{uuid.uuid4()}{_ext_for(mime)}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data,
                               ContentType=mime, ACL="private")
        return key

    def url_for(self, key: str, expires_s: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_s)


_backend = None


def get_storage():
    global _backend
    if _backend is None:
        if config.STORAGE_BACKEND == "s3":
            _backend = SpacesStorage()
        else:
            _backend = LocalDiskStorage(config.STORAGE_DIR)
    return _backend


def store_file(conn, company_id, uploaded_by, filename, mime, data: bytes,
               kind=None, attached_to_type=None, attached_to_id=None) -> str:
    """Save bytes + insert a files row. Returns the file id."""
    key = get_storage().save(str(company_id), mime, data)
    row = conn.execute(
        """INSERT INTO files (company_id, uploaded_by, storage_key, filename,
                              mime, size_bytes, kind, attached_to_type, attached_to_id)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
        (company_id, uploaded_by, key, filename or "file", mime, len(data),
         kind or MIME_KIND.get(mime, "pdf"), attached_to_type, attached_to_id),
    ).fetchone()
    return str(row["id"])
