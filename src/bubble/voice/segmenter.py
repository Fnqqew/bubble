"""Corta un audio continuo en frases: empieza cuando alguien habla y termina después de un silencio corto.

Se compara la energía de cada tramo de 30 ms con el ruido de fondo, que se va aprendiendo (la música del juego, el
viento…): una frase es algo claramente más fuerte que ese fondo. Lo que no era voz de verdad lo descarta después
Whisper.
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


class SpeechSegmenter:
    def __init__(self, rate: int = SAMPLE_RATE, frame_ms: int = 30, start_ms: int = 150, end_silence_ms: int = 650,
                 max_seconds: float = 12.0, min_seconds: float = 0.5, pre_roll_ms: int = 300) -> None:
        self.rate = rate
        self.frame = rate * frame_ms // 1000
        self.start_frames = max(1, start_ms // frame_ms)
        self.end_frames = max(1, end_silence_ms // frame_ms)
        self.max_samples = int(max_seconds * rate)
        self.min_samples = int(min_seconds * rate)
        self.pre_roll = max(1, pre_roll_ms // frame_ms)
        self.floor = 0.003  # energía del ruido de fondo (RMS), se adapta
        self._pending = np.zeros(0, dtype=np.float32)
        self._recent: list[np.ndarray] = []  # lo último antes de que empiece la frase (para no cortar el comienzo)
        self._speech: list[np.ndarray] = []
        self._loud = 0
        self._quiet = 0
        self._energies: list[float] = []  # los últimos ~600 ms
        self.active = False

    def _steady(self) -> bool:
        """Sonido parejo (música, viento, un motor): la voz sube y baja muchas veces por segundo (las sílabas)."""
        if len(self._energies) < 15:
            return False
        values = np.array(self._energies)
        return float(values.std()) < 0.2 * float(values.mean())

    def threshold(self) -> float:
        return max(0.008, self.floor * 3.0)

    def feed(self, samples: np.ndarray) -> list[np.ndarray]:
        """Agrega audio (mono, float32). Devuelve las frases que terminaron."""
        done: list[np.ndarray] = []
        data = np.concatenate([self._pending, samples.astype(np.float32).ravel()])
        whole = len(data) // self.frame * self.frame
        self._pending = data[whole:]
        for start in range(0, whole, self.frame):
            frame = data[start:start + self.frame]
            energy = float(np.sqrt(np.mean(frame * frame)))
            self._energies = (self._energies + [energy])[-20:]
            steady = self._steady()
            # Los primeros ~450 ms solo se escucha el fondo: todavía no se puede saber si un sonido es parejo.
            loud = energy > self.threshold() and not steady and len(self._energies) >= 15
            if not self.active:
                self._recent = (self._recent + [frame])[-self.pre_roll:]
                self._loud = self._loud + 1 if loud else 0
                if not loud:
                    # Solo se aprende el fondo cuando no habla nadie (sube despacio, baja rápido; un sonido parejo
                    # como la música del juego pasa a ser parte del fondo).
                    rate = (0.05 if steady else 0.02) if energy > self.floor else 0.2
                    self.floor += (energy - self.floor) * rate
                if self._loud >= self.start_frames:
                    self.active, self._speech, self._quiet = True, list(self._recent), 0
                continue
            self._speech.append(frame)
            self._quiet = 0 if loud else self._quiet + 1
            length = len(self._speech) * self.frame
            if self._quiet >= self.end_frames or length >= self.max_samples:
                phrase = np.concatenate(self._speech)
                if self._quiet >= self.end_frames:
                    phrase = phrase[: len(phrase) - (self._quiet - 3) * self.frame]  # sin el silencio del final
                if len(phrase) >= self.min_samples:
                    done.append(phrase)
                self.active, self._speech, self._recent, self._loud = False, [], [], 0
        return done
