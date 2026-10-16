"""Serve a photo or PDF from a link saved in a record (api/attachments.py).

No sign-in: the link is signed, because an <img> tag can't send a token. The
bytes are streamed through the API rather than redirected to Spaces, so the
bucket stays private and the app's "Download" links stay same-origin.
"""
import uuid
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Response

from ..attachments import sig_matches
from ..db import get_pool
from ..storage import get_storage

router = APIRouter(tags=["files"])


def _not_found():
    raise HTTPException(404, "File not found")


@router.api_route("/files/{file_id}/{sig}", methods=["GET", "HEAD"],
                  include_in_schema=False)
def get_file(file_id: str, sig: str):
    try:
        uuid.UUID(file_id)
    except ValueError:
        _not_found()
    if not sig_matches(file_id, sig):
        _not_found()
    with get_pool().connection() as conn:
        row = conn.execute(
            """SELECT f.storage_key, f.mime, f.filename
               FROM files f JOIN companies c ON c.id = f.company_id
               WHERE f.id = %s AND f.deleted_at IS NULL
                 AND c.deleted_at IS NULL""",
            (file_id,)).fetchone()
    if not row:
        _not_found()
    try:
        data = get_storage().read(row["storage_key"])
    except FileNotFoundError:
        _not_found()
    return Response(data, media_type=row["mime"], headers={
        "Content-Disposition":
            "inline; filename*=UTF-8''" + quote(row["filename"] or "file"),
        # A file id never gets different contents.
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    })
