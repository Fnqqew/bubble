"""Qué voces del juego vale la pena entender (en Basic y en Pro):

- **Radio de escucha:** en el chat de voz de Roblox, las voces lejanas suenan más bajo. Como referencia se toma el nivel
  de las voces cercanas (las más fuertes de los últimos minutos) y solo se dejan pasar las que no suenan mucho más bajo
  que ese nivel. Así no se subtitulan murmullos del otro extremo del mapa.
- **Filtro de ruido:** lo que no es una persona hablando un idioma (música, explosiones, risas, balbuceos, un idioma que
  no se reconoce) no se traduce, para evitar subtítulos inventados a partir del ruido.
"""

from __future__ import annotations

import re
import time
from typing import Callable

import numpy as np

# Cuánto más bajo que las voces cercanas (dB) puede sonar una voz para considerarse audible. "todo": sin límite. Entre
# jugadores cercanos, los micrófonos varían hasta ~15 dB (medido con grabaciones de Roblox).
RADIUS_DB = {"cerca": 12.0, "normal": 20.0, "lejos": 28.0, "todo": None}
RADIUS_NAMES = {"cerca": "Cerca", "normal": "Normal", "lejos": "Lejos", "todo": "Todas"}
FLOOR_DB = -55.0  # por debajo de este nivel no hay voz comprensible (en ningún radio)
FRAME = 512  # 32 ms a 16 kHz


def speech_level(audio: np.ndarray) -> float:
    """Nivel de una voz (dBFS): se calcula sobre la mitad más fuerte de sus tramos de 32 ms, para que las pausas no
    cuenten.
    """
    audio = np.asarray(audio, dtype=np.float32).ravel()
    count = len(audio) // FRAME
    if count == 0:
        return -120.0
    rms = np.sqrt(np.mean(audio[:count * FRAME].reshape(count, FRAME) ** 2, axis=1))
    loud = np.sort(rms)[count // 2:]
    return float(20 * np.log10(np.sqrt(np.mean(loud ** 2)) + 1e-9))


class Earshot:
    """Radio de escucha. La referencia (nivel de las voces cercanas) sube con las voces fuertes, solo parcialmente para
    que un grito no la dispare, y baja gradualmente si nadie habla cerca (el jugador se alejó o bajó el volumen del
    juego).
    """

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
        """Indica si una voz con este nivel está dentro del radio."""
        if level < FLOOR_DB:
            return False
        drop = RADIUS_DB.get(self.radius)
        reference = self.reference()
        return drop is None or reference is None or level >= reference - drop

    def learn(self, level: float) -> None:
        """Registra una voz escuchada con este nivel."""
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
    """Whisper (Basic): indica si el audio proviene de ruido y no de una persona hablando. `speech_ratio`: proporción
    del audio que el detector de voz marcó como voz. Solo descarta los casos seguros: en situaciones reales con
    ruido (música, golpes, voces superpuestas) Whisper marca voces verdaderas como "probablemente no es voz"
    (0,6–0,8) y las reconoce con poca seguridad (hasta −1,9 en un "No, no"); esas pasan (medido con grabaciones de
    Roblox).
    """
    if not _LETTER.search(text or ""):
        return True
    if speech_ratio < 0.25:
        return True  # casi todo el audio no era voz
    if logprob < -2.2:
        return True  # no se entendió nada (balbuceo o idioma no reconocido)
    if no_speech > 0.9 and logprob < -1.0:
        return True  # casi seguro que nadie hablaba
    return language_prob < 0.3 and logprob < -1.2  # ni siquiera se identifica el idioma


def cloud_noise(text: str, confidences: list[float]) -> bool:
    """La nube (Pro): el mismo criterio, según la confianza de cada palabra."""
    if not _LETTER.search(text or "") or not confidences:
        return True
    mean = float(np.mean(confidences))
    if len(confidences) == 1:
        return mean < 0.55  # una palabra suelta debe tener bastante confianza
    return mean < 0.4
