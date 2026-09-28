"""Qué voces del juego vale la pena entender (en Basic y en Pro):

- **Radio de escucha:** en el chat de voz de Roblox, los que están lejos suenan más bajo. Se toma como referencia cómo
  suenan las voces cercanas (las más fuertes de los últimos minutos) y se dejan pasar solo las que no suenan mucho más
  bajo que eso. Así no se subtitulan murmullos de la otra punta del mapa.
- **Filtro de ruido:** lo que no es alguien hablando un idioma (música, explosiones, risas, balbuceos, un idioma que
  no se entiende) no se traduce: antes salían subtítulos inventados a partir del ruido.
"""

from __future__ import annotations

import re
import time
from typing import Callable

import numpy as np

# Cuánto más bajo que las voces cercanas puede sonar una voz para que se entienda (dB). "todo": sin límite.
# Medido con grabaciones de Roblox: entre jugadores que están al lado, los micrófonos varían hasta ~15 dB.
RADIUS_DB = {"cerca": 12.0, "normal": 20.0, "lejos": 28.0, "todo": None}
RADIUS_NAMES = {"cerca": "Cerca", "normal": "Normal", "lejos": "Lejos", "todo": "Todas"}
FLOOR_DB = -55.0  # más bajo que esto no es una voz que se pueda entender (en ningún radio)
FRAME = 512  # 32 ms a 16 kHz


def speech_level(audio: np.ndarray) -> float:
    """Qué tan fuerte suena una voz (dBFS): la mitad más fuerte de sus tramos de 32 ms, así no cuentan las pausas."""
    audio = np.asarray(audio, dtype=np.float32).ravel()
    count = len(audio) // FRAME
    if count == 0:
        return -120.0
    rms = np.sqrt(np.mean(audio[:count * FRAME].reshape(count, FRAME) ** 2, axis=1))
    loud = np.sort(rms)[count // 2:]
    return float(20 * np.log10(np.sqrt(np.mean(loud ** 2)) + 1e-9))


class Earshot:
    """El radio de escucha. La referencia (cómo suenan las voces cercanas) sube con las voces fuertes (a medias: un
    grito no la dispara) y baja de a poco si nadie habla cerca (te alejaste, bajaste el volumen del juego)."""

    DECAY_DB_PER_S = 0.05  # 3 dB por minuto
    RISE = 0.5

    def __init__(self, radius: str = "normal", clock: Callable[[], float] = time.monotonic) -> None:
        self.radius = radius if radius in RADIUS_DB else "normal"
        self._clock = clock
        self._reference: float | None = None
        self._when = 0.0

    def reference(self) -> float | None:
        if self._reference is None:
            return None
        return self._reference - self.DECAY_DB_PER_S * (self._clock() - self._when)

    def hears(self, level: float) -> bool:
        """¿Una voz que suena así está dentro del radio?"""
        if level < FLOOR_DB:
            return False
        drop = RADIUS_DB.get(self.radius)
        reference = self.reference()
        return drop is None or reference is None or level >= reference - drop

    def learn(self, level: float) -> None:
        """Se escuchó una voz que suena así."""
        if level < FLOOR_DB:
            return
        reference = self.reference()
        if reference is None:
            self._reference = level
        elif level > reference:
            self._reference = reference + self.RISE * (level - reference)
        else:
            self._reference = reference
        self._when = self._clock()


_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def looks_like_noise(text: str, no_speech: float, logprob: float, language_prob: float,
                     speech_ratio: float = 1.0) -> bool:
    """Whisper (Basic): ¿esto salió de un ruido y no de alguien hablando? `speech_ratio`: qué parte del audio el
    detector de voz marcó como voz. Solo descarta lo seguro: en una pelea real (música, golpes, voces pisadas)
    Whisper marca voces verdaderas como "probablemente no es voz" (0,6–0,8) y las entiende con poca seguridad
    (hasta −1,9 en un "No, no"): esas pasan (medido con grabaciones de Roblox)."""
    if not _LETTER.search(text or ""):
        return True
    if speech_ratio < 0.25:
        return True  # casi todo el audio era otra cosa
    if logprob < -2.2:
        return True  # no se entendió nada (balbuceo, un idioma que no es)
    if no_speech > 0.9 and logprob < -1.0:
        return True  # casi seguro que no había nadie hablando
    return language_prob < 0.3 and logprob < -1.2  # ni siquiera se sabe qué idioma es


def cloud_noise(text: str, confidences: list[float]) -> bool:
    """La nube (Pro): lo mismo con la seguridad de cada palabra."""
    if not _LETTER.search(text or "") or not confidences:
        return True
    mean = float(np.mean(confidences))
    if len(confidences) == 1:
        return mean < 0.55  # una palabra suelta tiene que estar bastante clara
    return mean < 0.4
