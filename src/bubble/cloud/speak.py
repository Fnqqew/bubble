"""Las voces de Bubble Pro: Deepgram Aura-2, voces naturales y con personalidad (alegre, canchera o tranquila), para tu
voz traducida y el chat a voz. Misma cara que las voces de tu PC (voice/tts.py: `Voices`), así el resto no cambia.

Idiomas con voz en la nube: inglés, español (con acento argentino, mexicano, colombiano, de España…), francés,
alemán, italiano, neerlandés y japonés. Los demás (portugués, ruso…) y cualquier falla: la voz de tu PC.
"""

from __future__ import annotations

import json
import logging
import threading
import urllib.parse
from collections import OrderedDict
from typing import Callable

import numpy as np

from .. import pro
from ..voice.tts import STYLES, Speech
from .connection import pool
from .errors import CloudError

log = logging.getLogger(__name__)
SAMPLE_RATE = 24000
PERSONALITIES = {"alegre": "Alegre", "canchera": "Canchera", "tranquila": "Tranquila"}
# idioma → (femenina, masculina) por personalidad. Las descripciones son las de Deepgram.
VOICES = {
    "en": {"alegre": ("thalia", "aries"),  # clara, enérgica, entusiasta
           "canchera": ("andromeda", "apollo"),  # casual, expresiva / seguro, casual
           "tranquila": ("helena", "arcas")},  # cálida, natural / suave, natural
    "es": {"alegre": ("celeste", "javier"),  # colombiana: enérgica, positiva
           "canchera": ("selena", "aquila"),  # latinoamericanos (pronuncian bien las palabras en inglés)
           "tranquila": ("estrella", "nestor")},  # mexicana: calma, natural / de España: calmo, claro
    "fr": {"alegre": ("agathe", "hector"), "canchera": ("agathe", "hector"), "tranquila": ("agathe", "hector")},
    "de": {"alegre": ("viktoria", "julius"), "canchera": ("viktoria", "julius"), "tranquila": ("elara", "fabian")},
    "it": {"alegre": ("livia", "dionisio"), "canchera": ("livia", "dionisio"), "tranquila": ("melia", "elio")},
    "nl": {"alegre": ("rhea", "sander"), "canchera": ("rhea", "lars"), "tranquila": ("daphne", "sander")},
    "ja": {"alegre": ("izanami", "fujin"), "canchera": ("izanami", "fujin"), "tranquila": ("uzume", "ebisu")},
}
# Con tu región, la voz de tu zona (la femenina: Deepgram tiene una argentina).
REGIONAL = {("es", "AR"): ("antonia", None), ("es", "UY"): ("antonia", None), ("es", "MX"): ("estrella", "javier"),
            ("es", "ES"): ("carina", "alvaro"), ("es", "CO"): ("celeste", None), ("en", "GB"): ("pandora", "draco"),
            ("en", "AU"): ("theia", "hyperion")}
SPEED_LANGUAGES = {"en", "es"}  # Deepgram deja cambiar la velocidad solo en estos
MAX_CHARS = 2000
CACHE_SIZE = 120  # frases dichas que se guardan (en memoria): repetir "gg" o "gracias" no se vuelve a pagar


def voice_name(language: str, gender: str = "femenina", personality: str = "canchera") -> str | None:
    """El modelo de Deepgram para ese idioma ("es-AR", "en"…), o None si la nube no tiene voz en ese idioma."""
    parts = language.replace("_", "-").split("-")
    code = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 else ""
    options = VOICES.get(code)
    if options is None:
        return None
    male = gender.startswith("m")
    female_name, male_name = options.get(personality, options["canchera"])
    local = REGIONAL.get((code, region))
    if local and personality == "canchera" and (local[1] if male else local[0]):
        female_name, male_name = local[0] or female_name, local[1] or male_name
    return f"aura-2-{male_name if male else female_name}-{code}"


