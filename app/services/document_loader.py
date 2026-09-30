"""Load PDFs and images into page rasters. PDF points stay the coordinate system."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image

from app.config import BASE_DPI, MAX_RASTER_SIDE

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
PDF_SUFFIXES = {".pdf"}


@dataclass
class OpenDocument:
    path: Path
    kind: str
    page_count: int
    _pdf: pymupdf.Document | None = None
    _image: Image.Image | None = None

    def close(self) -> None:
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None

    def page_size(self, index: int) -> tuple[float, float]:
        if self.kind == "pdf":
            rect = self._pdf[index].rect
            return float(rect.width), float(rect.height)
        assert self._image is not None
        width, height = self._image.size
        return width * 72 / BASE_DPI, height * 72 / BASE_DPI

    def render(self, index: int, dpi: int) -> np.ndarray:
        if self.kind == "pdf":
            zoom = dpi / 72
            pixmap = self._pdf[index].get_pixmap(
                matrix=pymupdf.Matrix(zoom, zoom), alpha=False
            )
            image = _pixmap_to_rgb(pixmap)
        else:
            assert self._image is not None
            scale = dpi / BASE_DPI
            width = max(1, round(self._image.width * scale))
            height = max(1, round(self._image.height * scale))
            resized = self._image.resize((width, height), Image.Resampling.LANCZOS)
            image = np.asarray(resized.convert("RGB"))
        return _limit_side(image)

    def pdf_page(self, index: int):
        if self.kind != "pdf" or self._pdf is None:
            return None
        return self._pdf[index]

    def alphabetic_chars(self, index: int) -> int:
        page = self.pdf_page(index)
        if page is None:
            return 0
        text = page.get_text("text") or ""
        return sum(1 for ch in text if ch.isalpha())


def open_document(path: Path) -> OpenDocument:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        try:
            pdf = pymupdf.open(path)
        except Exception as exc:
            raise ValueError(f"Could not open this PDF. {exc}") from exc
        if pdf.needs_pass:
            pdf.close()
            raise ValueError("This PDF is password protected.")
        if pdf.page_count < 1:
            pdf.close()
            raise ValueError("This PDF has no pages.")
        return OpenDocument(path=path, kind="pdf", page_count=pdf.page_count, _pdf=pdf)
    if suffix in IMAGE_SUFFIXES:
        try:
            image = Image.open(path).convert("RGB")
        except Exception as exc:
            raise ValueError(f"Could not open this image. {exc}") from exc
        return OpenDocument(path=path, kind="image", page_count=1, _image=image)
    raise ValueError("Upload a PDF, JPG, or PNG file.")


def _pixmap_to_rgb(pixmap: pymupdf.Pixmap) -> np.ndarray:
    channels = pixmap.n
    array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
        pixmap.height, pixmap.width, channels
    )
    if channels == 4:
        array = array[:, :, :3]
    return np.ascontiguousarray(array)


def _limit_side(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= MAX_RASTER_SIDE:
        return image
    scale = MAX_RASTER_SIDE / longest
    resized = Image.fromarray(image).resize(
        (max(1, round(width * scale)), max(1, round(height * scale))),
        Image.Resampling.LANCZOS,
    )
    return np.asarray(resized)
