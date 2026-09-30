"""Checks run before a page is treated as finished."""

from __future__ import annotations

import re

from app.models import Issue, TextBlock
from app.services.translation import protector

GUJARATI = re.compile(r"[\u0A80-\u0AFF]")
ENGLISH_WORD = re.compile(r"\b[A-Za-z]{4,}\b")
BROKEN = re.compile(r"[\ufffd\u0000-\u0008]")
DIGIT_MAP = str.maketrans("૦૧૨૩૪૫૬૭૮૯", "0123456789")
KEPT_WORDS = {
    "email",
    "date",
    "page",
    "northwind",
    "http",
    "https",
    "www",
}


def validate_blocks(blocks: list[TextBlock]) -> list[Issue]:
    issues: list[Issue] = []
    seen: dict[tuple[int, str], TextBlock] = {}
    for block in blocks:
        issues.extend(_check_block(block))
        key = (block.page, _norm(block.translation))
        previous = seen.get(key)
        if (
            previous
            and key[1]
            and _iou(previous.bbox, block.bbox) > 0.72
            and previous.id != block.id
        ):
            issues.append(
                Issue(
                    "duplicate",
                    "The same Gujarati text appears twice in nearly the same place.",
                    "warning",
                    block.page,
                    block.id,
                )
            )
        if key[1]:
            seen[key] = block
    return issues


def _check_block(block: TextBlock) -> list[Issue]:
    issues: list[Issue] = []
    source = block.text.strip()
    translated = (block.translation or "").strip()
    if not source:
        return issues
    if not translated:
        issues.append(
            Issue(
                "missing_text",
                "A detected text block has no translation.",
                "error",
                block.page,
                block.id,
            )
        )
        return issues
    if BROKEN.search(translated):
        issues.append(
            Issue(
                "broken_unicode",
                "The translation contains a broken character.",
                "error",
                block.page,
                block.id,
            )
        )
    if block.confidence < 0.55:
        issues.append(
            Issue(
                "low_confidence",
                "OCR confidence is low. Check this text.",
                "warning",
                block.page,
                block.id,
            )
        )
    if not block.preserved and protector.needs_translation(source):
        if not GUJARATI.search(translated):
            issues.append(
                Issue(
                    "untranslated",
                    "This block still has no Gujarati text.",
                    "warning",
                    block.page,
                    block.id,
                )
            )
        else:
            leftover = _english_left(source, translated)
            if leftover:
                issues.append(
                    Issue(
                        "english_remaining",
                        "English still remains: " + ", ".join(leftover[:4]),
                        "warning",
                        block.page,
                        block.id,
                    )
                )
    if block.fit_scale < 0.78:
        issues.append(
            Issue(
                "overflow",
                "Gujarati text was reduced to fit the original space. Check for clipping.",
                "warning",
                block.page,
                block.id,
            )
        )
    source_numbers = protector.protected_numbers(source)
    normalized = translated.translate(DIGIT_MAP)
    missing_numbers = [number for number in source_numbers if number not in normalized]
    if missing_numbers and not block.preserved:
        issues.append(
            Issue(
                "missing_number",
                "A number from the original is missing: " + ", ".join(missing_numbers[:4]),
                "warning",
                block.page,
                block.id,
            )
        )
    missing_dates = [date for date in protector.protected_dates(source) if date not in translated]
    if missing_dates and not block.preserved:
        issues.append(
            Issue(
                "missing_date",
                "A date from the original is missing: " + ", ".join(missing_dates[:3]),
                "warning",
                block.page,
                block.id,
            )
        )
    return issues


def _english_left(source: str, translated: str) -> list[str]:
    protected = set()
    for segment in protector.segment_text(source):
        if segment.kind == "keep":
            protected.update(word.casefold() for word in ENGLISH_WORD.findall(segment.value))
    found = []
    for word in ENGLISH_WORD.findall(translated):
        token = word.casefold()
        if token in protected or token in KEPT_WORDS:
            continue
        if word.isupper() and len(word) <= 5:
            continue
        found.append(word)
    return found


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _iou(a: list[float], b: list[float]) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union else 0.0
