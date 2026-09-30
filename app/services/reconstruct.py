"""Remove original English ink and draw Unicode Gujarati back into the same layout.

MuPDF shapes Gujarati with HarfBuzz and embeds Noto Sans Gujarati, so the
exported PDF renders correctly on a machine with no Gujarati font installed.
"""

from __future__ import annotations

import io
from html import escape

import numpy as np
import pymupdf
from PIL import Image

from app.config import BASE_DPI, FONTS_DIR
from app.models import Issue, TextBlock


def erase_text(image: np.ndarray, boxes: list[list[float]], width_pt: float, height_pt: float) -> np.ndarray:
    """Cover English ink and leave rules, photos, stamps, and logos in place.

    Flat paper is filled with the surrounding color. Photographic and metallic
    lettering is inpainted, including the glow around gold type, so the
    original English cannot show through the Gujarati.
    """
    if image.size == 0 or not boxes:
        return image.copy()
    import cv2

    output = image.copy()
    height, width = output.shape[:2]
    sx = width / width_pt if width_pt else 1
    sy = height / height_pt if height_pt else 1
    paint = np.zeros((height, width), np.uint8)
    for box in boxes:
        rect = _pixel_box(box, sx, sy, width, height)
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        background = _ring_color(output, x0, y0, x1, y1)
        flat = _ring_std(output, x0, y0, x1, y1) < 14 and (y1 - y0) < 52
        roi = output[y0:y1, x0:x1]
        mask = _ink_mask(roi, background, flat)
        if not mask.any():
            continue
        if flat:
            roi[mask] = background
        else:
            paint[y0:y1, x0:x1][mask] = 255
    if int(paint.max()) > 0:
        painted = cv2.inpaint(
            cv2.cvtColor(output, cv2.COLOR_RGB2BGR),
            paint,
            5,
            cv2.INPAINT_TELEA,
        )
        output = cv2.cvtColor(painted, cv2.COLOR_BGR2RGB)
    return output


def compose_page(
    image: np.ndarray,
    blocks: list[TextBlock],
    width_pt: float,
    height_pt: float,
    page_index: int,
) -> tuple[pymupdf.Document, list[Issue]]:
    cleaned = erase_text(
        image,
        [box for block in blocks for box in block.source_boxes],
        width_pt,
        height_pt,
    )
    document = pymupdf.open()
    page = document.new_page(width=width_pt, height=height_pt)
    stream = _png_bytes(cleaned)
    page.insert_image(page.rect, stream=stream)
    issues: list[Issue] = []
    archive = pymupdf.Archive(str(FONTS_DIR))
    for block in blocks:
        text = (block.translation or "").strip()
        if not text:
            continue
        rect = pymupdf.Rect(*block.draw_bbox) & page.rect
        if rect.is_empty or rect.width < 2 or rect.height < 2:
            issues.append(
                Issue(
                    "placement",
                    "Translated text has no room on the page.",
                    "warning",
                    page_index,
                    block.id,
                )
            )
            block.fit_scale = 0.5
            continue
        try:
            spare, scale = page.insert_htmlbox(
                rect,
                _html(text),
                css=_css(block, rect.height),
                archive=archive,
                scale_low=0.45,
                rotate=_rotation(block.angle),
                overlay=True,
            )
        except Exception:
            issues.append(
                Issue(
                    "render_failed",
                    "This block could not be drawn. Edit it and render the page again.",
                    "error",
                    page_index,
                    block.id,
                )
            )
            block.fit_scale = 0.5
            continue
        block.fit_scale = float(scale or 1)
        if spare < -0.5:
            block.fit_scale = min(block.fit_scale, 0.5)
    return document, issues


def rasterize(document: pymupdf.Document, dpi: int) -> np.ndarray:
    zoom = dpi / 72
    pixmap = document[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    channels = pixmap.n
    array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, channels)
    if channels == 4:
        array = array[:, :, :3]
    return np.ascontiguousarray(array.copy())


def render_preview(image: np.ndarray, blocks: list[TextBlock], width_pt: float, height_pt: float, page_index: int, dpi: int = BASE_DPI):
    document, issues = compose_page(image, blocks, width_pt, height_pt, page_index)
    preview = rasterize(document, dpi)
    document.close()
    return preview, issues


