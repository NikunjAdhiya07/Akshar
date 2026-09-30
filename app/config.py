"""Paths and runtime settings."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

ON_VERCEL = bool(os.environ.get("VERCEL"))

FONTS_DIR = ROOT / "app" / "static" / "fonts"
# Keep repo-root fonts/ as a fallback for older checkouts / Docker images.
if not (FONTS_DIR / "NotoSansGujarati-Regular.ttf").is_file():
    FONTS_DIR = ROOT / "fonts"
FONT_REGULAR = FONTS_DIR / "NotoSansGujarati-Regular.ttf"
FONT_BOLD = FONTS_DIR / "NotoSansGujarati-Bold.ttf"
# Serverless has a read-only filesystem except /tmp.
DATA_DIR = Path("/tmp/akshar-data") if ON_VERCEL else (ROOT / "data")
JOBS_DIR = DATA_DIR / "jobs"
CACHE_DIR = DATA_DIR / "cache"
SAMPLES_DIR = ROOT / "samples"
STATIC_DIR = ROOT / "app" / "static"

# Lower raster DPI on Vercel so OCR finishes inside the function time budget.
BASE_DPI = 110 if ON_VERCEL else 150
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_RASTER_SIDE = 10_000

HOST = os.environ.get("AKSHAR_HOST") or (
    "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
)
PORT = int(os.environ.get("PORT") or os.environ.get("AKSHAR_PORT", "8765"))

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DATABASE_POOLER_URL = os.environ.get("DATABASE_POOLER_URL", "").strip()
SUPABASE_STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "akshar-documents").strip() or "akshar-documents"


def supabase_configured() -> bool:
    return bool(SUPABASE_URL and (SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY))


def ensure_dirs() -> None:
    # On Vercel only /tmp is writable; never mkdir into the read-only deploy tree.
    writable = (JOBS_DIR, CACHE_DIR) if ON_VERCEL else (JOBS_DIR, CACHE_DIR, SAMPLES_DIR, STATIC_DIR)
    for path in writable:
        path.mkdir(parents=True, exist_ok=True)


def ensure_fonts() -> None:
    missing = [str(path) for path in (FONT_REGULAR, FONT_BOLD) if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Bundled Unicode Gujarati font is missing: " + ", ".join(missing)
        )
