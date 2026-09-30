"""Bubble Pro: la voz se transcribe en la nube (Deepgram) en lugar del procesador local. La traducción sigue usando
Claude.

El costo corre por cuenta del jugador, con su propia cuenta de Deepgram, que cobra por minuto de voz enviado (no es
una suscripción). Las cuentas nuevas incluyen crédito gratuito. Bubble guarda la clave cifrada en el equipo
(cloud/keys.py) y solo envía audio cuando alguien habla: los silencios no se cobran.

Mientras está activo, la ventana se muestra dorada con el texto "Bubble Pro".
"""

from __future__ import annotations

import datetime
import threading

GOLD = {"oscuro": "#f2c14e", "claro": "#9a6700"}
GOLD_RGB = (242, 193, 78)
# Precios de Deepgram en US$ (deepgram.com/pricing). En vivo: Nova-3 multilingüe (varios idiomas mezclados) o de un solo
# idioma; una frase completa (el botón) es más barata. La identificación de hablantes y las palabras priorizadas se
# cobran aparte.
PRICE_PER_MIN = 0.0058  # en vivo, multilingüe
MONO_PER_MIN = 0.0048  # en vivo, un idioma
CLIP_PER_MIN = 0.0052  # frase completa (multilingüe)
DIARIZE_PER_MIN = 0.0020
KEYTERM_PER_MIN = 0.0013
TTS_PER_1K_CHARS = 0.030  # voces de la nube (Aura-2)
FLUX_PER_1K_CHARS = 0.045  # voces en inglés (Flux TTS, más expresivas)
TRANSLATE_PER_MIN = 0.075  # traducción sin Claude: agente de Deepgram, por minuto (cloud/agent.py)
SIGNUP_URL = "https://console.deepgram.com/signup"
NO_CLAUDE_WARNING = (("Sin Claude, traducir gasta créditos de Deepgram: unos 0,075 US$ por minuto de chat "
                      "activo. Una partida tranquila son centavos por hora; un servidor muy movido, hasta 4,50 "
                      "US$. Si conectás Claude, deja de gastar."))
FREE_CREDIT_USD = 200

_active = False
_lock = threading.Lock()


def active() -> bool:
    """Indica si Bubble Pro está en funcionamiento (activado y con una clave válida)."""
    return _active


def set_active(value: bool) -> None:
    global _active
    _active = bool(value)


def gold() -> str:
    from .ui import theme

    return GOLD.get(theme.current(), GOLD["oscuro"])


def listen_cost(seconds: float, multi: bool = True, diarized: bool = False, keyterms: bool = False,
                clip: bool = False) -> float:
    """Costo en US$ de transcribir la cantidad de segundos de voz indicada."""
    per_minute = CLIP_PER_MIN if clip else (PRICE_PER_MIN if multi else MONO_PER_MIN)
    per_minute += (DIARIZE_PER_MIN if diarized else 0.0) + (KEYTERM_PER_MIN if keyterms else 0.0)
    return seconds / 60 * per_minute


def _add(seconds: float = 0.0, chars: int = 0, cost: float = 0.0, translating: float = 0.0) -> None:
    from .state import load_state, update_state

    month = datetime.date.today().strftime("%Y-%m")
    with _lock:
        usage = load_state().get("pro_usage", {})
        entry = usage.setdefault(month, {"seconds": 0.0})
        if "cost" not in entry:  # meses registrados con la versión anterior
            entry["cost"] = entry.get("seconds", 0.0) / 60 * PRICE_PER_MIN + entry.get("diarized", 0.0) / 60 * DIARIZE_PER_MIN
        entry["seconds"] = round(entry.get("seconds", 0.0) + seconds, 1)
        entry["chars"] = entry.get("chars", 0) + chars
        if translating:
            entry["translating"] = round(entry.get("translating", 0.0) + translating, 1)
        entry["cost"] = round(entry["cost"] + cost, 5)
        update_state(pro_usage=usage)


def count_seconds(seconds: float, diarized: bool = False, multi: bool = True, keyterms: bool = False,
                  clip: bool = False) -> None:
    """Acumula los segundos de voz enviados a la nube en el mes, para mostrar el gasto."""
    if seconds > 0:
        _add(seconds=seconds, cost=listen_cost(seconds, multi, diarized, keyterms, clip))


def count_characters(chars: int, flux: bool = False) -> None:
    """Acumula los caracteres sintetizados por las voces de la nube en el mes."""
    if chars > 0:
        _add(chars=chars, cost=chars / 1000 * (FLUX_PER_1K_CHARS if flux else TTS_PER_1K_CHARS))


def count_translation(seconds: float) -> None:
    """Acumula el tiempo que estuvo abierta la traducción sin Claude (se cobra por minuto)."""
    if seconds > 0:
        _add(cost=seconds / 60 * TRANSLATE_PER_MIN, translating=seconds)


def month_translating() -> float:
    """Minutos de traducción sin Claude en el mes."""
    from .state import load_state

    entry = load_state().get("pro_usage", {}).get(datetime.date.today().strftime("%Y-%m"), {})
    return entry.get("translating", 0.0) / 60


def month_usage() -> tuple[float, float]:
    """Devuelve los minutos de voz transcritos por la nube en el mes y el costo aproximado en US$, incluidas las voces.
    """
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
