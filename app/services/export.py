"""Export a finished job to PDF, PNG, or JPEG. Pages are rebuilt one at a time."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
from PIL import Image

from app.config import BASE_DPI
from app.jobs import job_dir
from app.models import Job
from app.services.document_loader import open_document
from app.services.reconstruct import compose_page, rasterize

QUALITY = {
    "standard": 150,
    "high": 300,
    "print": 600,
    150: 150,
    300: 300,
    600: 600,
}


def export_job(job: Job, fmt: str, dpi_name: str | int) -> Path:
    fmt = fmt.lower()
    if fmt not in {"pdf", "png", "jpg", "jpeg"}:
        raise ValueError("Choose PDF, PNG, or JPG.")
    dpi = QUALITY.get(dpi_name, QUALITY.get(str(dpi_name).lower(), 300))
    folder = job_dir(job.id)
    source = folder / job.filename
    document = open_document(source)
    stem = Path(job.source_name).stem or "document"
    try:
        if fmt == "pdf":
            target = folder / f"{stem}-gujarati.pdf"
            _write_pdf(document, job, dpi, target)
            return target
        images = _page_images(document, job, dpi, "jpeg" if fmt in {"jpg", "jpeg"} else "png")
        if len(images) == 1:
            suffix = "jpg" if fmt in {"jpg", "jpeg"} else "png"
            target = folder / f"{stem}-gujarati.{suffix}"
            target.write_bytes(images[0][1])
            return target
        target = folder / f"{stem}-gujarati.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in images:
                archive.writestr(name, payload)
        return target
    finally:
        document.close()


def _write_pdf(document, job: Job, dpi: int, target: Path) -> None:
    output = pymupdf.open()
    try:
        for page in job.pages:
            blocks = [block for block in job.blocks if block.page == page.index]
            image = document.render(page.index, dpi)
            composed, _issues = compose_page(image, blocks, page.width_pt, page.height_pt, page.index)
            output.insert_pdf(composed)
            composed.close()
        output.save(target, garbage=4, deflate=True)
    finally:
        output.close()


def _page_images(document, job: Job, dpi: int, kind: str) -> list[tuple[str, bytes]]:
    quality = 92 if dpi >= 300 else 85
    pages = []
    suffix = "jpg" if kind == "jpeg" else "png"
    for page in job.pages:
        blocks = [block for block in job.blocks if block.page == page.index]
        image = document.render(page.index, max(dpi, BASE_DPI))
        composed, _issues = compose_page(image, blocks, page.width_pt, page.height_pt, page.index)
        raster_dpi = dpi
        pixels = rasterize(composed, raster_dpi)
        composed.close()
        buffer = io.BytesIO()
        pillow = Image.fromarray(pixels)
        if kind == "jpeg":
            pillow.save(buffer, format="JPEG", quality=quality, subsampling=0)
        else:
            pillow.save(buffer, format="PNG")
        pages.append((f"page-{page.index + 1:02d}.{suffix}", buffer.getvalue()))
    return pages
