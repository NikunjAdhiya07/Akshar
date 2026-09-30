"""Run the document pipeline one page at a time and keep the UI responsive."""

from __future__ import annotations

import uuid

import numpy as np
from PIL import Image

from app.config import BASE_DPI, ON_VERCEL, VERCEL_MAX_PAGES
from app.jobs import job_dir, load_job, note, page_dir, save_job
from app.models import Issue, PageState, TextBlock
from app.services.document_loader import open_document
from app.services.layout import build_blocks
from app.services.ocr import DigitalPdfExtractor, select_ocr_provider
from app.services.reconstruct import render_preview
from app.services.translation.google import TranslationError, get_translator
from app.services.translation.service import translate_blocks, translate_text
from app.services.validation import validate_blocks

_digital = DigitalPdfExtractor()


def process_job(job_id: str, translator=None, on_progress=None) -> None:
    job = load_job(job_id)
    provider = translator or get_translator()
    source = job_dir(job_id) / job.filename
    job.status = "processing"
    note(job, "Opening document")
    _emit(on_progress, job)
    try:
        document = open_document(source)
    except Exception as exc:
        job.status = "error"
        job.error = str(exc)
        note(job, str(exc), cloud=True)
        _emit(on_progress, job)
        return
    total_pages = document.page_count
    limit = min(total_pages, VERCEL_MAX_PAGES) if ON_VERCEL else total_pages
    job.page_count = limit
    if ON_VERCEL and total_pages > limit:
        note(job, f"Serverless mode: converting first {limit} of {total_pages} pages")
    job.pages = [
        PageState(index=index, width_pt=document.page_size(index)[0], height_pt=document.page_size(index)[1])
        for index in range(limit)
    ]
    job.blocks = []
    job.issues = []
    save_job(job, cloud=False)
    _emit(on_progress, job)
    try:
        for index in range(limit):
            job.page_index = index
            _process_page(job, document, index, provider, replace_blocks=True)
            _emit(on_progress, job)
    finally:
        document.close()
    if any(page.render_status == "done" for page in job.pages):
        job.status = "ready"
        job.stage = "Ready for review"
    else:
        job.status = "error"
        job.error = job.error or "No page could be processed."
    job.revision += 1
    save_job(job, cloud=True)
    _emit(on_progress, job)


def _emit(on_progress, job) -> None:
    if on_progress is None:
        return
    try:
        on_progress(job.to_dict())
    except Exception:
        pass


def retry_page(job_id: str, page_index: int, translator=None) -> None:
    job = load_job(job_id)
    provider = translator or get_translator()
    document = open_document(job_dir(job_id) / job.filename)
    job.status = "processing"
    try:
        _process_page(job, document, page_index, provider, replace_blocks=True)
    finally:
        document.close()
    job.status = "ready" if any(page.render_status == "done" for page in job.pages) else "error"
    job.stage = "Ready for review"
    job.revision += 1
    save_job(job)


def rerender_page(job_id: str, page_index: int) -> None:
    job = load_job(job_id)
    document = open_document(job_dir(job_id) / job.filename)
    try:
        _render_only(job, document, page_index)
    finally:
        document.close()
    job.revision += 1
    job.stage = "Preview updated"
    save_job(job)


def update_translation(job_id: str, block_id: str, translation: str) -> TextBlock:
    job = load_job(job_id)
    block = _block(job, block_id)
    block.translation = translation.strip()
    block.edited = True
    block.preserved = False
    rerender_page(job_id, block.page)
    return _block(load_job(job_id), block_id)


def retranslate_block(job_id: str, block_id: str, translator=None) -> TextBlock:
    job = load_job(job_id)
    block = _block(job, block_id)
    provider = translator or get_translator()
    block.edited = False
    block.preserved = False
    block.translation = translate_text(block.text, provider, force=True)
    save_job(job)
    rerender_page(job_id, block.page)
    return _block(load_job(job_id), block_id)


