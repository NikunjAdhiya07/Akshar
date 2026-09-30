"""Job persistence. One folder per document, so a failed page never drops the rest."""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.config import JOBS_DIR, ensure_dirs
from app.models import Job

_guard = threading.Lock()
_jobs: dict[str, Job] = {}
_locks: dict[str, threading.Lock] = {}


def _lock_for(job_id: str) -> threading.Lock:
    with _guard:
        if job_id not in _locks:
            _locks[job_id] = threading.Lock()
        return _locks[job_id]


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{12}", job_id):
        raise ValueError("Unknown document")
    return JOBS_DIR / job_id


def safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .")
    return cleaned or "document"


def create_job(source: Path, original_name: str, force_ocr: bool = False) -> Job:
    ensure_dirs()
    job_id = uuid.uuid4().hex[:12]
    folder = JOBS_DIR / job_id
    folder.mkdir(parents=True, exist_ok=True)
    filename = safe_filename(original_name)
    stored = folder / filename
    if Path(source).resolve() != stored.resolve():
        shutil.copyfile(source, stored)
    job = Job(
        id=job_id,
        filename=filename,
        source_name=original_name,
        force_ocr=force_ocr,
        created=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        stage="Queued",
    )
    job.log.append("Document received")
    with _guard:
        _jobs[job_id] = job
        _locks[job_id] = threading.Lock()
    save_job(job)
    try:
        from app.supabase_client import upload_document

        upload_document(job.id, stored, filename)
    except Exception:
        pass
    return job


def save_job(job: Job) -> None:
    folder = job_dir(job.id)
    folder.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(job.to_dict(), ensure_ascii=False, indent=2)
    temporary = folder / "job.json.tmp"
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(folder / "job.json")
    try:
        from app.supabase_client import sync_job

        sync_job(job)
    except Exception:
        pass


def load_job(job_id: str) -> Job:
    with _guard:
        cached = _jobs.get(job_id)
    if cached is not None:
        return cached
    path = job_dir(job_id) / "job.json"
    if not path.is_file():
        raise FileNotFoundError("Document not found")
    job = Job.from_dict(json.loads(path.read_text(encoding="utf-8")))
    with _guard:
        _jobs[job_id] = job
        _locks.setdefault(job_id, threading.Lock())
    return job


def page_dir(job_id: str, index: int) -> Path:
    folder = job_dir(job_id) / "pages" / str(index)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def note(job: Job, message: str) -> None:
    job.log.append(message)
    job.stage = message
    job.revision += 1
    save_job(job)
