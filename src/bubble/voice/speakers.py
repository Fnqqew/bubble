"""Quién habla: cada voz se resume en una "huella" (embedding) y las parecidas se agrupan como la misma persona.

No sabe nombres: numera las voces en el orden en que aparecen ("Voz 1", "Voz 2"…) y las reconoce cuando vuelven a
hablar. El modelo (CAM++, entrenado con miles de voces de VoxCeleb) es chico (~30 MB), corre en la CPU y tarda
unos milisegundos por frase.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .models import Progress, download, models_dir

SAMPLE_RATE = 16000
MODEL_NAME = "3dspeaker_speech_campplus_sv_en_voxceleb_16k.onnx"
MODEL_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/{MODEL_NAME}"
MIN_SECONDS = 0.7  # con menos audio la huella no es confiable


# ---------------------------------------------------------------- espectro (igual que Kaldi, que es lo que espera el modelo)
def _mel(freq):
    return 1127.0 * np.log(1.0 + np.asarray(freq, dtype=np.float64) / 700.0)


def _mel_banks(bins: int = 80, fft: int = 512, rate: int = SAMPLE_RATE, low: float = 20.0) -> np.ndarray:
    high = rate / 2
    low_mel, high_mel = _mel(low), _mel(high)
    step = (high_mel - low_mel) / (bins + 1)
    freqs = np.arange(fft // 2) * rate / fft
    mels = _mel(freqs)
    banks = np.zeros((bins, fft // 2 + 1), dtype=np.float32)
    for index in range(bins):
        left, center, right = low_mel + index * step, low_mel + (index + 1) * step, low_mel + (index + 2) * step
        up = (mels - left) / (center - left)
        down = (right - mels) / (right - center)
        banks[index, : fft // 2] = np.maximum(0.0, np.minimum(up, down))
    return banks


_BANKS = _mel_banks()
_WINDOW = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(400) / 399)) ** 0.85  # ventana "povey"


def fbank(audio: np.ndarray) -> np.ndarray:
    """80 bandas de energía (log) cada 10 ms, sobre ventanas de 25 ms. Devuelve (cuadros, 80)."""
    audio = np.asarray(audio, dtype=np.float32) * 32768.0  # el modelo se entrenó con muestras de 16 bits
    if len(audio) < 400:
        return np.zeros((0, 80), dtype=np.float32)
    count = 1 + (len(audio) - 400) // 160
    index = np.arange(400)[None, :] + 160 * np.arange(count)[:, None]
    frames = audio[index].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    frames[:, 1:] -= 0.97 * frames[:, :-1]
    frames[:, 0] *= 1 - 0.97
    frames *= _WINDOW
    power = np.abs(np.fft.rfft(frames, n=512)) ** 2
    return np.log(np.maximum(power @ _BANKS.T, np.finfo(np.float32).eps)).astype(np.float32)


# ---------------------------------------------------------------- huellas y agrupamiento
@dataclass
class _Voice:
    number: int
    centroid: np.ndarray
    count: int = 1
    samples: list[np.ndarray] = field(default_factory=list)


class SpeakerTracker:
    """Asigna un número a cada voz. `same` y `maybe` son umbrales de parecido (coseno) que se ajustaron con el
    laboratorio de voz (tools/voice_lab.py)."""

    def __init__(self, model_path: Path | None = None, same: float = 0.6, maybe: float = 0.5,
                 progress: Progress | None = None) -> None:
        import onnxruntime

        path = model_path or download(MODEL_URL, models_dir() / "speaker" / MODEL_NAME, "reconocimiento de voces",
                                      progress)
        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self._session = onnxruntime.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self.same = same
        self.maybe = maybe
        self.voices: list[_Voice] = []
        self._lock = threading.Lock()

    def embed(self, audio: np.ndarray) -> np.ndarray | None:
        feats = fbank(audio)
        if len(feats) < MIN_SECONDS * 100:
            return None
        feats -= feats.mean(axis=0, keepdims=True)
        vector = self._session.run(None, {self._input: feats[None]})[0][0]
        return vector / (np.linalg.norm(vector) + 1e-9)

    def peek(self, audio: np.ndarray) -> int:
        """Si es una voz ya conocida (bastante parecida), su número; si no, 0. No crea voces ni las modifica: sirve
        para mostrar quién habla mientras todavía está hablando."""
        vector = self.embed(audio)
        if vector is None:
            return 0
        with self._lock:
            scored = [(float(voice.centroid @ vector), voice.number) for voice in self.voices]
        score, number = max(scored, default=(-1.0, 0))
        return number if score >= self.same else 0

    def identify(self, audio: np.ndarray, hint: int = 0) -> int:
        """Número de la voz (1, 2…). 0 si el audio es muy corto para saberlo. `hint`: la voz que se supone (la de la
        frase anterior sin pausa): se prefiere si se parece lo suficiente."""
        vector = self.embed(audio)
        if vector is None:
            return hint
        mixed = False
        if len(audio) >= 2.4 * SAMPLE_RATE:
            # ¿Hablaron dos personas en la misma frase (se pisaron)? Si las dos mitades no se parecen, la huella
            # está mezclada: se usa la de la primera mitad y no se crea una voz nueva con eso.
            half = len(audio) // 2
            first, second = self.embed(audio[:half]), self.embed(audio[half:])
            if first is not None and second is not None and float(first @ second) < 0.4:
                vector, mixed = first, True
        with self._lock:
            scores = [(float(voice.centroid @ vector), voice) for voice in self.voices]
            best_score, best = max(scores, key=lambda item: item[0], default=(-1.0, None))
            hinted = next((item for item in scores if item[1].number == hint), None)
            if hinted and hinted[0] >= self.maybe and hinted[0] >= best_score - 0.05:
                best_score, best = hinted
            if mixed and best is not None and best_score >= self.maybe - 0.1:
                return best.number
            if best is None or best_score < self.maybe or (best_score < self.same and best.count >= 3):
                voice = _Voice(len(self.voices) + 1, vector)
                self.voices.append(voice)
                return voice.number
            # La huella de cada voz se va afinando con cada frase (promedio que pesa más lo reciente).
            weight = 1.0 / min(best.count + 1, 8)
            centroid = best.centroid * (1 - weight) + vector * weight
            best.centroid = centroid / (np.linalg.norm(centroid) + 1e-9)
            best.count += 1
            return best.number

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float | None:
        ea, eb = self.embed(a), self.embed(b)
        return None if ea is None or eb is None else float(ea @ eb)
