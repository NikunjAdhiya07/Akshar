"""Translate text blocks, preserving protected tokens and paragraph structure."""

from __future__ import annotations

import re

from app.models import TextBlock
from app.services.translation import glossary, protector
from app.services.translation.google import TranslationError

_PAGE = re.compile(r"(?i)^page\s+(\d+)\s+of\s+(\d+)$")
_GUJARATI = re.compile(r"[\u0A80-\u0AFF]")
_ENGLISH_WORD = re.compile(r"[A-Za-z]{2,}")


def translate_blocks(blocks: list[TextBlock], provider, force: bool = False) -> None:
    pending: list[tuple[TextBlock, list[protector.Segment]]] = []
    needed: list[str] = []
    for block in blocks:
        if block.edited and block.translation and not force:
            continue
        if block.preserved and not _must_translate(block.text) and not force:
            block.translation = block.text
            continue
        block.preserved = False
        exact = glossary.lookup(block.text)
        if exact and not force:
            block.translation = exact
            block.preserved = False
            continue
        if protector.is_identifier_line(block.text):
            block.translation = block.text
            block.preserved = True
            continue
        page = _PAGE.match(block.text.strip())
        if page:
            block.translation = f"પાનું {page.group(1)} માંથી {page.group(2)}"
            block.preserved = False
            continue
        composed = glossary.compose(block.text)
        if composed and not force:
            block.translation = composed
            continue
        if not protector.needs_translation(block.text):
            block.translation = block.text
            block.preserved = True
            continue
        segments = protector.segment_text(block.text)
        pending.append((block, segments))
        for segment in segments:
            if segment.kind == "text" and protector.needs_translation(segment.value):
                needed.append(segment.value.strip())
    unique = list(dict.fromkeys(text for text in needed if text))
    mapping: dict[str, str] = {}
    if unique:
        translated = provider.translate_batch(unique, force=force)
        if len(translated) != len(unique):
            raise TranslationError("The translator returned a different number of segments.")
        mapping = dict(zip(unique, translated))
    for block, segments in pending:
        had_words = any(
            segment.kind == "text" and protector.needs_translation(segment.value)
            for segment in segments
        )
        block.translation = _join(segments, mapping)
        if not had_words:
            block.translation = block.text
            block.preserved = True
        else:
            block.preserved = False
    for block in blocks:
        if block.edited and not force:
            continue
        if block.preserved and not force:
            continue
        block.translation = _polish(block.translation or "", provider, force=force)
        if _GUJARATI.search(block.translation or ""):
            block.preserved = False


def translate_text(text: str, provider, force: bool = False) -> str:
    probe = TextBlock(
        id="probe",
        page=0,
        bbox=[0, 0, 10, 10],
        draw_bbox=[0, 0, 10, 10],
        source_boxes=[[0, 0, 10, 10]],
        text=text,
    )
    translate_blocks([probe], provider, force=force)
    return probe.translation


def _must_translate(text: str) -> bool:
    """Ordinary words were sometimes marked as codes. They still need Gujarati."""
    stripped = text.strip()
    if protector.is_identifier_line(stripped):
        return False
    token = re.sub(r"^[\s.:;\-]+|[\s.:;\-]+$", "", stripped)
    if protector.ABBREV.fullmatch(token):
        return False
    return protector.needs_translation(stripped)


def _join(segments: list[protector.Segment], mapping: dict[str, str]) -> str:
    parts: list[str] = []
    for segment in segments:
        if segment.kind == "keep" or not protector.needs_translation(segment.value):
            parts.append(segment.value)
            continue
        key = segment.value.strip()
        translated = mapping.get(key, segment.value)
        if segment.value[:1].isspace():
            translated = " " + translated.lstrip()
        if segment.value[-1:].isspace():
            translated = translated.rstrip() + " "
        parts.append(translated)
    return "".join(parts).strip()


def _polish(text: str, provider, force: bool = False) -> str:
    """Fill leftover ordinary English after the main pass, without touching keepers."""
    updated = glossary.apply_known(text)
    leftovers = [
        segment.value.strip()
        for segment in protector.segment_text(updated)
        if segment.kind == "text" and _leftover_needs_work(segment.value)
    ]
    unique = list(dict.fromkeys(item for item in leftovers if item))
    if not unique:
        return updated
    # Prefer glossary fills before another provider round for short leftovers.
    filled = glossary.apply_known(updated)
    still = [
        segment.value.strip()
        for segment in protector.segment_text(filled)
        if segment.kind == "text" and _leftover_needs_work(segment.value)
    ]
    unique = list(dict.fromkeys(item for item in still if item and len(item) >= 4))
    if not unique:
        return filled
    try:
        translated = provider.translate_batch(unique, force=force)
    except TranslationError:
        return filled
    if len(translated) != len(unique):
        return filled
    mapping = dict(zip(unique, translated))
    rebuilt = _join(protector.segment_text(filled), mapping)
    return glossary.apply_known(rebuilt)


def _leftover_needs_work(text: str) -> bool:
    if not protector.needs_translation(text):
        return False
    # Skip pure proper-name fragments that the protector already missed.
    words = _ENGLISH_WORD.findall(text)
    if not words:
        return False
    if all(word[:1].isupper() and word[1:].islower() for word in words) and len(words) <= 3:
        if not any(word.casefold() in glossary.GLOSSARY for word in words):
            return False
    return True
