"""Las voces de Bubble Pro: Deepgram Aura-2, voces naturales y con personalidad (alegre, canchera o tranquila), para tu
voz traducida y el chat a voz. Misma cara que las voces de tu PC (voice/tts.py: `Voices`), así el resto no cambia.

Idiomas con voz en la nube: inglés (de EE. UU., Reino Unido, Australia y Filipinas), español (de Argentina, México,
Colombia, España y neutro), francés, alemán, italiano, neerlandés y japonés: todas las voces que tiene Deepgram, con
mujer y hombre en cada idioma. Los demás (portugués, ruso…) y cualquier falla: la voz de tu PC.

Cada frase pasa por voice/prosody.py: la nube varía sola el tono y el volumen de una frase a otra (medido: de 100 a
250 Hz y hasta 10 dB con la misma voz); ahí se emparejan y se les pone tu expresión.
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
from ..voice.prosody import POLISH, Streaming
from ..voice.tts import STYLES, Speech
from .connection import pool
from .errors import CloudError

log = logging.getLogger(__name__)
SAMPLE_RATE = 24000
PERSONALITIES = {"alegre": "Alegre", "canchera": "Canchera", "tranquila": "Tranquila"}
# idioma → (femenina, masculina) por personalidad, según cómo las describe Deepgram.
VOICES = {
    "en": {"alegre": ("thalia", "aries"),  # clara, enérgica, entusiasta
           "canchera": ("andromeda", "apollo"),  # casual, expresiva / seguro, casual
           "tranquila": ("helena", "arcas")},  # cálida, natural / suave, natural
    "es": {"alegre": ("celeste", "luciano"),  # enérgica, entusiasta / carismático, alegre
           "canchera": ("selena", "aquila"),  # latinoamericanos, casuales (pronuncian bien las palabras en inglés)
           "tranquila": ("estrella", "sirio")},  # calma, natural / calmo, barítono
    "fr": {"alegre": ("agathe", "hector"), "canchera": ("agathe", "hector"), "tranquila": ("agathe", "hector")},
    "de": {"alegre": ("viktoria", "julius"),  # carismática, alegre / casual, alegre
           "canchera": ("aurelia", "julius"),  # casual, cercana
           "tranquila": ("elara", "fabian")},  # calma, paciente / segura, natural
    "it": {"alegre": ("livia", "dionisio"),  # alegre, expresiva / positivo, melódico
           "canchera": ("melia", "dionisio"),  # cercana, natural
           "tranquila": ("demetra", "elio")},  # calma, paciente / calmo, suave
    "nl": {"alegre": ("beatrix", "lars"),  # alegre, entusiasta / casual
           "canchera": ("hestia", "lars"),  # expresiva, cercana
           "tranquila": ("daphne", "roman")},  # calma, clara / calmo, grave
    "ja": {"alegre": ("uzume", "ebisu"),  # joven, clara / joven, natural
           "canchera": ("ama", "ebisu"),  # casual, segura
           "tranquila": ("izanami", "fujin")},  # clara, cordial / calmo, seguro
}
# Con la región del que te escucha, las voces de esa zona (las que hay): {personalidad: (femenina, masculina)}. "*":
# todas las personalidades. None: esa voz no está en la zona (queda la de VOICES).
REGIONAL = {
    ("es", "AR"): {"*": ("antonia", "aquila")},  # argentina (Deepgram no tiene un hombre argentino: latinoamericano)
    ("es", "UY"): {"*": ("antonia", "aquila")},
    ("es", "MX"): {"alegre": ("olivia", "luciano"), "canchera": ("olivia", "javier"), "tranquila": ("estrella", "sirio")},
    ("es", "ES"): {"alegre": ("silvia", "alvaro"), "canchera": ("carina", "alvaro"), "tranquila": ("agustina", "nestor")},
    ("es", "CO"): {"alegre": ("celeste", None), "canchera": ("gloria", None), "tranquila": ("gloria", None)},
    ("en", "GB"): {"*": ("pandora", "draco")},
    ("en", "AU"): {"*": ("theia", "hyperion")},
    ("en", "PH"): {"*": ("amalthea", None)},
}
# El tono de siempre de cada voz (Hz, la mediana de 4 frases tranquilas, medido el 30/9/2026): la referencia con la
# que se emparejan sus frases desde la primera (ver voice/prosody.py). Algunas varían poco (Agustina: 186 a 189 Hz);
# otras, mucho (Beatrix: 212 a 288; Apollo, entusiasmado, de 108 a 241).
TYPICAL_PITCH = {
    "agathe-fr": 215, "agustina-es": 187, "alvaro-es": 120, "ama-ja": 217, "amalthea-en": 212, "andromeda-en": 182,
    "antonia-es": 231, "apollo-en": 141, "aquila-es": 147, "arcas-en": 127, "aries-en": 117, "aurelia-de": 200,
    "beatrix-nl": 233, "carina-es": 203, "celeste-es": 222, "daphne-nl": 165, "demetra-it": 204, "dionisio-it": 133,
    "draco-en": 103, "ebisu-ja": 113, "elara-de": 206, "elio-it": 116, "estrella-es": 191, "fabian-de": 147,
    "fujin-ja": 151, "gloria-es": 241, "hector-fr": 178, "helena-en": 213, "hestia-nl": 177, "hyperion-en": 93,
    "izanami-ja": 171, "javier-es": 102, "julius-de": 122, "lars-nl": 101, "livia-it": 179, "luciano-es": 121,
    "melia-it": 213, "nestor-es": 122, "olivia-es": 169, "pandora-en": 185, "roman-nl": 122, "selena-es": 224,
    "silvia-es": 205, "sirio-es": 106, "thalia-en": 213, "theia-en": 181, "uzume-ja": 197, "viktoria-de": 222,
}
POLISH.seed({f"aura-2-{name}": hz for name, hz in TYPICAL_PITCH.items()})
SPEED_LANGUAGES = {"en", "es"}  # Deepgram deja cambiar la velocidad solo en estos
MAX_CHARS = 2000
FADE_S = 0.07  # si la voz termina de golpe, se baja suave en este final
CACHE_SIZE = 120  # frases dichas que se guardan (en memoria): repetir "gg" o "gracias" no se vuelve a pagar


def speakable(text: str, style: str = "") -> str:
    """Con signo al final: la voz de la nube corta el final de las palabras sueltas sin puntuación ("nice", "gg"). El
    signo es el de cómo lo dijiste: "?" si preguntaste, "!" si exclamaste o gritaste, "." si no (antes, toda palabra
    suelta llevaba "!" y sonaba exaltada aunque la dijeras tranquilo)."""
    text = " ".join(text.split())
    marks = set(style.split("+")) if style else set()
    if text and text[-1] not in ".!?…。！？\"'»”)":
        text += "?" if "question" in marks else "!" if marks & {"shout", "exclaim"} else "."
    return text


def soften_end(audio: np.ndarray) -> np.ndarray:
    """Si la voz termina todavía sonando (la nube a veces corta la última sílaba), se baja suave en los últimos
    FADE_S en vez del corte seco (que suena a palabra sin terminar)."""
    tail = int(SAMPLE_RATE * FADE_S)
    if len(audio) < tail * 2:
        return audio
    last = audio[-int(SAMPLE_RATE * 0.02):]
    if float(np.sqrt(np.mean(last ** 2))) < 0.01:  # (−40 dB: ya terminaba en silencio)
        return audio
    audio = audio.copy()
    audio[-tail:] *= np.cos(np.linspace(0, np.pi / 2, tail)) ** 2
    return audio


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
    local = REGIONAL.get((code, region), {})
    regional = local.get(personality) or local.get("*")
    if regional:
        female_name, male_name = regional[0] or female_name, regional[1] or male_name
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
        text = speakable(text, style)
        params, key = self._request(name, language, speed, style, text)
        code = language.split("-")[0].lower()
        with self._cache_lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
        if cached is not None:
            return self._speech(cached, name, style)
        try:
            raw = pool(self.timeout).request(
                "POST", f"/v1/speak?{urllib.parse.urlencode(params)}",
                json.dumps({"text": text.strip()[:MAX_CHARS]}).encode("utf-8"),
                {"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        except CloudError as exc:
            log.warning("La voz de la nube no respondió (%s): esta frase va con la voz de tu PC", exc)
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
        return self._speech(samples, name, style)

    def stream(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
               style: str = ""):
        """La voz de a pedazos, apenas llegan (empieza a sonar ~0,5 s antes que esperando la frase entera). Devuelve
        (frecuencia, pedazos) o None si la nube no tiene voz en ese idioma (entonces se usa `synthesize`)."""
        name = voice_name(language, gender or self.gender, self.personality)
        if name is None or not text.strip() or text.strip().lower() == "ok":
            return None
        text = speakable(text, style)
        params, key = self._request(name, language, speed, style, text)
        with self._cache_lock:
            cached = self._cache.get(key)
        if cached is not None:
            return SAMPLE_RATE, iter([self._speech(cached, name, style).audio])
        try:
            pieces = pool(self.timeout).stream(
                "POST", f"/v1/speak?{urllib.parse.urlencode(params)}",
                json.dumps({"text": text.strip()[:MAX_CHARS]}).encode("utf-8"),
                {"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        except CloudError as exc:
            log.warning("La voz de la nube no respondió (%s)", exc)
            self.on_fail(exc)
            return None
        return SAMPLE_RATE, self._decode(pieces, key, name, style, language, len(text.strip()))

    def _decode(self, pieces, key, name: str, style: str, language: str, chars: int):
        parts, leftover = [], b""
        shaper = Streaming(POLISH, SAMPLE_RATE, name, style)  # mismo tono y volumen que las otras frases
        hold = int(SAMPLE_RATE * FADE_S) * 2  # el final se retiene un momento: si termina de golpe, se suaviza
        held = np.zeros(0, dtype=np.float32)
        for data in pieces:
            data = leftover + data
            cut = len(data) // 2 * 2
            leftover = data[cut:]
            if not cut:
                continue
            samples = np.frombuffer(data[:cut], dtype="<i2").copy()
            parts.append(samples)
            held = np.concatenate([held, samples.astype(np.float32) / 32768.0])
            if len(held) > hold:
                ready, held = held[:-hold], held[-hold:]
                yield from shaper.feed(ready)
        if len(held):
            yield from shaper.feed(soften_end(held))
        yield from shaper.finish()
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
        pace, _expressive = STYLES[mood]
        params = {"model": name, "encoding": "linear16", "sample_rate": SAMPLE_RATE, "container": "none"}
        code = language.split("-")[0].lower()
        if code in SPEED_LANGUAGES:
            low = 0.9 if code == "es" else 0.7  # en español, más lento que 0,9 se traba
            params["speed"] = round(min(1.5, max(low, (speed or self.speed) * pace)), 2)
        return params, (name, params.get("speed"), text.strip())

    @staticmethod
    def _speech(samples: np.ndarray, name: str, style: str) -> Speech:
        audio = soften_end(samples.astype(np.float32) / 32768.0)
        return Speech(POLISH.apply(audio, SAMPLE_RATE, name, style), SAMPLE_RATE)
