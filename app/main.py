"""HTTP API for the Akshar document studio."""

from __future__ import annotations

import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import (
    FONTS_DIR,
    HOST,
    JOBS_DIR,
    MAX_UPLOAD_BYTES,
    ON_VERCEL,
    PORT,
    STATIC_DIR,
    ensure_dirs,
    ensure_fonts,
)
from app.jobs import create_job, job_dir, load_job, page_dir, save_job
from app.samples import ensure_samples
from app.services.export import export_job
from app.services.pipeline import (
    add_block,
    approve_page,
    delete_block,
    process_job,
    retranslate_block,
    retry_page,
    update_translation,
)

_workers: set[str] = set()
_workers_guard = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    ensure_fonts()
    # Skip building sample PDFs on every cold start in serverless.
    if not ON_VERCEL:
        ensure_samples()
    yield


app = FastAPI(title="Akshar", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/fonts", StaticFiles(directory=FONTS_DIR), name="fonts")


class TranslationUpdate(BaseModel):
    translation: str


class ManualBlock(BaseModel):
    text: str
    bbox: list[float]


class ExportRequest(BaseModel):
    format: str = "pdf"
    dpi: str | int = "high"


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    from app.supabase_client import status as supabase_status

    return {"ok": True, "supabase": supabase_status()}


@app.post("/api/jobs")
async def upload(file: UploadFile = File(...), force_ocr: str = Form("false")):
    name = file.filename or "document"
    if Path(name).suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"}:
        raise HTTPException(400, "Upload a PDF, JPG, or PNG file.")
    ensure_dirs()
    temporary = JOBS_DIR / f"upload-{uuid.uuid4().hex}"
    size = 0
    try:
        with temporary.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "File is larger than 80 MB.")
                handle.write(chunk)
        job = create_job(temporary, name, force_ocr=force_ocr.lower() in {"1", "true", "yes", "on"})
    finally:
        temporary.unlink(missing_ok=True)
    _start(job.id, process_job)
    return job.to_dict()


@app.post("/api/samples/{name}")
def open_sample(name: str, force_ocr: bool = False):
    samples = ensure_samples()
    if name not in samples:
        raise HTTPException(404, "Unknown sample")
    job = create_job(samples[name], samples[name].name, force_ocr=force_ocr)
    _start(job.id, process_job)
    return job.to_dict()


@app.get("/api/jobs/{job_id}")
def read_job(job_id: str):
    return _job(job_id).to_dict()


@app.get("/api/jobs/{job_id}/pages/{index}/original")
def original(job_id: str, index: int):
    return _image(job_id, index, "original.png")


@app.get("/api/jobs/{job_id}/pages/{index}/preview")
def preview(job_id: str, index: int):
    return _image(job_id, index, "preview.png")


@app.patch("/api/jobs/{job_id}/blocks/{block_id}")
def edit_block(job_id: str, block_id: str, body: TranslationUpdate):
    _require_ready(job_id)
    try:
        update_translation(job_id, block_id, body.translation)
    except KeyError as exc:
        raise HTTPException(404, "Text block not found") from exc
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/blocks/{block_id}/retranslate")
def regenerate(job_id: str, block_id: str):
    _require_ready(job_id)
    try:
        retranslate_block(job_id, block_id)
    except KeyError as exc:
        raise HTTPException(404, "Text block not found") from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/pages/{index}/blocks")
def create_block(job_id: str, index: int, body: ManualBlock):
    _require_ready(job_id)
    if len(body.bbox) != 4 or not body.text.strip():
        raise HTTPException(400, "Draw a box and enter the English text.")
    try:
        add_block(job_id, index, body.bbox, body.text)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _job(job_id).to_dict()


@app.delete("/api/jobs/{job_id}/blocks/{block_id}")
def remove_block(job_id: str, block_id: str):
    _require_ready(job_id)
    try:
        delete_block(job_id, block_id)
    except KeyError as exc:
        raise HTTPException(404, "Text block not found") from exc
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/pages/{index}/retry")
def retry(job_id: str, index: int):
    job = _job(job_id)
    if index < 0 or index >= job.page_count:
        raise HTTPException(404, "That page does not exist.")
    job.status = "processing"
    save_job(job)
    _start(job_id, lambda job_id: retry_page(job_id, index))
    return job.to_dict()


@app.post("/api/jobs/{job_id}/pages/{index}/approve")
def approve(job_id: str, index: int, approved: bool = True):
    job = _job(job_id)
    if index < 0 or index >= len(job.pages):
        raise HTTPException(404, "That page does not exist.")
    approve_page(job_id, index, approved)
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/export")
def start_export(job_id: str, body: ExportRequest):
    job = _job(job_id)
    if job.status != "ready":
        raise HTTPException(409, "Wait until translation finishes before exporting.")
    job.export_status = "running"
    job.export_error = None
    save_job(job)

    def _run(current_id: str) -> None:
        current = load_job(current_id)
        try:
            path = export_job(current, body.format, body.dpi)
            current.export_status = "ready"
            current.export_file = path.name
            current.export_name = path.name
            current.export_error = None
        except Exception as exc:
            current.export_status = "error"
            current.export_error = str(exc)
        current.revision += 1
        save_job(current)

    _start(job_id, _run)
    return job.to_dict()


@app.get("/api/jobs/{job_id}/download")
def download(job_id: str):
    job = _job(job_id)
    if job.export_status != "ready" or not job.export_file:
        raise HTTPException(404, "The export is not ready.")
    path = job_dir(job_id) / job.export_file
    if not path.is_file():
        raise HTTPException(404, "The export file is missing.")
    return FileResponse(path, filename=job.export_name or path.name)


def _job(job_id: str):
    try:
        return load_job(job_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(404, "Document not found") from exc


def _image(job_id: str, index: int, name: str):
    _job(job_id)
    path = page_dir(job_id, index) / name
    if not path.is_file():
        raise HTTPException(404, "Page image is not ready.")
    return FileResponse(path, media_type="image/png")


def _require_ready(job_id: str) -> None:
    job = _job(job_id)
    if job.status == "processing":
        raise HTTPException(409, "Wait until this page finishes processing.")


def _start(job_id: str, target) -> None:
    # Vercel kills background threads when the HTTP response finishes.
    if ON_VERCEL:
        with _workers_guard:
            _workers.add(job_id)
        try:
            target(job_id)
        finally:
            with _workers_guard:
                _workers.discard(job_id)
        return

    def runner():
        try:
            target(job_id)
        finally:
            with _workers_guard:
                _workers.discard(job_id)

    with _workers_guard:
        _workers.add(job_id)
    threading.Thread(target=runner, daemon=True).start()


def main() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)


if __name__ == "__main__":
    main()
