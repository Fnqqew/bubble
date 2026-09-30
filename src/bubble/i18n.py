"""Bubble en tu idioma: la ventana, el tutorial y los avisos del juego se muestran en el idioma de tu Windows.

Los textos se escriben en español en el código. Cada idioma tiene su traducción en locales/<código>.json (los más
comunes en Roblox vienen con Bubble; ver tools/ui_strings.py). Para los demás, Bubble la arma la primera vez con tu
Claude y la guarda en %APPDATA%\\Bubble\\idiomas (se usa desde la próxima vez que lo abras). Sin traducción, en inglés.

Para no marcar cada texto a mano, se traduce al dibujarlo: todo texto que se le da a una ventana (text=, label=, el
título) pasa por `t()`. Los textos con variables se reconocen por su plantilla ("Salió Bubble {0}…").
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)
SOURCE = "es"  # el idioma en que están escritos los textos
FALLBACK = "en"
LOCALES = Path(__file__).resolve().parent / "locales"
_lang = SOURCE
_exact: dict[str, str] = {}
_templates: dict[str, list[tuple[re.Pattern, str]]] = {}  # primeras letras → [(plantilla, traducción)]
_loose: list[tuple[re.Pattern, str]] = []  # plantillas que empiezan con una variable
_SLOT = re.compile(r"(?<!\{)\{(\d+)\}(?!\})")


def language() -> str:
    return _lang


def user_folder() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Bubble" / "idiomas"


def system_language() -> str:
    """El idioma de la interfaz de Windows ("pt", "fr"…)."""
    try:
        import ctypes

        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        buffer = ctypes.create_unicode_buffer(85)
        if ctypes.windll.kernel32.LCIDToLocaleName(lcid, buffer, 85, 0):
            return buffer.value.split("-")[0].lower()
    except (AttributeError, OSError):
        pass
    return SOURCE


def choose(setting: str = "auto") -> str:
    """El idioma de la interfaz: el elegido en Ajustes o, en «auto», el de Windows (si Bubble lo conoce; si no,
    inglés)."""
    from .translate.languages import LANGUAGES

    code = system_language() if setting in ("", "auto") else setting.split("-")[0].lower()
    return code if code in LANGUAGES else FALLBACK


def catalog_path(code: str) -> Path | None:
    """Dónde está la traducción de ese idioma (la armada en esta PC gana: es la más nueva)."""
    for folder in (user_folder(), LOCALES):
        path = folder / f"{code}.json"
        if path.exists():
            return path
    return None


def has_catalog(code: str) -> bool:
    return code == SOURCE or catalog_path(code) is not None


def use(code: str) -> str:
    """Pasa la interfaz a ese idioma. Si no hay traducción, en inglés. Devuelve el idioma que quedó."""
    global _lang
    if code != SOURCE and not has_catalog(code):
        code = FALLBACK if has_catalog(FALLBACK) else SOURCE
    _exact.clear()
    _templates.clear()
    _loose.clear()
    t.cache_clear()
    _lang = code
    if code == SOURCE:
        return code
    try:
        table = json.loads(catalog_path(code).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        log.warning("No se pudo leer la traducción de la interfaz (%s)", code, exc_info=True)
        _lang = SOURCE
        return SOURCE
    for source, translated in table.items():
        if not isinstance(translated, str):
            continue
        if _SLOT.search(source):
            pattern = _pattern(source)
            if pattern is None:
                continue
            head = source.split("{", 1)[0][:3]
            (_templates.setdefault(head, []) if head else _loose).append((pattern, translated))
        _exact[source.replace("{{", "{").replace("}}", "}")] = translated.replace("{{", "{").replace("}}", "}")
    return code


def _pattern(source: str) -> re.Pattern | None:
    parts = []
    last = 0
    for match in _SLOT.finditer(source):
        parts.append(re.escape(source[last:match.start()].replace("{{", "{").replace("}}", "}")))
        parts.append(f"(?P<v{match.group(1)}>.+?)")
        last = match.end()
    parts.append(re.escape(source[last:].replace("{{", "{").replace("}}", "}")))
    try:
        return re.compile("^" + "".join(parts) + "$", re.DOTALL)
    except re.error:
        return None


@lru_cache(maxsize=4096)
def t(text: str) -> str:
    """El texto en el idioma de la interfaz (si no se conoce, igual)."""
    if _lang == SOURCE or not text or not isinstance(text, str):
        return text
    found = _exact.get(text)
    if found is not None:
        return found
    for pattern, translated in [*_templates.get(text[:3], ()), *_loose]:
        match = pattern.match(text)
        if match:
            values = {int(name[1:]): t(value) for name, value in match.groupdict().items()}
            return _SLOT.sub(lambda slot: values.get(int(slot.group(1)), slot.group(0)), translated) \
                .replace("{{", "{").replace("}}", "}")
    symbols = len(text) - len(text.lstrip("•✓⚠✦🔒→·- \u2003"))  # "• Texto", "✓ Listo…": el símbolo queda
    if 0 < symbols < len(text):
        inner = t(text[symbols:])
        if inner != text[symbols:]:
            return text[:symbols] + inner
    if text.islower() and len(text) < 40:  # "casual", "neutro / formal" (en minúscula dentro de otra frase)
        found = _exact.get(text[:1].upper() + text[1:])
        if found is not None:
            return found.lower()
    stripped = text.strip()  # "Texto\n" o "  Texto": con la traducción del texto de adentro
    if stripped != text and stripped:
        inner = t(stripped)
        if inner != stripped:
            return text.replace(stripped, inner)
    return text


# ---------------------------------------------------------------- la ventana (Tk)
_installed = False
_OPTIONS = ("text", "label", "title")


def install() -> None:
    """Todo texto que se le da a la ventana (al crear un control o al cambiarlo) pasa por `t()`."""
    global _installed
    if _installed:
        return
    import tkinter

    original_options = tkinter.Misc._options
    original_title = tkinter.Wm.wm_title

    def options(self, cnf, kw=None):
        if _lang != SOURCE:
            cnf = _translated(cnf)
            kw = _translated(kw)
        return original_options(self, cnf, kw)

    def title(self, string=None):
        return original_title(self, t(string) if isinstance(string, str) else string)

    tkinter.Misc._options = options
    tkinter.Wm.wm_title = title
    tkinter.Wm.title = title
    _installed = True


def _translated(options):
    if not isinstance(options, dict) or not any(key in options for key in _OPTIONS):
        return options
    options = dict(options)
    for key in _OPTIONS:
        value = options.get(key)
        if isinstance(value, str):
            options[key] = t(value)
    return options


# ---------------------------------------------------------------- idiomas que no vienen con Bubble
def sources() -> list[str]:
    try:
        return json.loads((LOCALES / "_textos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def build_in_background(code: str, done=lambda _ok: None) -> threading.Thread | None:
    """Arma la traducción de la interfaz a ese idioma con tu Claude (una sola vez: queda guardada). `done(ok)`."""
    from .translate.languages import LANGUAGES

    if code == SOURCE or code not in LANGUAGES or catalog_path(code) is not None:
        return None

    def work() -> None:
        try:
            from .tools.ui_strings import translate

            texts = sources()
            table = translate(texts, LANGUAGES[code], workers=2) if texts else {}
            ok = len(table) >= 0.9 * len(texts) > 0
            if ok:
                user_folder().mkdir(parents=True, exist_ok=True)
                (user_folder() / f"{code}.json").write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001 - sin traducción, en inglés
            log.warning("No se pudo armar la traducción de la interfaz (%s)", code, exc_info=True)
            ok = False
        done(ok)

    thread = threading.Thread(target=work, name="bubble-idioma", daemon=True)
    thread.start()
    return thread
