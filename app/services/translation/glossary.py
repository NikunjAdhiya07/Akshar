"""Exact block glossary. Sentences still go to the translation provider."""

from __future__ import annotations

import re

from app.services.translation import protector

# Whole-block phrases and common words. The provider still translates the rest.
GLOSSARY = {
    "quality": "ગુણવત્તા",
    "quality control department": "ગુણવત્તા નિયંત્રણ વિભાગ",
    "honeyxp": "હની એક્સપી",
    "purity excellence": "શુદ્ધતા અને શ્રેષ્ઠતા",
    "premium raw": "પ્રીમિયમ કાચો",
    "wild forest honey": "જંગલી વન મધ",
    "harvested from the himalayan foothills net wt 24 oz 680g": "હિમાલયની તળેટીમાંથી લણેલું. નેટ વજન: 24 OZ (680g)",
    "certificate of analysis": "વિશ્લેષણનું પ્રમાણપત્ર",
    "quality assurance": "ગુણવત્તા ખાતરી",
    "stability summary": "સ્થિરતા સારાંશ",
    "finished product release": "તૈયાર ઉત્પાદન મુક્તિ",
    "controlled document": "નિયંત્રિત દસ્તાવેજ",
    "specification": "વિશિષ્ટતા",
    "parameter": "પરિમાણ",
    "result": "પરિણામ",
    "appearance": "દેખાવ",
    "complies": "અનુરૂપ",
    "white crystalline powder": "સફેદ સ્ફટિકમય પાવડર",
    "released": "મુક્ત",
    "diagram": "આકૃતિ",
    "storage note": "સંગ્રહ નોંધ",
    "not more than": "વધુમાં વધુ",
    "net wt": "નેટ વજન",
    "honey xp raw pure unfiltered himalayan heritage": "હની એક્સપી • કાચું • શુદ્ધ • ગાળ્યા વગરનું • હિમાલયન વારસો",
    "approved by": "મંજૂર કરનાર",
    "checked by": "ચકાસનાર",
    "remarks": "ટિપ્પણી",
    "hello": "નમસ્તે",
    "twitter": "ટ્વિટર",
    "the": "ધ",
    "gold": "સુવર્ણ",
    "experience": "અનુભવ",
    "honey": "મધ",
    "xp": "એક્સપી",
    "power": "શક્તિ",
    "gift": "ભેટ",
    "lover": "શોખીન",
    "recovery": "પુનઃપ્રાપ્તિ",
    "athoughtful": "વિચારપૂર્વકનું",
    "thoughtful": "વિચારપૂર્વકનું",
    "to": "પ્રતિ",
    "from": "પ્રેષક",
    "subject": "વિષય",
    "cc": "સીસી",
    "bcc": "બીસીસી",
    "dear": "પ્રિય",
    "best": "આપની",
    "subscribers": "સબ્સ્ક્રાઇબર્સ",
    "email": "ઇમેઇલ",
    "date": "તારીખ",
    "please": "કૃપા કરીને",
    "note": "નોંધ",
    "using this survey on our website": "અમારી વેબસાઇટ પર આ સર્વેક્ષણનો ઉપયોગ કરીને",
}

# Short labels that still need a Gujarati form inside mixed lines.
_SHORT = {"to", "from", "subject", "cc", "bcc", "dear", "best", "hello", "the", "gold", "xp"}


def lookup(text: str) -> str | None:
    return GLOSSARY.get(_normalize(text))


def compose(text: str) -> str | None:
    """Replace known phrases. Use the result only when no English is left."""
    replaced = apply_known(text)
    if replaced == text:
        return None
    leftover = re.sub(r"\b(?:oz|ml|kg|mg|mm|cm|wt|g)\b", " ", replaced, flags=re.I)
    if re.search(r"[A-Za-z]{2,}", leftover):
        return None
    return re.sub(r"\s+", " ", replaced).strip()


def apply_common(text: str) -> str:
    """Convert everyday glossary words while leaving protected tokens alone."""
    return apply_known(text)


def apply_known(text: str) -> str:
    """Swap known English for Gujarati without touching emails, brands, or codes."""
    if not text:
        return text
    masked, held = protector.shield(text)
    phrases = sorted(GLOSSARY.items(), key=lambda item: len(item[0]), reverse=True)
    updated = masked
    for english, gujarati in phrases:
        if " " in english:
            updated = re.sub(re.escape(english), gujarati, updated, flags=re.I)
            continue
        if len(english) < 4 and english not in _SHORT:
            continue
        updated = re.sub(rf"\b{re.escape(english)}\b", gujarati, updated, flags=re.I)
    return protector.unshield(updated, held)


def _normalize(text: str) -> str:
    cleaned = text.casefold()
    cleaned = re.sub(r"[•·∙|]+", " ", cleaned)
    cleaned = re.sub(r"[^0-9a-z\u0a80-\u0aff%]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()