def add_block(job_id: str, page_index: int, bbox: list[float], text: str, translator=None) -> TextBlock:
    job = load_job(job_id)
    if page_index < 0 or page_index >= len(job.pages):
        raise ValueError("That page does not exist.")
    provider = translator or get_translator()
    block = TextBlock(
        id=f"p{page_index}m{uuid.uuid4().hex[:6]}",
        page=page_index,
        bbox=[float(v) for v in bbox],
        draw_bbox=[float(v) for v in bbox],
        source_boxes=[[float(v) for v in bbox]],
        text=text.strip(),
        kind="paragraph",
        font_size=12,
        confidence=1,
    )
    try:
        block.translation = translate_text(block.text, provider, force=True)
    except TranslationError:
        block.translation = ""
    job.blocks.append(block)
    save_job(job)
    rerender_page(job_id, page_index)
    return _block(load_job(job_id), block.id)


def delete_block(job_id: str, block_id: str) -> None:
    job = load_job(job_id)
    block = _block(job, block_id)
    job.blocks = [item for item in job.blocks if item.id != block_id]
    save_job(job)
    rerender_page(job_id, block.page)


def approve_page(job_id: str, page_index: int, approved: bool) -> None:
    job = load_job(job_id)
    job.pages[page_index].approved = approved
    job.revision += 1
    save_job(job)


def _process_page(job, document, index: int, provider, replace_blocks: bool) -> None:
    page = job.pages[index]
    page.error = None
    page.ocr_status = "running"
    note(job, f"Page {index + 1} / {job.page_count} — reading layout")
    try:
        image = document.render(index, BASE_DPI)
        _save_rgb(page_dir(job.id, index) / "original.png", image)
        page.width_px = int(image.shape[1])
        page.height_px = int(image.shape[0])
        lines, engine = _detect(document, index, image, job.force_ocr)
        if not ON_VERCEL:
            _sample_colors(image, lines, page.width_pt, page.height_pt)
        page.engine = engine
        page.ocr_status = "done"
        drawings = []
        pdf_page = document.pdf_page(index)
        if pdf_page is not None and engine == "digital-pdf":
            try:
                drawings = pdf_page.get_drawings()
            except Exception:
                drawings = []
        blocks = build_blocks(lines, index, page.width_pt, page.height_pt, drawings)
        if not blocks and not lines:
            page.error = "No English text was found on this page. You can type it manually."
            job.issues = [issue for issue in job.issues if issue.page != index]
            job.issues.append(
                Issue("ocr_failed", page.error, "error", index, None)
            )
        page.translation_status = "running"
        note(job, f"Page {index + 1} / {job.page_count} — translating")
        try:
            translate_blocks(blocks, provider)
            page.translation_status = "done"
        except TranslationError as exc:
            page.translation_status = "error"
            page.error = str(exc)
            for block in blocks:
                if not block.translation:
                    block.translation = block.text
            job.issues.append(Issue("translation_failed", str(exc), "error", index, None))
        if replace_blocks:
            job.blocks = [block for block in job.blocks if block.page != index] + blocks
        page.render_status = "running"
        note(job, f"Page {index + 1} / {job.page_count} — rendering Gujarati")
        _paint(job, image, index)
        page.render_status = "done"
    except Exception as exc:
        page.error = str(exc)
        if page.ocr_status == "running":
            page.ocr_status = "error"
        if page.translation_status == "running":
            page.translation_status = "error"
        if page.render_status == "running":
            page.render_status = "error"
        job.issues.append(Issue("page_failed", str(exc), "error", index, None))
        note(job, f"Page {index + 1} failed: {exc}")


def _render_only(job, document, index: int) -> None:
    page = job.pages[index]
    image = document.render(index, BASE_DPI)
    _save_rgb(page_dir(job.id, index) / "original.png", image)
    page.render_status = "running"
    save_job(job)
    _paint(job, image, index)
    page.render_status = "done"


