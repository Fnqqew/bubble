"""Idiomas soportados (los más comunes en Roblox) y sus variantes regionales."""

from __future__ import annotations

LANGUAGES: dict[str, str] = {
    "es": "Spanish",
    "en": "English",
    "pt": "Portuguese",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "ru": "Russian",
    "tr": "Turkish",
    "pl": "Polish",
    "nl": "Dutch",
    "id": "Indonesian",
    "tl": "Tagalog",
    "vi": "Vietnamese",
    "th": "Thai",
    "ar": "Arabic",
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "hi": "Hindi",
}

# Nombres para mostrar en la interfaz.
DISPLAY_NAMES: dict[str, str] = {
    "es": "Español",
    "en": "Inglés",
    "pt": "Portugués",
    "fr": "Francés",
    "de": "Alemán",
    "it": "Italiano",
    "ru": "Ruso",
    "tr": "Turco",
    "pl": "Polaco",
    "nl": "Neerlandés",
    "id": "Indonesio",
    "tl": "Tagalo",
    "vi": "Vietnamita",
    "th": "Tailandés",
    "ar": "Árabe",
    "ja": "Japonés",
    "ko": "Coreano",
    "zh": "Chino",
    "hi": "Hindi",
}

# Cómo se le describe a Claude cada variante: (la variante, su jerga). La variante guía el vocabulario y el trato
# (vos/tú/usted); la jerga se nombra solo cuando el tono la pide (en los tonos 1 y 2, "como escriben los gamers" hacía
# que hasta el tono neutro saliera con "dude" y "for real").
VARIANTS: dict[tuple[str, str], tuple[str, str]] = {
    ("es", "AR"): ("Rioplatense Spanish from Argentina/Uruguay (voseo: 'vos tenés')",
                   "slang like che, re, posta, joya, dale"),
    ("es", "MX"): ("Mexican Spanish (tú)", "slang like wey, neta, chido, órale"),
    ("es", "ES"): ("Spanish from Spain (tú/vosotros)", "slang like tío, mola, guay, vale"),
    ("es", "CO"): ("Colombian Spanish", "slang like parce, bacano"),
    ("es", "CL"): ("Chilean Spanish", "slang like weón, cachai, bacán"),
    ("es", ""): ("neutral Latin American Spanish (tú), understandable in every Spanish-speaking country", ""),
    ("pt", "BR"): ("Brazilian Portuguese", "as young gamers write it online (vc, blz, vlw, mano, kkkk)"),
    ("pt", "PT"): ("European Portuguese", ""),
    ("en", "US"): ("American English", "as young gamers write it online"),
    ("en", "GB"): ("British English", "casual British slang"),
    ("fr", "FR"): ("French from France", "as young gamers write it online"),
    ("fr", "CA"): ("Québécois French", ""),
    ("zh", "CN"): ("Simplified Chinese (mainland)", ""),
    ("zh", "TW"): ("Traditional Chinese (Taiwan)", ""),
    ("hi", "IN"): ("Hinglish: Hindi written in Latin letters the way Indian gamers type it",
                   "bhai, kya, nahi, yaar"),
}
DEFAULT_REGION: dict[str, str] = {"es": "", "pt": "BR", "en": "US", "fr": "FR", "zh": "CN", "hi": "IN"}

# Opciones de la interfaz: (código, nombre visible).
LOCALE_CHOICES: list[tuple[str, str]] = [
    ("es-AR", "Español (Argentina / Uruguay)"),
    ("es-MX", "Español (México)"),
    ("es-ES", "Español (España)"),
    ("es-CO", "Español (Colombia)"),
    ("es-CL", "Español (Chile)"),
    ("es", "Español (neutro)"),
    ("pt-BR", "Portugués (Brasil)"),
    ("pt-PT", "Portugués (Portugal)"),
    ("en-US", "Inglés (EE. UU.)"),
    ("en-GB", "Inglés (Reino Unido)"),
    *[(code, name) for code, name in DISPLAY_NAMES.items() if code not in ("es", "pt", "en")],
]


def language_name(code: str) -> str:
    return LANGUAGES.get(code, code)


def split_locale(code: str) -> tuple[str, str]:
    """'es-AR' -> ('es', 'AR'); 'pt' -> ('pt', 'BR') usando la región por defecto."""
    lang, _, region = code.replace("_", "-").partition("-")
    lang = lang.lower()
    return lang, (region.upper() if region else DEFAULT_REGION.get(lang, ""))


def describe(lang: str, region: str = "", slang: bool = True) -> str:
    """Descripción de la variante para el prompt. `slang`: con su jerga (para los tonos informales y lo que te
    escriben a vos)."""
    variant = VARIANTS.get((lang, region)) or VARIANTS.get((lang, DEFAULT_REGION.get(lang, "")))
    if variant is None:
        return language_name(lang)
    base, extra = variant
    return f"{base}; {extra}" if slang and extra else base
