"""Supabase client for Akshar.

Local job folders remain the source of truth. When SUPABASE_URL and a key are
set in .env, job metadata is mirrored to Postgres and source files can be
stored in the `akshar-documents` bucket. Missing or failing cloud calls never
block the studio.
"""

from __future__ import annotations

import logging
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
        return {
            "configured": True,
            "connected": False,
            "url": SUPABASE_URL,
            "bucket": SUPABASE_STORAGE_BUCKET,
            "message": str(exc),
        }


def sync_job(job) -> None:
    """Upsert job metadata. Safe to call on every local save."""
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
        logger.warning("Supabase job sync skipped for %s: %s", job.id, exc)


def upload_document(job_id: str, path: Path, remote_name: str | None = None) -> str | None:
    """Upload a local file into the documents bucket. Returns the object path."""
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
        logger.warning("Supabase upload skipped for %s: %s", object_path, exc)
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
