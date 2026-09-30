"""Idiomas con los que anda Bubble y sus variantes regionales.

Son todos los que el detector de idioma reconoce bien en mensajes cortos y que Claude traduce: primero los más comunes
en Roblox, después el resto. Casi todos tienen voz (ver voice/tts.py).
"""

from __future__ import annotations

# código → nombre en inglés (así se le nombran a Claude), nombre en español (la interfaz) y en ese idioma.
_TABLE = [
    ("es", "Spanish", "Español", "Español"),
    ("en", "English", "Inglés", "English"),
    ("pt", "Portuguese", "Portugués", "Português"),
    ("fr", "French", "Francés", "Français"),
    ("de", "German", "Alemán", "Deutsch"),
    ("it", "Italian", "Italiano", "Italiano"),
    ("ru", "Russian", "Ruso", "Русский"),
    ("tr", "Turkish", "Turco", "Türkçe"),
    ("pl", "Polish", "Polaco", "Polski"),
    ("nl", "Dutch", "Neerlandés", "Nederlands"),
    ("id", "Indonesian", "Indonesio", "Bahasa Indonesia"),
    ("tl", "Tagalog", "Tagalo", "Tagalog"),
    ("vi", "Vietnamese", "Vietnamita", "Tiếng Việt"),
    ("th", "Thai", "Tailandés", "ไทย"),
    ("ar", "Arabic", "Árabe", "العربية"),
    ("ja", "Japanese", "Japonés", "日本語"),
    ("ko", "Korean", "Coreano", "한국어"),
    ("zh", "Chinese", "Chino", "中文"),
    ("hi", "Hindi", "Hindi", "हिन्दी"),
    ("uk", "Ukrainian", "Ucraniano", "Українська"),
    ("sv", "Swedish", "Sueco", "Svenska"),
    ("no", "Norwegian", "Noruego", "Norsk"),
    ("da", "Danish", "Danés", "Dansk"),
    ("fi", "Finnish", "Finlandés", "Suomi"),
    ("cs", "Czech", "Checo", "Čeština"),
    ("sk", "Slovak", "Eslovaco", "Slovenčina"),
    ("hu", "Hungarian", "Húngaro", "Magyar"),
    ("ro", "Romanian", "Rumano", "Română"),
    ("el", "Greek", "Griego", "Ελληνικά"),
    ("bg", "Bulgarian", "Búlgaro", "Български"),
    ("sr", "Serbian", "Serbio", "Srpski"),
    ("hr", "Croatian", "Croata", "Hrvatski"),
    ("sl", "Slovenian", "Esloveno", "Slovenščina"),
    ("mk", "Macedonian", "Macedonio", "Македонски"),
    ("lt", "Lithuanian", "Lituano", "Lietuvių"),
    ("lv", "Latvian", "Letón", "Latviešu"),
    ("et", "Estonian", "Estonio", "Eesti"),
    ("be", "Belarusian", "Bielorruso", "Беларуская"),
    ("he", "Hebrew", "Hebreo", "עברית"),
    ("fa", "Persian", "Persa", "فارسی"),
    ("ur", "Urdu", "Urdu", "اردو"),
    ("bn", "Bengali", "Bengalí", "বাংলা"),
    ("mr", "Marathi", "Maratí", "मराठी"),
    ("te", "Telugu", "Telugu", "తెలుగు"),
    ("ta", "Tamil", "Tamil", "தமிழ்"),
    ("gu", "Gujarati", "Guyaratí", "ગુજરાતી"),
    ("pa", "Punjabi", "Panyabí", "ਪੰਜਾਬੀ"),
    ("ms", "Malay", "Malayo", "Bahasa Melayu"),
    ("ka", "Georgian", "Georgiano", "ქართული"),
    ("hy", "Armenian", "Armenio", "Հայերեն"),
    ("kk", "Kazakh", "Kazajo", "Қазақ"),
    ("az", "Azerbaijani", "Azerí", "Azərbaycan"),
    ("ca", "Catalan", "Catalán", "Català"),
    ("eu", "Basque", "Euskera", "Euskara"),
    ("cy", "Welsh", "Galés", "Cymraeg"),
    ("is", "Icelandic", "Islandés", "Íslenska"),
    ("sq", "Albanian", "Albanés", "Shqip"),
    ("af", "Afrikaans", "Afrikáans", "Afrikaans"),
    ("sw", "Swahili", "Suajili", "Kiswahili"),
]
LANGUAGES: dict[str, str] = {code: english for code, english, _es, _native in _TABLE}
DISPLAY_NAMES: dict[str, str] = {code: spanish for code, _en, spanish, _native in _TABLE}
NATIVE_NAMES: dict[str, str] = {code: native for code, _en, _es, native in _TABLE}
# Los que se escriben de derecha a izquierda (en el juego hay que ordenarlos a mano: ver ui/rtl.py).
RIGHT_TO_LEFT = {"ar", "he", "fa", "ur"}

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