def _pixel_box(box: list[float], sx: float, sy: float, width: int, height: int) -> tuple[int, int, int, int] | None:
    bw = max(1.0, box[2] - box[0])
    bh = max(1.0, box[3] - box[1])
    pad_x = max(0.6, bw * 0.04)
    pad_y = max(0.8, bh * 0.16)
    x0 = max(0, int(np.floor((box[0] - pad_x) * sx)) - 1)
    y0 = max(0, int(np.floor((box[1] - pad_y) * sy)) - 1)
    x1 = min(width, int(np.ceil((box[2] + pad_x) * sx)) + 1)
    y1 = min(height, int(np.ceil((box[3] + pad_y * 0.55) * sy)) + 1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    if (x1 - x0) * (y1 - y0) > 0.45 * width * height:
        return None
    return x0, y0, x1, y1


def _ink_mask(roi: np.ndarray, background: np.ndarray, flat: bool) -> np.ndarray:
    import cv2

    if flat:
        mask = np.ones(roi.shape[:2], dtype=bool)
        mask = _drop_rules(mask)
        return _clear_straight_lines(mask, roi)
    distance = np.linalg.norm(roi.astype(np.int16) - background.reshape(1, 1, 3), axis=2)
    sigma = max(1.2, min(8.0, roi.shape[0] * 0.04))
    blur = cv2.GaussianBlur(roi, (0, 0), sigmaX=sigma)
    edges = np.max(np.abs(roi.astype(np.int16) - blur.astype(np.int16)), axis=2) > 16
    mask = edges
    if int(edges.sum()) > 12:
        ink = np.median(roi[edges], axis=0)
        near_ink = np.linalg.norm(roi.astype(np.int16) - ink.reshape(1, 1, 3), axis=2) < 40
        mask = edges | (near_ink & (distance > 18))
    if mask.mean() > 0.55:
        mask = edges
    mask = _drop_rules(mask)
    mask = _clear_straight_lines(mask, roi)
    radius = int(np.clip(round(roi.shape[0] * 0.08), 2, 10))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    painted = cv2.dilate(mask.astype(np.uint8) * 255, kernel, iterations=1)
    painted = cv2.morphologyEx(painted, cv2.MORPH_CLOSE, kernel)
    return _clear_straight_lines(painted > 0, roi)


def _clear_straight_lines(mask: np.ndarray, roi: np.ndarray) -> np.ndarray:
    """Keep table rules and banner borders that run through a text box."""
    cleaned = mask.copy()
    height, width = cleaned.shape[:2]
    if height < 5 or width < 8:
        return cleaned
    luminance = roi.astype(np.float32) @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    core = luminance[:, int(width * 0.08) : int(width * 0.92)]
    if core.size == 0:
        return cleaned
    spread = core.std(axis=1)
    level = np.median(core, axis=1)
    runs: list[tuple[int, int]] = []
    start = 0
    for index in range(1, height + 1):
        split = index == height or spread[index] > 16 or abs(level[index] - level[start]) > 18
        if split:
            runs.append((start, index))
            start = index
    for start, end in runs:
        if end - start > 4 or float(spread[start:end].max()) > 16:
            continue
        outside = []
        if start > 0:
            outside.append(level[start - 1])
        if end < height:
            outside.append(level[end])
        if outside and abs(float(level[start]) - float(np.median(outside))) > 35:
            cleaned[start:end, :] = False
    return cleaned


def _html(text: str) -> str:
    body = escape(text).replace("\n", "<br>")
    return f"<div>{body}</div>"


def _css(block: TextBlock, box_height: float) -> str:
    family = "AksharBold" if block.bold else "Akshar"
    red, green, blue = (block.color + [20, 20, 20])[:3]
    align = block.align if block.align in {"left", "center", "right"} else "left"
    decoration = "underline" if block.underline else "none"
    lines = max(1, len(block.source_boxes))
    size = max(6.0, float(block.font_size))
    if lines == 1:
        size = min(size, max(6.0, box_height * 0.82))
        line_height = 1.02
    else:
        line_height = max(1.05, min(1.28, (box_height * 0.94) / (size * lines)))
    if block.kind == "heading":
        size = max(size, min(box_height * 0.72, max(size, block.font_size)))
    return f"""
@font-face {{ font-family: Akshar; src: url("NotoSansGujarati-Regular.ttf"); }}
@font-face {{ font-family: AksharBold; src: url("NotoSansGujarati-Bold.ttf"); }}
div {{
  font-family: {family};
  font-size: {size:.2f}pt;
  color: rgb({int(red)},{int(green)},{int(blue)});
  text-align: {align};
  text-decoration: {decoration};
  line-height: {line_height:.3f};
  margin: 0;
  padding: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}}
"""


def _rotation(angle: float) -> int:
    value = angle % 360
    if value > 180:
        value -= 360
    if abs(abs(value) - 90) < 12:
        return 90 if value > 0 else 270
    return 0


def _png_bytes(image: np.ndarray) -> bytes:
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    return buffer.getvalue()


def _ring_color(image: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    samples = _ring(image, x0, y0, x1, y1)
    if samples.size == 0:
        return np.array([255, 255, 255], dtype=np.uint8)
    return np.median(samples, axis=0).astype(np.uint8)


def _ring_std(image: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> float:
    samples = _ring(image, x0, y0, x1, y1)
    if samples.size == 0:
        return 0.0
    median = np.median(samples, axis=0)
    distance = np.linalg.norm(samples.astype(np.float32) - median, axis=1)
    core = samples[distance < 28]
    if len(core) < 8:
        core = samples
    return float(core.astype(np.float32).std())


def _ring(image: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> np.ndarray:
    height, width = image.shape[:2]
    radius = 4
    ox0, oy0 = max(0, x0 - radius), max(0, y0 - radius)
    ox1, oy1 = min(width, x1 + radius), min(height, y1 + radius)
    outer = image[oy0:oy1, ox0:ox1]
    if outer.size == 0:
        return np.empty((0, 3), dtype=np.uint8)
    mask = np.ones(outer.shape[:2], dtype=bool)
    iy0, ix0 = max(0, y0 - oy0), max(0, x0 - ox0)
    iy1 = min(outer.shape[0], iy0 + (y1 - y0))
    ix1 = min(outer.shape[1], ix0 + (x1 - x0))
    mask[iy0:iy1, ix0:ix1] = False
    samples = outer[mask]
    if len(samples) < 8:
        return outer.reshape(-1, 3)
    return samples


def _drop_rules(mask: np.ndarray) -> np.ndarray:
    cleaned = mask.copy()
    if cleaned.shape[0] < 3 or cleaned.shape[1] < 3:
        return cleaned
    row_frac = cleaned.mean(axis=1)
    col_frac = cleaned.mean(axis=0)
    rule_rows = row_frac > 0.72
    rule_cols = col_frac > 0.72
    if 0 < int(rule_rows.sum()) <= max(3, int(0.2 * len(rule_rows))):
        cleaned[rule_rows, :] = False
    if 0 < int(rule_cols.sum()) <= max(3, int(0.2 * len(rule_cols))):
        cleaned[:, rule_cols] = False
    return cleaned
