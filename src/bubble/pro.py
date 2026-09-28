"""Bubble Pro: la voz se entiende en la nube (Deepgram) en vez de en tu procesador. La traducción sigue con tu Claude.

Cómo se paga: con tu propia cuenta de Deepgram, que cobra por minuto de voz que se le manda (no es una suscripción).
La cuenta nueva trae crédito gratis. Bubble guarda tu clave cifrada en tu PC (cloud/keys.py) y solo le manda audio
cuando alguien habla: los silencios no se pagan.

Cuando está activo, la ventana se pone dorada y dice "Bubble Pro", para que se note.
"""

from __future__ import annotations

import datetime
import threading

GOLD = {"oscuro": "#f2c14e", "claro": "#9a6700"}
GOLD_RGB = (242, 193, 78)
# Deepgram, US$ (precios del 28/9/2026, deepgram.com/pricing). En vivo: Nova-3 multilingüe (varios idiomas mezclados)
# o de un idioma; una frase entera (el botón) sale más barata. Quién habla y las palabras priorizadas, aparte.
PRICE_PER_MIN = 0.0058  # en vivo, multilingüe
MONO_PER_MIN = 0.0048  # en vivo, un idioma
CLIP_PER_MIN = 0.0052  # una frase entera (multilingüe)
DIARIZE_PER_MIN = 0.0020
KEYTERM_PER_MIN = 0.0013
TTS_PER_1K_CHARS = 0.030  # voces de la nube (Aura-2)
SIGNUP_URL = "https://console.deepgram.com/signup"
FREE_CREDIT_USD = 200

_active = False
_lock = threading.Lock()


def active() -> bool:
    """¿Está andando Bubble Pro (activado y con una clave que sirve)?"""
    return _active


def set_active(value: bool) -> None:
    global _active
    _active = bool(value)


def gold() -> str:
    from .ui import theme

    return GOLD.get(theme.current(), GOLD["oscuro"])


def listen_cost(seconds: float, multi: bool = True, diarized: bool = False, keyterms: bool = False,
                clip: bool = False) -> float:
    """Lo que cuesta entender tantos segundos de voz (US$)."""
    per_minute = CLIP_PER_MIN if clip else (PRICE_PER_MIN if multi else MONO_PER_MIN)
    per_minute += (DIARIZE_PER_MIN if diarized else 0.0) + (KEYTERM_PER_MIN if keyterms else 0.0)
    return seconds / 60 * per_minute


def _add(seconds: float = 0.0, chars: int = 0, cost: float = 0.0) -> None:
    from .state import load_state, update_state

    month = datetime.date.today().strftime("%Y-%m")
    with _lock:
        usage = load_state().get("pro_usage", {})
        entry = usage.setdefault(month, {"seconds": 0.0})
        if "cost" not in entry:  # meses anotados con la versión anterior
            entry["cost"] = entry.get("seconds", 0.0) / 60 * PRICE_PER_MIN + entry.get("diarized", 0.0) / 60 * DIARIZE_PER_MIN
        entry["seconds"] = round(entry.get("seconds", 0.0) + seconds, 1)
        entry["chars"] = entry.get("chars", 0) + chars
        entry["cost"] = round(entry["cost"] + cost, 5)
        update_state(pro_usage=usage)


def count_seconds(seconds: float, diarized: bool = False, multi: bool = True, keyterms: bool = False,
                  clip: bool = False) -> None:
    """Suma la voz que se mandó a la nube este mes (para mostrar cuánto se gastó)."""
    if seconds > 0:
        _add(seconds=seconds, cost=listen_cost(seconds, multi, diarized, keyterms, clip))


def count_characters(chars: int) -> None:
    """Suma lo que dijeron las voces de la nube este mes."""
    if chars > 0:
        _add(chars=chars, cost=chars / 1000 * TTS_PER_1K_CHARS)


def month_usage() -> tuple[float, float]:
    """(minutos de voz entendidos por la nube este mes, costo aproximado en US$, con las voces)."""
    from .state import load_state

    entry = load_state().get("pro_usage", {}).get(datetime.date.today().strftime("%Y-%m"), {})
    minutes = entry.get("seconds", 0.0) / 60
    cost = entry.get("cost")
    if cost is None:
        cost = minutes * PRICE_PER_MIN + entry.get("diarized", 0.0) / 60 * DIARIZE_PER_MIN
    return minutes, cost


def month_characters() -> int:
    from .state import load_state

    return int(load_state().get("pro_usage", {}).get(datetime.date.today().strftime("%Y-%m"), {}).get("chars", 0))