def _paint(job, image: np.ndarray, index: int) -> None:
    page = job.pages[index]
    blocks = [block for block in job.blocks if block.page == index]
    for block in blocks:
        block.fit_scale = 1
    preview, render_issues = render_preview(image, blocks, page.width_pt, page.height_pt, index, BASE_DPI)
    _save_rgb(page_dir(job.id, index) / "preview.png", preview)
    job.issues = [issue for issue in job.issues if issue.page != index]
    if ON_VERCEL:
        job.issues.extend(render_issues)
    else:
        checked = validate_blocks(blocks)
        job.issues.extend(checked)
        job.issues.extend(render_issues)


def _detect(document, index: int, image: np.ndarray, force_ocr: bool):
    pdf_page = document.pdf_page(index)
    if pdf_page is not None and not force_ocr and document.alphabetic_chars(index) >= 20:
        return _digital.extract(pdf_page), "digital-pdf"
    provider = select_ocr_provider()
    return provider.recognize(image, BASE_DPI), provider.name


def _sample_colors(image: np.ndarray, lines, width_pt: float, height_pt: float) -> None:
    height, width = image.shape[:2]
    sx = width / width_pt if width_pt else 1
    sy = height / height_pt if height_pt else 1
    for line in lines:
        if line.color != [20, 20, 20]:
            continue
        x0 = max(0, int(line.bbox[0] * sx))
        y0 = max(0, int(line.bbox[1] * sy))
        x1 = min(width, int(line.bbox[2] * sx))
        y1 = min(height, int(line.bbox[3] * sy))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = image[y0:y1, x0:x1]
        if roi.size == 0:
            continue
        background = _outside_color(image, x0, y0, x1, y1)
        flat = roi.reshape(-1, 3).astype(np.int16)
        distance = np.linalg.norm(flat - background.reshape(1, 3), axis=1)
        ink = flat[distance > 24]
        if len(ink) < 8:
            ink = flat
        chosen = np.median(ink, axis=0)
        line.color = _readable_color(chosen, background)


def _outside_color(image: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    height, width = image.shape[:2]
    radius = 8
    ox0, oy0 = max(0, x0 - radius), max(0, y0 - radius)
    ox1, oy1 = min(width, x1 + radius), min(height, y1 + radius)
    outer = image[oy0:oy1, ox0:ox1]
    if outer.size == 0:
        return np.array([255, 255, 255], dtype=np.int16)
    mask = np.ones(outer.shape[:2], dtype=bool)
    iy0, ix0 = max(0, y0 - oy0), max(0, x0 - ox0)
    iy1 = min(outer.shape[0], iy0 + (y1 - y0))
    ix1 = min(outer.shape[1], ix0 + (x1 - x0))
    mask[iy0:iy1, ix0:ix1] = False
    samples = outer[mask]
    if len(samples) < 8:
        samples = outer.reshape(-1, 3)
    return np.median(samples, axis=0).astype(np.int16)


def _readable_color(ink: np.ndarray, background: np.ndarray) -> list[int]:
    color = np.asarray(ink, dtype=np.float32)
    paper = np.asarray(background, dtype=np.float32)

    def lum(value: np.ndarray) -> float:
        return float(value @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))

    if lum(paper) > 165 and lum(color) > 105:
        level = max(lum(color), 1)
        color = color * (70 / level)
    elif abs(lum(color) - lum(paper)) < 58:
        if lum(paper) > 150:
            level = max(lum(color), 1)
            color = color * (70 / level)
        else:
            color = np.clip(color + (230 - lum(color)), 0, 255)
    return [int(channel) for channel in np.clip(color, 0, 255)]


def _save_rgb(path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(path, format="PNG")


def _block(job, block_id: str) -> TextBlock:
    for block in job.blocks:
        if block.id == block_id:
            return block
    raise KeyError("Text block not found")
