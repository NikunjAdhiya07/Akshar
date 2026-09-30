"""OCR providers. Digital PDFs use the text layer. Scans use RapidOCR.

A Tesseract provider is included so the engine can be swapped without
touching layout, translation, or export.
"""

from __future__ import annotations

import math
import shutil
import threading
from typing import Protocol

import numpy as np

from app.models import DetectedLine

_engine = None
_engine_lock = threading.Lock()


class OcrProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def recognize(self, image: np.ndarray, dpi: int) -> list[DetectedLine]: ...


def _color_from_int(value: int) -> list[int]:
    return [(value >> 16) & 255, (value >> 8) & 255, value & 255]


def _snap_angle(dx: float, dy: float) -> float:
    if abs(dx) < 1e-6 and abs(dy) < 1e-6:
        return 0.0
    return math.degrees(math.atan2(dy, dx))


class DigitalPdfExtractor:
    """Read positioned text from a born-digital PDF page."""

    name = "digital-pdf"

    def extract(self, page) -> list[DetectedLine]:
        data = page.get_text("dict") or {}
        lines: list[DetectedLine] = []
        for block in data.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                spans = [span for span in line.get("spans", []) if span.get("text")]
                text = "".join(span["text"] for span in spans).strip()
                if not text or not any(ch.isalnum() for ch in text):
                    continue
                bbox = [float(v) for v in line["bbox"]]
                if bbox[2] - bbox[0] < 1 or bbox[3] - bbox[1] < 1:
                    continue
                size = max(float(span.get("size", 11)) for span in spans)
                flags = 0
                font_name = ""
                color = 0
                for span in spans:
                    flags |= int(span.get("flags", 0))
                    font_name = span.get("font", font_name)
                    color = int(span.get("color", color))
                direction = line.get("dir") or (1, 0)
                lines.append(
                    DetectedLine(
                        text=text,
                        bbox=bbox,
                        font_size=size,
                        bold=bool(flags & 16) or "bold" in font_name.lower(),
                        italic=bool(flags & 2) or "italic" in font_name.lower(),
                        color=_color_from_int(color),
                        confidence=0.99,
                        angle=_snap_angle(float(direction[0]), float(direction[1])),
                    )
                )
        return lines


