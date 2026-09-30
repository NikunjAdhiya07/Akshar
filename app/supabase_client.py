"""Supabase client for Akshar.

Local job folders remain the source of truth. When SUPABASE_URL and a key are
set, job metadata can be mirrored to Postgres and source files can go to the
`akshar-documents` bucket. Cloud calls are optional and must never stall
conversion — especially on Vercel serverless.
"""

from __future__ import annotations

import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import (
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_STORAGE_BUCKET,
    SUPABASE_URL,
    supabase_configured,
)

logger = logging.getLogger("akshar.supabase")

# After a failure, skip cloud I/O briefly so conversion is not blocked by
# repeated timeouts (e.g. missing table / storage bucket).
_COOLDOWN_SECONDS = 120.0
_sync_blocked_until = 0.0
_upload_blocked_until = 0.0


def _sync_allowed() -> bool:
    return time.monotonic() >= _sync_blocked_until


def _upload_allowed() -> bool:
    return time.monotonic() >= _upload_blocked_until


def _block_sync(reason: str) -> None:
    global _sync_blocked_until
    _sync_blocked_until = time.monotonic() + _COOLDOWN_SECONDS
    logger.warning("Supabase sync paused for %ss: %s", int(_COOLDOWN_SECONDS), reason)


def _block_upload(reason: str) -> None:
    global _upload_blocked_until
    _upload_blocked_until = time.monotonic() + _COOLDOWN_SECONDS
    logger.warning("Supabase upload paused for %ss: %s", int(_COOLDOWN_SECONDS), reason)


@lru_cache(maxsize=1)
def get_supabase():
    """Return a Supabase client, or None when cloud is not configured."""
    if not supabase_configured():
        return None
    from supabase import create_client

    key = SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY
    return create_client(SUPABASE_URL, key)


def status() -> dict[str, Any]:
    if not supabase_configured():
        return {"configured": False, "connected": False, "message": "Set SUPABASE_URL and a key in .env"}
    if not _sync_allowed():
        return {
            "configured": True,
            "connected": False,
            "url": SUPABASE_URL,
            "bucket": SUPABASE_STORAGE_BUCKET,
            "message": "Temporarily paused after a previous cloud error",
        }
    client = get_supabase()
    if client is None:
        return {"configured": True, "connected": False, "message": "Client could not be created"}
    try:
        client.table("akshar_jobs").select("id").limit(1).execute()
        return {
            "configured": True,
            "connected": True,
            "url": SUPABASE_URL,
            "bucket": SUPABASE_STORAGE_BUCKET,
            "message": "Connected",
        }
    except Exception as exc:
        _block_sync(str(exc))
        return {
            "configured": True,
            "connected": False,
            "url": SUPABASE_URL,
            "bucket": SUPABASE_STORAGE_BUCKET,
            "message": str(exc),
        }


def sync_job(job) -> None:
    """Upsert job metadata. Safe no-op when cloud is down or cooling off."""
    if not _sync_allowed():
        return
    client = get_supabase()
    if client is None:
        return
    try:
        row = {
            "id": job.id,
            "source_name": job.source_name,
            "filename": job.filename,
            "status": job.status,
            "stage": job.stage,
            "page_count": job.page_count,
            "force_ocr": job.force_ocr,
            "export_status": job.export_status,
            "export_name": job.export_name,
            "error": job.error,
            "revision": job.revision,
            "created_at": job.created,
            "payload": job.to_dict(),
        }
        client.table("akshar_jobs").upsert(row).execute()
    except Exception as exc:
        _block_sync(str(exc))


def upload_document(job_id: str, path: Path, remote_name: str | None = None) -> str | None:
    """Upload a local file into the documents bucket. Returns the object path."""
    if not _upload_allowed():
        return None
    client = get_supabase()
    if client is None or not path.is_file():
        return None
    object_path = f"{job_id}/{remote_name or path.name}"
    try:
        data = path.read_bytes()
        client.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            object_path,
            data,
            file_options={"upsert": "true", "content-type": _content_type(path)},
        )
        return object_path
    except Exception as exc:
        _block_upload(str(exc))
        return None


def _content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".json": "application/json",
        ".zip": "application/zip",
    }.get(suffix, "application/octet-stream")
