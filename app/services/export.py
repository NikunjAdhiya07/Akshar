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
    """Build a print-ready PDF using each page's original point size (no letterboxing)."""
    output = pymupdf.open()
    try:
        for page in job.pages:
            blocks = [block for block in job.blocks if block.page == page.index]
            width_pt = float(page.width_pt)
            height_pt = float(page.height_pt)
            if width_pt < 1 or height_pt < 1:
                width_pt, height_pt = document.page_size(page.index)
            image = document.render(page.index, dpi)
            composed, _issues = compose_page(image, blocks, width_pt, height_pt, page.index)
            try:
                dest = output.new_page(width=width_pt, height=height_pt)
                dest.show_pdf_page(dest.rect, composed, 0)
                dest.set_mediabox(pymupdf.Rect(0, 0, width_pt, height_pt))
                dest.set_cropbox(pymupdf.Rect(0, 0, width_pt, height_pt))
                dest.set_rotation(0)
            finally:
                composed.close()
        output.save(target, garbage=4, deflate=True, clean=True)
    finally:
        output.close()


def _page_images(document, job: Job, dpi: int, kind: str) -> list[tuple[str, bytes]]:
    """Raster exports match the original page aspect at the selected DPI."""
    quality = 92 if dpi >= 300 else 85
    pages = []
    suffix = "jpg" if kind == "jpeg" else "png"
    for page in job.pages:
        blocks = [block for block in job.blocks if block.page == page.index]
        width_pt = float(page.width_pt) or document.page_size(page.index)[0]
        height_pt = float(page.height_pt) or document.page_size(page.index)[1]
        # Render source at export DPI so erase/compose stays sharp, then rasterize
        # the composed vector page at the same DPI for a 1:1 print-ready bitmap.
        render_dpi = max(dpi, BASE_DPI)
        image = document.render(page.index, render_dpi)
        composed, _issues = compose_page(image, blocks, width_pt, height_pt, page.index)
        try:
            pixels = rasterize(composed, dpi)
        finally:
            composed.close()
        # Enforce exact pixel dimensions for the chosen paper size at DPI.
        target_w = max(1, round(width_pt * dpi / 72.0))
        target_h = max(1, round(height_pt * dpi / 72.0))
        pillow = Image.fromarray(pixels)
        if pillow.size != (target_w, target_h):
            pillow = pillow.resize((target_w, target_h), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        if kind == "jpeg":
            pillow = pillow.convert("RGB")
            pillow.save(buffer, format="JPEG", quality=quality, subsampling=0, optimize=True)
        else:
            pillow.save(buffer, format="PNG", optimize=True)
        pages.append((f"page-{page.index + 1:02d}.{suffix}", buffer.getvalue()))
    return pages