class RapidOcrProvider:
    name = "rapidocr"

    def available(self) -> bool:
        try:
            import rapidocr  # noqa: F401
            return True
        except Exception:
            return False

    def recognize(self, image: np.ndarray, dpi: int) -> list[DetectedLine]:
        from app.config import ON_VERCEL

        engine = _rapid_engine()
        import cv2

        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
        result = engine(bgr, text_score=0.35)
        if result is None or not getattr(result, "txts", None):
            return []
        scale = 72 / dpi
        lines: list[DetectedLine] = []
        boxes = result.boxes if result.boxes is not None else []
        for quad, text, score in zip(boxes, result.txts, result.scores):
            cleaned = str(text).strip()
            if not cleaned or not any(ch.isalnum() for ch in cleaned):
                continue
            points = np.asarray(quad, dtype=float)
            xs = points[:, 0]
            ys = points[:, 1]
            x0, y0, x1, y1 = xs.min(), ys.min(), xs.max(), ys.max()
            if x1 - x0 < 2 or y1 - y0 < 2:
                continue
            height_px = max(1.0, y1 - y0)
            # Second-pass OCR crops are too slow for serverless cold paths.
            if not ON_VERCEL:
                cleaned, score = _refine_weak_line(
                    engine,
                    image,
                    cleaned,
                    float(score),
                    int(xs.min()),
                    int(ys.min()),
                    int(xs.max()),
                    int(ys.max()),
                )
            lines.append(
                DetectedLine(
                    text=cleaned,
                    bbox=[x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                    font_size=max(6.0, height_px * scale * 0.78),
                    confidence=float(score),
                    angle=_snap_angle(float(points[1][0] - points[0][0]), float(points[1][1] - points[0][1])),
                )
            )
        return lines


class TesseractProvider:
    """Optional provider. Used only when the tesseract binary is installed."""

    name = "tesseract"

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def recognize(self, image: np.ndarray, dpi: int) -> list[DetectedLine]:
        import pytesseract
        from pytesseract import Output

        data = pytesseract.image_to_data(image, lang="eng", output_type=Output.DICT)
        scale = 72 / dpi
        grouped: dict[tuple[int, int, int], list[int]] = {}
        for index, text in enumerate(data["text"]):
            if int(data["conf"][index]) < 0 or not str(text).strip():
                continue
            key = (data["block_num"][index], data["par_num"][index], data["line_num"][index])
            grouped.setdefault(key, []).append(index)
        lines: list[DetectedLine] = []
        for indexes in grouped.values():
            words = [str(data["text"][i]).strip() for i in indexes]
            text = " ".join(word for word in words if word)
            if not any(ch.isalnum() for ch in text):
                continue
            x0 = min(data["left"][i] for i in indexes)
            y0 = min(data["top"][i] for i in indexes)
            x1 = max(data["left"][i] + data["width"][i] for i in indexes)
            y1 = max(data["top"][i] + data["height"][i] for i in indexes)
            confs = [float(data["conf"][i]) for i in indexes if int(data["conf"][i]) >= 0]
            height_px = max(1, y1 - y0)
            lines.append(
                DetectedLine(
                    text=text,
                    bbox=[x0 * scale, y0 * scale, x1 * scale, y1 * scale],
                    font_size=max(6.0, height_px * scale * 0.78),
                    confidence=(sum(confs) / len(confs) / 100) if confs else 0.5,
                )
            )
        return lines


def select_ocr_provider() -> OcrProvider:
    tesseract = TesseractProvider()
    if tesseract.available():
        try:
            import pytesseract  # noqa: F401
            return tesseract
        except Exception:
            pass
    rapid = RapidOcrProvider()
    if not rapid.available():
        raise RuntimeError("No OCR engine is available. Install rapidocr.")
    return rapid


def _rapid_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR

            _engine = RapidOCR(params={"Global.log_level": "ERROR"})
        return _engine


def _refine_weak_line(engine, image: np.ndarray, text: str, score: float, x0: int, y0: int, x1: int, y1: int) -> tuple[str, float]:
    """Re-read decorative or low-confidence lines from a black-on-white crop."""
    letters = sum(ch.isalpha() for ch in text)
    stuck = " " not in text and letters >= 9 and text.replace("'", "").isalpha()
    if score >= 0.9 and not stuck:
        return text, score
    height, width = image.shape[:2]
    pad_y = max(2, int(0.45 * (y1 - y0)))
    pad_x = max(2, int(0.06 * (x1 - x0)))
    xa, ya = max(0, x0 - pad_x), max(0, y0 - pad_y)
    xb, yb = min(width, x1 + pad_x), min(height, y1 + pad_y)
    crop = image[ya:yb, xa:xb]
    if crop.size == 0 or crop.shape[0] < 4 or crop.shape[1] < 8:
        return text, score
    import cv2

    up = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
    best_text = text
    best_score = score
    best_letters = letters
    for plate in _contrast_plates(up):
        result = engine(cv2.cvtColor(plate, cv2.COLOR_RGB2BGR), text_score=0.3)
        if result is None or not getattr(result, "txts", None):
            continue
        pieces = []
        scores = []
        order = sorted(
            zip(result.boxes, result.txts, result.scores),
            key=lambda item: float(np.min(np.asarray(item[0])[:, 0])),
        )
        for _box, raw, raw_score in order:
            piece = str(raw).strip()
            if float(raw_score) < 0.45 or sum(ch.isalpha() for ch in piece) < 2:
                continue
            pieces.append(piece)
            scores.append(float(raw_score))
        if not pieces:
            continue
        candidate = " ".join(pieces)
        candidate_score = float(np.mean(scores))
        candidate_letters = sum(ch.isalpha() for ch in candidate)
        garbage = score < 0.8
        if garbage:
            improved = candidate_letters > best_letters + 2
        else:
            improved = (
                " " in candidate
                and " " not in best_text
                and best_letters - 1 <= candidate_letters <= best_letters + 2
            )
        if candidate_score + 0.04 >= best_score and improved:
            best_text = candidate
            best_score = candidate_score
            best_letters = candidate_letters
    return best_text, best_score


def _contrast_plates(image: np.ndarray) -> list[np.ndarray]:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    _level, binary = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    plates = []
    for candidate in (binary, 255 - binary):
        dark = candidate < 128
        fraction = float(dark.mean())
        if not 0.02 < fraction < 0.5:
            continue
        plate = np.full_like(image, 255)
        plate[dark] = 0
        plates.append(plate)
    return plates
