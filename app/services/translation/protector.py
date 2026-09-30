"""Keep identifiers, numbers, and codes intact while translating the words around them."""

from __future__ import annotations

import re
from dataclasses import dataclass

EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
URL = re.compile(r"\b(?:https?://|www\.)[^\s<>()]+", re.I)
HANDLE = re.compile(r"(?<!\w)@[A-Za-z0-9_]{2,}\b")
GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b", re.I)
PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PHONE = re.compile(r"(?<!\w)\+?\d[\d\s().\-]{7,}\d(?!\w)")
DATE = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2}|"
    r"\d{1,2}\s(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s\d{4})\b",
    re.I,
)
CODE = re.compile(r"\b[A-Z]{1,10}[-/][A-Z0-9][A-Z0-9./\-]*\b")
RANGE = re.compile(
    r"\d+(?:[.,]\d+)?\s*%?\s*(?:±|\+/-|to|–|-)\s*\d+(?:[.,]\d+)?\s*%?",
    re.I,
)
MEASURE = re.compile(
    r"\d+(?:[.,]\d+)?\s*(?:°\s*[CFcf]|%|kg|g|mg|ml|mL|l|L|mm|cm|m|km|pcs|nos)\b"
)
NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?%?")
ABBREV = re.compile(
    r"\b(?:pH|QC|QA|GMP|GLP|HPLC|GC|UV|IR|RPM|SOP|COA|USD|INR|GSTIN|GST|PAN|ID|PDF|API)\b"
)
# Product and service names that should stay Latin in a Gujarati page.
BRAND = re.compile(
    r"\b(?:YouTube|Vimeo|GoogleMaps|Google\s*Maps|Google\s*Alerts?|Facebook|"
    r"Instagram|LinkedIn|WhatsApp|Gmail|Outlook|Excel|Word|PowerPoint|Windows|"
    r"Android|iPhone|iPad|Mac|iOS|HTML|CSS|JSON|XML|HTTP|HTTPS|SMTP|"
    r"HoneyXP|Northwind)\b",
    re.I,
)
# CamelCase tokens such as GoogleMaps that are not already covered above.
CAMEL = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b")
PERSON = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr|Prof)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\b"
)
IDENTIFIER_LINE = re.compile(
    r"(?i)^(?:sop|invoice|inv|gstin|gst|pan|batch|serial|lot|product|item|hsn|sac|"
    r"po|p\.o\.|ref|reference|document|doc|report|awb|lr|challan|code|mfg|expiry|exp)"
    r"\b\.?\s*(?:no\.?|number|code|#)?\s*[:.\-]?\s*\S.*$"
)

PATTERNS = [
    EMAIL,
    URL,
    HANDLE,
    PERSON,
    BRAND,
    CAMEL,
    GSTIN,
    PAN,
    PHONE,
    DATE,
    CODE,
    RANGE,
    MEASURE,
    ABBREV,
    NUMBER,
]


@dataclass
class Segment:
    kind: str
    value: str


def is_identifier_line(text: str) -> bool:
    return bool(IDENTIFIER_LINE.match(text.strip()))


def needs_translation(text: str) -> bool:
    return len(re.findall(r"[A-Za-z]", text)) >= 2


def protected_numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def protected_dates(text: str) -> list[str]:
    return [match.group(0) for match in DATE.finditer(text)]


def segment_text(text: str) -> list[Segment]:
    stripped = text.strip()
    if not stripped:
        return [Segment("keep", text)]
    if is_identifier_line(stripped):
        return [Segment("keep", text)]
    spans = _spans(text)
    if not spans:
        return [Segment("text", text)]
    parts: list[Segment] = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            parts.append(Segment("text", text[cursor:start]))
        parts.append(Segment("keep", text[start:end]))
        cursor = end
    if cursor < len(text):
        parts.append(Segment("text", text[cursor:]))
    return parts


def shield(text: str) -> tuple[str, list[str]]:
    """Swap protected tokens for placeholders so glossary edits cannot touch them."""
    held: list[str] = []

    def stash(match: re.Match) -> str:
        held.append(match.group(0))
        return _marker(len(held) - 1)

    masked = text
    for pattern in PATTERNS:
        masked = pattern.sub(stash, masked)
    return masked, held


def unshield(text: str, held: list[str]) -> str:
    restored = text
    for index, value in enumerate(held):
        restored = restored.replace(_marker(index), value)
    return restored


def _marker(index: int) -> str:
    # Letter-only markers so number / code patterns cannot re-match them.
    n = index
    letters = []
    while True:
        letters.append(chr(97 + (n % 26)))
        n = n // 26 - 1
        if n < 0:
            break
    return "⟦AK" + "".join(reversed(letters)) + "⟧"


def _spans(text: str) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for pattern in PATTERNS:
        for match in pattern.finditer(text):
            if match.end() > match.start():
                found.append((match.start(), match.end()))
    found.sort(key=lambda item: (item[0], -(item[1] - item[0])))
    chosen: list[tuple[int, int]] = []
    last = -1
    for start, end in found:
        if start < last:
            continue
        chosen.append((start, end))
        last = end
    return chosen
