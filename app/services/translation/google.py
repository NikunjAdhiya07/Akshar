"""English to Gujarati translation providers.

Google is tried first. If it is rate-limited, MyMemory is used. Either
provider can be replaced by editing `get_translator()` without touching
layout or export.
"""

from __future__ import annotations

import json
import re
import threading
import time
from html import unescape

from app.config import CACHE_DIR, ensure_dirs

_lock = threading.Lock()
_GUJARATI = re.compile(r"[\u0A80-\u0AFF]")


class TranslationError(Exception):
    pass


class GoogleGujaratiTranslator:
    name = "google"

    def __init__(self) -> None:
        self._cache = _load_cache()
        self._google_open = True

    def translate_batch(self, texts: list[str], force: bool = False) -> list[str]:
        results: list[str | None] = [None] * len(texts)
        pending: list[int] = []
        for index, text in enumerate(texts):
            cached = None if force else self._cache.get(text)
            if cached and _usable(text, cached):
                results[index] = cached
            else:
                pending.append(index)
        if pending:
            translated = self._translate_pending([texts[index] for index in pending])
            for index, value in zip(pending, translated):
                results[index] = value
                if _usable(texts[index], value):
                    self._cache[texts[index]] = value
            _save_cache(self._cache)
        return [value if value is not None else texts[index] for index, value in enumerate(results)]

    def _translate_pending(self, texts: list[str]) -> list[str]:
        output: list[str] = []
        failures = 0
        last_error: Exception | None = None
        for text in texts:
            try:
                output.append(self._one(text))
            except Exception as exc:
                last_error = exc
                failures += 1
                output.append(text)
            time.sleep(0.15)
        if failures == len(texts) and last_error is not None:
            raise TranslationError(
                "Translation service is unavailable. Check the network and try again."
            ) from last_error
        return output

    def _one(self, text: str) -> str:
        if len(text) > 420:
            return " ".join(self._one(part) for part in _split_long(text))
        if self._google_open:
            try:
                value = _gtx(text)
                if _usable(text, value):
                    return value
            except Exception:
                self._google_open = False
        value = _mymemory(text)
        if not _usable(text, value):
            raise TranslationError("The translation service returned no Gujarati.")
        return value


def _split_long(text: str) -> list[str]:
    if len(text) <= 380:
        return [text]
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > 380:
            if current:
                chunks.append(current.strip())
                current = ""
            for start in range(0, len(sentence), 380):
                chunks.append(sentence[start : start + 380])
            continue
        if len(current) + len(sentence) > 380 and current:
            chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk] or [text[:380]]


def _gtx(text: str) -> str:
    import requests

    response = requests.get(
        "https://translate.googleapis.com/translate_a/single",
        params={"client": "gtx", "sl": "en", "tl": "gu", "dt": "t", "q": text},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=20,
    )
    if response.status_code == 429:
        raise TranslationError("Google Translate is rate-limiting requests.")
    response.raise_for_status()
    data = response.json()
    return _clean("".join(part[0] for part in data[0] if part and part[0]))


def _mymemory(text: str) -> str:
    import requests

    response = requests.get(
        "https://api.mymemory.translated.net/get",
        params={"q": text, "langpair": "en|gu"},
        timeout=25,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("quotaFinished"):
        raise TranslationError("The translation quota is exhausted. Try again later.")
    value = _clean((payload.get("responseData") or {}).get("translatedText") or "")
    if "MYMEMORY WARNING" in value.upper() or value.upper().startswith("QUERY LENGTH"):
        raise TranslationError(value)
    return value


def _usable(source: str, translated: str) -> bool:
    if not translated or "MYMEMORY WARNING" in translated.upper():
        return False
    if len(re.findall(r"[A-Za-z]", source)) < 2:
        return True
    return _GUJARATI.search(translated) is not None


def _clean(value: str) -> str:
    return unescape(value or "").strip()


def _cache_path():
    ensure_dirs()
    return CACHE_DIR / "gujarati.json"


def _load_cache() -> dict[str, str]:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    cleaned = {}
    for key, value in data.items():
        text = str(value)
        if "Arizona" in text or "MYMEMORY" in text.upper():
            continue
        cleaned[str(key)] = text
    return cleaned


def _save_cache(cache: dict[str, str]) -> None:
    if len(cache) > 5000:
        for key in list(cache)[: len(cache) - 5000]:
            cache.pop(key, None)
    path = _cache_path()
    with _lock:
        path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def get_translator(name: str | None = None) -> GoogleGujaratiTranslator:
    chosen = (name or "google").lower()
    if chosen != "google":
        raise ValueError(f"Unknown translation provider: {chosen}")
    return GoogleGujaratiTranslator()
