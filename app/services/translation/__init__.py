"""Translation package. Providers are replaceable from this boundary."""

from app.services.translation.google import get_translator
from app.services.translation.service import translate_blocks, translate_text

__all__ = ["get_translator", "translate_blocks", "translate_text"]