class CloudVoices:
    """Las voces de la nube con la cara de `Voices` (tts.py). `local`: las de tu PC, para lo que la nube no tiene."""

    def __init__(self, key: str, local, on_fail: Callable[[CloudError], None] = lambda _exc: None,
                 personality: str = "canchera", timeout: float = 6.0) -> None:
        self.key = key
        self.local = local
        self.on_fail = on_fail
        self.personality = personality if personality in PERSONALITIES else "canchera"
        self.timeout = timeout
        self._ready: set[str] = set()
        self._cache: OrderedDict[tuple, np.ndarray] = OrderedDict()  # (voz, velocidad, texto) → audio (int16)
        self._cache_lock = threading.Lock()

    # género y velocidad: los mismos que las voces de tu PC (se eligen en la página Voz)
    @property
    def gender(self) -> str:
        return self.local.gender

    @gender.setter
    def gender(self, value: str) -> None:
        self.local.gender = value

    @property
    def speed(self) -> float:
        return self.local.speed

    @speed.setter
    def speed(self, value: float) -> None:
        self.local.speed = value

    def voice_for(self, language: str, gender: str = "femenina") -> str | None:
        return voice_name(language, gender, self.personality) or self.local.voice_for(language, gender)

    def is_loaded(self, language: str, gender: str | None = None) -> bool:
        if voice_name(language, gender or self.gender, self.personality):
            return language.split("-")[0].lower() in self._ready
        return self.local.is_loaded(language, gender)

    def is_downloaded(self, language: str, gender: str | None = None) -> bool:
        if voice_name(language, gender or self.gender, self.personality):
            return True  # no se descarga nada
        return self.local.is_downloaded(language, gender)

    def prepare(self, language: str, gender: str | None = None) -> bool:
        if voice_name(language, gender or self.gender, self.personality):
            pool(self.timeout).warm()  # deja la conexión abierta: la primera frase sale enseguida (no gasta)
            self._ready.add(language.split("-")[0].lower())
            return True
        return self.local.prepare(language, gender)

    def synthesize(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
                   style: str = "") -> Speech | None:
        name = voice_name(language, gender or self.gender, self.personality)
        if name is None or not text.strip():
            return self.local.synthesize(text, language, gender, speed, style)
        if text.strip().lower() == "ok":
            self.prepare(language, gender)  # la "práctica" de las voces de tu PC: acá alcanza con conectarse
            return None
        params, gain, key = self._request(name, language, speed, style, text)
        code = language.split("-")[0].lower()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
        if cached is not None:
            return self._speech(cached, gain)
        try:
            raw = pool(self.timeout).request(
                "POST", f"/v1/speak?{urllib.parse.urlencode(params)}",
                json.dumps({"text": text.strip()[:MAX_CHARS]}).encode("utf-8"),
                {"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        except CloudError as exc:
            self.on_fail(exc)
            return self.local.synthesize(text, language, gender, speed, style)
        self._ready.add(code)
        try:
            pro.count_characters(len(text.strip()))
        except Exception:  # noqa: BLE001 - es solo la cuenta
            log.debug("No se pudo anotar el uso de la voz de la nube", exc_info=True)
        samples = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").copy()
        if not len(samples):
            return None
        with self._cache_lock:
            self._cache[key] = samples
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
        return self._speech(samples, gain)

    def stream(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
               style: str = ""):
        """La voz de a pedazos, apenas llegan (empieza a sonar ~0,5 s antes que esperando la frase entera). Devuelve
        (frecuencia, pedazos) o None si la nube no tiene voz en ese idioma (entonces se usa `synthesize`)."""
        name = voice_name(language, gender or self.gender, self.personality)
        if name is None or not text.strip() or text.strip().lower() == "ok":
            return None
        params, gain, key = self._request(name, language, speed, style, text)
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None:
            return SAMPLE_RATE, iter([self._speech(cached, gain).audio])
        try:
            pieces = pool(self.timeout).stream(
                "POST", f"/v1/speak?{urllib.parse.urlencode(params)}",
                json.dumps({"text": text.strip()[:MAX_CHARS]}).encode("utf-8"),
                {"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        except CloudError as exc:
            self.on_fail(exc)
            return None
        return SAMPLE_RATE, self._decode(pieces, key, gain, language, len(text.strip()))

    def _decode(self, pieces, key, gain: float, language: str, chars: int):
        parts, leftover = [], b""
        gain_now = 1.0 if gain == 1.0 else min(gain, 1.6)
        for data in pieces:
            data = leftover + data
            cut = len(data) // 2 * 2
            leftover = data[cut:]
            if not cut:
                continue
            samples = np.frombuffer(data[:cut], dtype="<i2").copy()
            parts.append(samples)
            audio = samples.astype(np.float32) / 32768.0
            yield np.clip(audio * gain_now, -0.98, 0.98) if gain_now != 1.0 else audio
        if parts:
            self._ready.add(language.split("-")[0].lower())
            with self._cache_lock:
                self._cache[key] = np.concatenate(parts)
                while len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
            try:
                pro.count_characters(chars)
            except Exception:  # noqa: BLE001 - es solo la cuenta
                log.debug("No se pudo anotar el uso de la voz de la nube", exc_info=True)

    def _request(self, name: str, language: str, speed: float | None, style: str, text: str):
        marks = set(style.split("+")) if style else set()
        mood = next((mark for mark in ("shout", "exclaim", "soft") if mark in marks), "")
        pace, _expressive, gain = STYLES[mood]
        params = {"model": name, "encoding": "linear16", "sample_rate": SAMPLE_RATE, "container": "none"}
        code = language.split("-")[0].lower()
        if code in SPEED_LANGUAGES:
            low = 0.9 if code == "es" else 0.7  # en español, más lento que 0,9 se traba
            params["speed"] = round(min(1.5, max(low, (speed or self.speed) * pace)), 2)
        return params, gain, (name, params.get("speed"), text.strip())

    @staticmethod
    def _speech(samples: np.ndarray, gain: float) -> Speech:
        audio = samples.astype(np.float32) / 32768.0
        if gain != 1.0:
            peak = float(np.max(np.abs(audio))) or 1.0
            audio = audio * min(gain, 0.98 / peak)  # gritando más fuerte, bajito más suave (sin saturar)
        return Speech(audio, SAMPLE_RATE)
