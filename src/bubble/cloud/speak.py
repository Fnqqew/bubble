"""Las voces de Bubble Pro: Deepgram Aura-2, voces naturales y con personalidad (alegre, canchera o tranquila), para tu
voz traducida y el chat a voz. Misma cara que las voces de tu PC (voice/tts.py: `Voices`), así el resto no cambia.

En inglés hablan las voces Flux (Deepgram, septiembre de 2026): más parejas de una frase a otra y con expresividad
(de calma a animada), que se ajusta a cómo dijiste las cosas; cuestan 0,045 US$ cada 1.000 letras en vez de 0,030.
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
from .errors import BadKey, CloudError, NoCredit

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
# Flux, en inglés: (femenina, masculina) por personalidad y, por acento, las de esa zona.
FLUX_VOICES = {"alegre": ("heather", "wade"),  # atrapante, enérgica / entusiasta
               "canchera": ("brooke", "cole"),  # amigable, rápida / amigable, enérgico
               "tranquila": ("hannah", "miles")}  # clara, tranquila / calmo, sincero
FLUX_REGIONAL = {"GB": ("gemma", "kit"), "AU": ("sharon", None), "IE": ("maeve", None), "IN": ("meena", "naveen"),
                 "SG": (None, "kai"), "PH": (None, "marcelo")}
FLUX_PITCH = {"brooke": 231, "cole": 135, "gemma": 221, "hannah": 234, "heather": 246, "kai": 112, "kit": 119,
              "maeve": 252, "marcelo": 116, "meena": 227, "miles": 101, "naveen": 186, "sharon": 178, "wade": 124}
POLISH.seed({f"flux-{name}-en": hz for name, hz in FLUX_PITCH.items()})
# Cómo lo dijiste → expresividad de Flux (de -2, calma, a 2, animada).
EXPRESSIVITY = {"shout": 2, "exclaim": 1, "animated": 1, "soft": -2, "flat": -1}
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


def expressivity(style: str) -> int:
    marks = set(style.split("+")) if style else set()
    values = [EXPRESSIVITY[mark] for mark in marks if mark in EXPRESSIVITY]
    return max(values, key=abs) if values else 0


def is_flux(name: str) -> bool:
    return name.startswith("flux-")


def voice_name(language: str, gender: str = "femenina", personality: str = "canchera",
               flux: bool = True) -> str | None:
    """El modelo de Deepgram para ese idioma ("es-AR", "en"…), o None si la nube no tiene voz en ese idioma. En
    inglés, una voz Flux (con `flux=False`, la Aura de siempre)."""
    parts = language.replace("_", "-").split("-")
    code = parts[0].lower()
    region = parts[1].upper() if len(parts) > 1 else ""
    male = gender.startswith("m")
    if code == "en" and flux:
        female_name, male_name = FLUX_VOICES.get(personality, FLUX_VOICES["canchera"])
        local = FLUX_REGIONAL.get(region)
        if local:
            female_name, male_name = local[0] or female_name, local[1] or male_name
        return f"flux-{male_name if male else female_name}-en"
    options = VOICES.get(code)
    if options is None:
        return None
    female_name, male_name = options.get(personality, options["canchera"])
    local = REGIONAL.get((code, region), {})
    regional = local.get(personality) or local.get("*")
    if regional:
        female_name, male_name = regional[0] or female_name, regional[1] or male_name
    return f"aura-2-{male_name if male else female_name}-{code}"


def _path(name: str) -> str:
    return "/v2/speak" if is_flux(name) else "/v1/speak"


class CloudVoices:
    """Las voces de la nube con la cara de `Voices` (tts.py). `local`: las de tu PC, para lo que la nube no tiene."""

    def __init__(self, key: str, local, on_fail: Callable[[CloudError], None] = lambda _exc: None,
                 personality: str = "canchera", timeout: float = 6.0) -> None:
        self.key = key
        self.local = local
        self.on_fail = on_fail
        self.personality = personality if personality in PERSONALITIES else "canchera"
        self.flux = True  # en inglés, las voces Flux (si Deepgram no las acepta, las Aura de siempre)
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
        return voice_name(language, gender, self.personality, self.flux) or self.local.voice_for(language, gender)

    def is_loaded(self, language: str, gender: str | None = None) -> bool:
        if voice_name(language, gender or self.gender, self.personality, self.flux):
            return language.split("-")[0].lower() in self._ready
        return self.local.is_loaded(language, gender)

    def is_downloaded(self, language: str, gender: str | None = None) -> bool:
        if voice_name(language, gender or self.gender, self.personality, self.flux):
            return True  # no se descarga nada
        return self.local.is_downloaded(language, gender)

    def prepare(self, language: str, gender: str | None = None) -> bool:
        if voice_name(language, gender or self.gender, self.personality, self.flux):
            pool(self.timeout).warm()  # deja la conexión abierta: la primera frase sale enseguida (no gasta)
            self._ready.add(language.split("-")[0].lower())
            return True
        return self.local.prepare(language, gender)

    def synthesize(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
                   style: str = "") -> Speech | None:
        name = voice_name(language, gender or self.gender, self.personality, self.flux)
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
                "POST", f"{_path(name)}?{urllib.parse.urlencode(params)}",
                json.dumps({"text": text.strip()[:MAX_CHARS]}).encode("utf-8"),
                {"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        except CloudError as exc:
            if self._no_flux(name, exc):
                return self.synthesize(text, language, gender, speed, style)
            log.warning("La voz de la nube no respondió (%s): esta frase va con la voz de tu PC", exc)
            self.on_fail(exc)
            return self.local.synthesize(text, language, gender, speed, style)
        self._ready.add(code)
        try:
            pro.count_characters(len(text.strip()), flux=is_flux(name))
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
        name = voice_name(language, gender or self.gender, self.personality, self.flux)
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
                "POST", f"{_path(name)}?{urllib.parse.urlencode(params)}",
                json.dumps({"text": text.strip()[:MAX_CHARS]}).encode("utf-8"),
                {"Authorization": f"Token {self.key}", "Content-Type": "application/json"})
        except CloudError as exc:
            if self._no_flux(name, exc):
                return self.stream(text, language, gender, speed, style)
            log.warning("La voz de la nube no respondió (%s)", exc)
            self.on_fail(exc)
            return None
        return SAMPLE_RATE, self._decode(pieces, key, name, style, language, len(text.strip()))

    def _decode(self, pieces, key, name: str, style: str, language: str, chars: int):
        parts, leftover = [], b""
        # Mismo tono y volumen que las otras frases (salvo si Flux ya puso el tono según tu expresión).
        shaper = Streaming(POLISH, SAMPLE_RATE, name, style, tone=not (is_flux(name) and expressivity(style)))
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
                pro.count_characters(chars, flux=is_flux(name))
            except Exception:  # noqa: BLE001 - es solo la cuenta
                log.debug("No se pudo anotar el uso de la voz de la nube", exc_info=True)

    def _request(self, name: str, language: str, speed: float | None, style: str, text: str):
        marks = set(style.split("+")) if style else set()
        mood = next((mark for mark in ("shout", "exclaim", "soft") if mark in marks), "")
        pace, _expressive = STYLES[mood]
        params = {"model": name, "encoding": "linear16", "sample_rate": SAMPLE_RATE, "container": "none"}
        code = language.split("-")[0].lower()
        if is_flux(name):
            params["speed"] = round(min(1.5, max(0.5, (speed or self.speed) * pace)) * 20) / 20  # (de a 0,05)
            if expressivity(style):
                params["expressivity"] = expressivity(style)
        elif code in SPEED_LANGUAGES:
            low = 0.9 if code == "es" else 0.7  # en español, más lento que 0,9 se traba
            params["speed"] = round(min(1.5, max(low, (speed or self.speed) * pace)), 2)
        return params, (name, params.get("speed"), params.get("expressivity"), text.strip())

    def _no_flux(self, name: str, error: CloudError) -> bool:
        """Deepgram no aceptó la voz Flux (una cuenta sin acceso, un cambio en su servicio): desde ahora, en inglés, las
        Aura de siempre. True si hay que probar de nuevo."""
        if not is_flux(name) or isinstance(error, (BadKey, NoCredit)) or not self.flux:
            return False
        log.warning("Las voces Flux no andan (%s): en inglés, las Aura", error)
        self.flux = False
        return True

    @staticmethod
    def _speech(samples: np.ndarray, name: str, style: str) -> Speech:
        audio = soften_end(samples.astype(np.float32) / 32768.0)
        tone = not (is_flux(name) and expressivity(style))
        return Speech(POLISH.apply(audio, SAMPLE_RATE, name, style, tone=tone), SAMPLE_RATE)
