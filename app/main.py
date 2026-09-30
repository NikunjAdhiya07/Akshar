"""HTTP API for the Akshar document studio."""

from __future__ import annotations

import base64
import io
import logging
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
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

logger = logging.getLogger("akshar")

_workers: set[str] = set()
_workers_guard = threading.Lock()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_dirs()
    try:
        ensure_fonts()
    except FileNotFoundError as exc:
        # Do not take down the whole function on a missing asset during cold start.
        logger.error("%s", exc)
    if not ON_VERCEL:
        from app.samples import ensure_samples

        ensure_samples()
    yield


app = FastAPI(title="Akshar", lifespan=lifespan)
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
if FONTS_DIR.is_dir():
    app.mount("/fonts", StaticFiles(directory=str(FONTS_DIR)), name="fonts")


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
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        raise HTTPException(500, "Studio UI is missing from the deploy bundle.")
    return FileResponse(index_path)


@app.get("/api/health")
def health():
    from app.supabase_client import status as supabase_status

    return {
        "ok": True,
        "vercel": ON_VERCEL,
        "fonts": {
            "dir": str(FONTS_DIR),
            "regular": (FONTS_DIR / "NotoSansGujarati-Regular.ttf").is_file(),
            "bold": (FONTS_DIR / "NotoSansGujarati-Bold.ttf").is_file(),
        },
        "supabase": supabase_status(),
    }


@app.post("/api/jobs")
async def upload(file: UploadFile = File(...), force_ocr: str = Form("false")):
    from app.services.pipeline import process_job

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

    # Local: return immediately and process in a background thread (fluent polling UI).
    if not ON_VERCEL:
        _start(job.id, process_job)
        return job.to_dict()

    # Vercel: process in-request and return JSON (streaming is unreliable behind the edge).
    # Also attach inline page images + a preview PDF so the UI still works when the
    # next request lands on a different serverless instance with an empty /tmp.
    with _workers_guard:
        _workers.add(job.id)
    try:
        process_job(job.id)
        payload = load_job(job.id).to_dict()
        payload.update(_inline_assets(job.id))
        return payload
    except Exception as exc:
        try:
            failed = load_job(job.id)
            failed.status = "error"
            failed.error = str(exc)
            save_job(failed, cloud=False)
            return failed.to_dict()
        except Exception:
            raise HTTPException(500, str(exc)) from exc
    finally:
        with _workers_guard:
            _workers.discard(job.id)

@app.post("/api/samples/{name}")
def open_sample(name: str, force_ocr: bool = False):
    from app.samples import ensure_samples
    from app.services.pipeline import process_job

    if ON_VERCEL:
        raise HTTPException(400, "Samples are disabled on Vercel. Upload a PDF or image instead.")
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
    from app.services.pipeline import update_translation

    _require_ready(job_id)
    try:
        update_translation(job_id, block_id, body.translation)
    except KeyError as exc:
        raise HTTPException(404, "Text block not found") from exc
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/blocks/{block_id}/retranslate")
def regenerate(job_id: str, block_id: str):
    from app.services.pipeline import retranslate_block

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
    from app.services.pipeline import add_block

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
    from app.services.pipeline import delete_block

    _require_ready(job_id)
    try:
        delete_block(job_id, block_id)
    except KeyError as exc:
        raise HTTPException(404, "Text block not found") from exc
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/pages/{index}/retry")
def retry(job_id: str, index: int):
    from app.services.pipeline import retry_page

    job = _job(job_id)
    if index < 0 or index >= job.page_count:
        raise HTTPException(404, "That page does not exist.")
    job.status = "processing"
    save_job(job)
    _start(job_id, lambda job_id: retry_page(job_id, index))
    return job.to_dict()


@app.post("/api/jobs/{job_id}/pages/{index}/approve")
def approve(job_id: str, index: int, approved: bool = True):
    from app.services.pipeline import approve_page

    job = _job(job_id)
    if index < 0 or index >= len(job.pages):
        raise HTTPException(404, "That page does not exist.")
    approve_page(job_id, index, approved)
    return _job(job_id).to_dict()


@app.post("/api/jobs/{job_id}/export")
def start_export(job_id: str, body: ExportRequest):
    from app.services.export import export_job

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


def _jpeg_data_url(path: Path, max_side: int = 1400, quality: int = 72) -> str | None:
    if not path.is_file():
        return None
    image = Image.open(path).convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def _inline_assets(job_id: str) -> dict:
    """Bundle previews into the JSON response so Vercel /tmp does not have to survive."""
    try:
        job = load_job(job_id)
    except Exception:
        return {}
    if job.status != "ready":
        return {}

    import pymupdf

    inline_pages: list[dict] = []
    pdf = pymupdf.open()
    try:
        for page in job.pages:
            folder = page_dir(job_id, page.index)
            original = _jpeg_data_url(folder / "original.png")
            preview = _jpeg_data_url(folder / "preview.png")
            inline_pages.append({
                "index": page.index,
                "original": original,
                "preview": preview,
            })
            preview_path = folder / "preview.png"
            source = preview_path if preview_path.is_file() else folder / "original.png"
            if not source.is_file():
                continue
            width_pt = float(page.width_pt) or 612.0
            height_pt = float(page.height_pt) or 792.0
            # Keep the PDF MediaBox in original document points (print-ready).
            sheet = pdf.new_page(width=width_pt, height=height_pt)
            with Image.open(source) as img:
                rgb = img.convert("RGB")
                buf = io.BytesIO()
                # Use PNG in the PDF to avoid JPEG recompression artifacts.
                rgb.save(buf, format="PNG", optimize=True)
            sheet.insert_image(sheet.rect, stream=buf.getvalue(), keep_proportion=False)
            sheet.set_mediabox(pymupdf.Rect(0, 0, width_pt, height_pt))
            sheet.set_cropbox(pymupdf.Rect(0, 0, width_pt, height_pt))
        payload: dict = {"inline_pages": inline_pages}
        if pdf.page_count:
            pdf_bytes = pdf.tobytes(deflate=True, garbage=3)
            stem = Path(job.source_name).stem or "document"
            name = f"{stem}-gujarati.pdf"
            payload["inline_export"] = "data:application/pdf;base64," + base64.b64encode(pdf_bytes).decode("ascii")
            payload["inline_export_name"] = name
            payload["export_status"] = "ready"
            payload["export_file"] = name
            payload["export_name"] = name
        return payload
    except Exception as exc:
        logger.warning("inline assets failed for %s: %s", job_id, exc)
        return {"inline_pages": inline_pages} if inline_pages else {}
    finally:
        pdf.close()


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
