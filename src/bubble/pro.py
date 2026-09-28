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
# Deepgram (Nova-3 multilingüe, en vivo), US$ por minuto de voz; quién habla se cobra aparte. Medido el 28/9/2026.
PRICE_PER_MIN = 0.0058
DIARIZE_PER_MIN = 0.0020
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


def count_seconds(seconds: float, diarized: bool = False) -> None:
    """Suma lo que se le mandó a la nube este mes (para mostrar cuánto se gastó)."""
    if seconds <= 0:
        return
    from .state import load_state, update_state

    month = datetime.date.today().strftime("%Y-%m")
    with _lock:
        usage = load_state().get("pro_usage", {})
        entry = usage.setdefault(month, {"seconds": 0.0, "diarized": 0.0})
        entry["seconds"] = round(entry["seconds"] + seconds, 1)
        if diarized:
            entry["diarized"] = round(entry.get("diarized", 0.0) + seconds, 1)
        update_state(pro_usage=usage)


def month_usage() -> tuple[float, float]:
    """(minutos de voz mandados este mes, costo aproximado en US$)."""
    from .state import load_state

    entry = load_state().get("pro_usage", {}).get(datetime.date.today().strftime("%Y-%m"), {})
    minutes = entry.get("seconds", 0.0) / 60
    cost = minutes * PRICE_PER_MIN + entry.get("diarized", 0.0) / 60 * DIARIZE_PER_MIN
    return minutes, cost
