"""Build the acceptance documents used by the studio and the tests."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from PIL import Image, ImageFilter

from app.config import SAMPLES_DIR, ensure_dirs


def ensure_samples() -> dict[str, Path]:
    ensure_dirs()
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    digital = SAMPLES_DIR / "certificate.pdf"
    scanned = SAMPLES_DIR / "certificate-scan.pdf"
    image = SAMPLES_DIR / "certificate-page.png"
    if not (digital.is_file() and scanned.is_file() and image.is_file()):
        build_certificate(digital)
        build_scanned(digital, scanned, image)
    return {"certificate": digital, "scan": scanned, "image": image}


def build_certificate(path: Path) -> None:
    document = pymupdf.open()
    _page_one(document.new_page(width=595, height=842))
    _page_two(document.new_page(width=595, height=842))
    path.parent.mkdir(parents=True, exist_ok=True)
    document.save(path)
    document.close()


def build_scanned(source: Path, pdf_path: Path, png_path: Path) -> None:
    original = pymupdf.open(source)
    scanned = pymupdf.open()
    for index, page in enumerate(original):
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(150 / 72, 150 / 72), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        image = image.filter(ImageFilter.GaussianBlur(radius=0.6))
        holder = Path(pdf_path).with_name(f"_scan_{index}.jpg")
        image.save(holder, quality=70)
        if index == 0:
            image.save(png_path)
        blank = scanned.new_page(width=page.rect.width, height=page.rect.height)
        blank.insert_image(blank.rect, filename=str(holder))
        holder.unlink(missing_ok=True)
    scanned.save(pdf_path)
    scanned.close()
    original.close()


def _page_one(page) -> None:
    ink = (0.12, 0.14, 0.13)
    muted = (0.28, 0.32, 0.3)
    rule = (0.08, 0.32, 0.26)
    page.draw_rect(pymupdf.Rect(36, 32, 88, 84), fill=rule, color=rule)
    page.insert_text((48, 66), "NW", fontsize=16, fontname="hebo", color=(1, 1, 1))
    page.insert_text((100, 52), "Northwind Quality Laboratory", fontsize=16, fontname="hebo", color=ink)
    page.insert_text(
        (100, 72),
        "Ahmedabad    +91 79 4000 2210    qa@northwind.example",
        fontsize=9,
        fontname="helv",
        color=muted,
    )
    page.draw_line(pymupdf.Point(36, 96), pymupdf.Point(559, 96), color=rule, width=1.4)
    page.insert_text((36, 132), "Certificate of Analysis", fontsize=20, fontname="hebo", color=ink)
    page.insert_text((36, 152), "Finished Product Release", fontsize=12, fontname="helv", color=rule)
    paragraph = (
        "This certificate confirms that the batch described below was tested by the "
        "Quality Control Department and meets the approved specification. Store the "
        "material below 25°C in a dry place and retain this record with the batch file."
    )
    page.insert_textbox(pymupdf.Rect(36, 168, 559, 230), paragraph, fontsize=11, fontname="helv", color=ink, align=0)
    meta = [
        (36, "SOP No: QA-001"),
        (210, "Date: 12/03/2024"),
        (390, "Batch No: BN-2024-118"),
        (36, "GSTIN: 24ABCDE1234F1Z5"),
        (280, "PAN: ABCDE1234F"),
    ]
    y = 248
    for index, (x, text) in enumerate(meta):
        if index == 3:
            y = 268
        page.insert_text((x, y), text, fontsize=10, fontname="helv", color=ink)
    _table(
        page,
        36,
        292,
        [170, 210, 143],
        28,
        [
            ["Parameter", "Specification", "Result"],
            ["Assay", "98.0% to 102.0%", "99.4%"],
            ["pH", "6.5 to 7.5", "7.1"],
            ["Water", "Not more than 0.50%", "0.22%"],
            ["Appearance", "White crystalline powder", "Complies"],
        ],
    )
    page.insert_text((36, 470), "Checked by", fontsize=10, fontname="helv", color=ink)
    page.draw_line(pymupdf.Point(110, 470), pymupdf.Point(280, 470), color=ink, width=0.6)
    page.insert_text((310, 470), "Approved by", fontsize=10, fontname="helv", color=ink)
    page.draw_line(pymupdf.Point(390, 470), pymupdf.Point(559, 470), color=ink, width=0.6)
    page.insert_text((36, 500), "Email: release@northwind.example", fontsize=10, fontname="helv", color=ink)
    page.draw_circle(pymupdf.Point(500, 760), 38, color=(0.55, 0.12, 0.12), width=1.6)
    page.insert_text((472, 764), "RELEASED", fontsize=8, fontname="hebo", color=(0.55, 0.12, 0.12))
    page.draw_line(pymupdf.Point(36, 800), pymupdf.Point(559, 800), color=rule, width=0.8)
    page.insert_text((36, 818), "Controlled document", fontsize=8, fontname="helv", color=muted)
    page.insert_text((500, 818), "Page 1 of 2", fontsize=8, fontname="helv", color=muted)


def _page_two(page) -> None:
    ink = (0.12, 0.14, 0.13)
    muted = (0.28, 0.32, 0.3)
    rule = (0.08, 0.32, 0.26)
    page.insert_text((36, 48), "Northwind Quality Laboratory", fontsize=12, fontname="hebo", color=ink)
    page.draw_line(pymupdf.Point(36, 58), pymupdf.Point(559, 58), color=rule, width=1)
    page.insert_text((36, 96), "Stability Summary", fontsize=18, fontname="hebo", color=ink)
    body = (
        "Accelerated stability was studied for 6 months at 40°C and 75% relative humidity. "
        "Chromatographic purity remained within the approved limit. No unknown impurity "
        "exceeded 0.10%. The container closure system showed no visible change."
    )
    page.insert_textbox(pymupdf.Rect(36, 114, 400, 210), body, fontsize=11, fontname="helv", color=ink)
    page.draw_rect(pymupdf.Rect(420, 114, 559, 250), color=(0.75, 0.72, 0.66), width=0.8)
    page.insert_text((436, 150), "Diagram", fontsize=11, fontname="hebo", color=ink)
    page.draw_rect(pymupdf.Rect(440, 170, 470, 230), fill=(0.08, 0.32, 0.26), color=(0.08, 0.32, 0.26))
    page.draw_rect(pymupdf.Rect(478, 190, 508, 230), fill=(0.55, 0.42, 0.18), color=(0.55, 0.42, 0.18))
    page.draw_rect(pymupdf.Rect(516, 150, 546, 230), fill=(0.45, 0.18, 0.16), color=(0.45, 0.18, 0.16))
    page.insert_text((36, 250), "Serial No: SN-77821", fontsize=11, fontname="helv", color=ink)
    page.insert_text((250, 250), "Invoice No: INV-2024-015", fontsize=11, fontname="helv", color=ink)
    note = (
        "Retain the sample for one year after batch release. Report any confirmed "
        "out of specification result to the Quality Assurance group before distribution."
    )
    page.draw_rect(pymupdf.Rect(36, 280, 559, 360), fill=(0.95, 0.93, 0.88), color=(0.82, 0.78, 0.7))
    page.insert_text((48, 302), "Storage note", fontsize=12, fontname="hebo", color=ink)
    page.insert_textbox(pymupdf.Rect(48, 312, 540, 352), note, fontsize=10, fontname="heit", color=ink)
    page.insert_text((36, 400), "Reviewer comments", fontsize=13, fontname="hebo", color=ink)
    page.insert_textbox(
        pymupdf.Rect(36, 412, 559, 470),
        "Results are consistent with the previous three commercial batches. No retest is required.",
        fontsize=11,
        fontname="helv",
        color=ink,
    )
    page.draw_line(pymupdf.Point(36, 800), pymupdf.Point(559, 800), color=rule, width=0.8)
    page.insert_text((36, 818), "Controlled document", fontsize=8, fontname="helv", color=muted)
    page.insert_text((500, 818), "Page 2 of 2", fontsize=8, fontname="helv", color=muted)


def _table(page, x, y, widths, row_h, rows) -> None:
    total = sum(widths)
    page.draw_rect(pymupdf.Rect(x, y, x + total, y + row_h * len(rows)), color=(0.2, 0.24, 0.22), width=0.8)
    cursor = x
    for width in widths[:-1]:
        cursor += width
        page.draw_line(pymupdf.Point(cursor, y), pymupdf.Point(cursor, y + row_h * len(rows)), color=(0.2, 0.24, 0.22), width=0.6)
    for index in range(1, len(rows)):
        yy = y + row_h * index
        page.draw_line(pymupdf.Point(x, yy), pymupdf.Point(x + total, yy), color=(0.2, 0.24, 0.22), width=0.6)
    page.draw_rect(pymupdf.Rect(x, y, x + total, y + row_h), fill=(0.9, 0.93, 0.91), color=(0.2, 0.24, 0.22), width=0.4)
    for r, row in enumerate(rows):
        cx = x
        for c, value in enumerate(row):
            font = "hebo" if r == 0 else "helv"
            page.insert_textbox(
                pymupdf.Rect(cx + 6, y + row_h * r + 7, cx + widths[c] - 6, y + row_h * (r + 1) - 4),
                value,
                fontsize=10,
                fontname=font,
                color=(0.1, 0.12, 0.11),
                align=1,
            )
            cx += widths[c]
