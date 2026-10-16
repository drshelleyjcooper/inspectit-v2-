"""Photos and PDFs in synced records live in file storage, not the database.

The app embeds each photo or PDF in its record as a base64 data URL
("data:image/jpeg;base64,..."). On every collection write the server lifts
those out: the bytes go to storage (DigitalOcean Spaces in production, via
storage.store_file) and the record keeps a link in their place:

    <APP_BASE_URL>/files/<file id>/<signature>

The app only ever uses these values as an <img src> or a link href, so a link
works wherever the embedded copy did. An <img> can't send a sign-in token,
which is why the link carries its own HMAC signature (like the calendar
feed); the file route checks it and that the file and company still exist.

Only the types the app produces are moved (JPEG/PNG/GIF/WebP photos and
PDFs), and only when the bytes really are that type. Anything else (an SVG,
a mislabelled string) is left in the record exactly as it was.
"""
import hashlib
import hmac
from typing import Any, Dict, List, Optional, Tuple

from . import config
from .storage import parse_data_url, store_file

MOVABLE = {"image/jpeg", "image/png", "image/gif", "image/webp",
           "application/pdf"}
_PREFIX = "data:"


def _looks_like(mime: str, data: bytes) -> bool:
    if mime == "image/jpeg":
        return data[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return data[:8] == b"\x89PNG\r\n\x1a\n"
    if mime == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    if mime == "image/webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime == "application/pdf":
        return b"%PDF-" in data[:1024]
    return False


def link_sig(file_id) -> str:
    return hmac.new(config.FILE_LINK_SECRET.encode(), f"file:{file_id}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def sig_matches(file_id, sig: str) -> bool:
    return hmac.compare_digest(link_sig(file_id), sig or "")


def link_for(file_id) -> str:
    return f"{config.APP_BASE_URL}/files/{file_id}/{link_sig(file_id)}"


def is_inline_file(value: Any) -> bool:
    """Cheap pre-check: could this value be an embedded photo/PDF?"""
    return isinstance(value, str) and value[:5].lower() == _PREFIX \
        and value.split(";", 1)[0][5:].lower() in MOVABLE


def count_inline(data: Any) -> Tuple[int, int]:
    """(embedded files, their text size in bytes) anywhere in `data`."""
    if is_inline_file(data):
        return 1, len(data)
    n = size = 0
    items = data.values() if isinstance(data, dict) else \
        data if isinstance(data, list) else ()
    for v in items:
        a, b = count_inline(v)
        n += a
        size += b
    return n, size


class _Mover:
    def __init__(self, conn, company_id, user_id):
        self.conn, self.cid, self.uid = conn, company_id, user_id
        self.by_hash: Dict[str, str] = {}
        self.moved = 0                 # embedded values replaced by a link
        self.new_files: List[str] = []  # files stored by this call
        self.new_bytes = 0

    def link(self, value: str, filename: str) -> Optional[str]:
        parsed = parse_data_url(value)
        if not parsed:
            return None
        mime, data = parsed
        mime = mime.lower()
        if mime not in MOVABLE or not data or not _looks_like(mime, data):
            return None
        digest = hashlib.sha256(data).hexdigest()
        if digest not in self.by_hash:
            row = self.conn.execute(
                """SELECT id FROM files WHERE company_id = %s AND sha256 = %s
                     AND deleted_at IS NULL ORDER BY created_at LIMIT 1""",
                (self.cid, digest)).fetchone()
            if row:
                fid = str(row["id"])
            else:
                fid = store_file(self.conn, self.cid, self.uid, filename, mime,
                                 data, attached_to_type="collection",
                                 sha256=digest)
                self.new_files.append(fid)
                self.new_bytes += len(data)
            self.by_hash[digest] = link_for(fid)
        self.moved += 1
        return self.by_hash[digest]

    def walk(self, node: Any, filename: str) -> Any:
        if isinstance(node, dict):
            name = node.get("name")
            own = name if isinstance(name, str) and name.strip() else None
            return {k: self.walk(v, own or k) for k, v in node.items()}
        if isinstance(node, list):
            return [self.walk(v, filename) for v in node]
        if is_inline_file(node):
            return self.link(node, filename) or node
        return node


def externalize(conn, company_id, user_id, data: Any):
    """Return (data with embedded photos/PDFs replaced by links, stats).

    Must run inside the caller's transaction: the files rows commit (or roll
    back) with the record that points at them. stats: moved, new_files,
    new_bytes."""
    m = _Mover(conn, company_id, user_id)
    out = m.walk(data, "file")
    return out, {"moved": m.moved, "new_files": m.new_files,
                 "new_bytes": m.new_bytes}
