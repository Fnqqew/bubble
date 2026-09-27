"""Voz a texto local: Whisper (faster-whisper) en la CPU, así funciona con cualquier placa de video.

El modelo se elige según la PC: en un procesador de 12 hilos, "base" transcribe 5 s de voz en ~1,9 s; "small" es más
preciso pero tarda más que el propio audio (~5,4 s), así que queda para procesadores más potentes.
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass

import numpy as np

from .models import Progress, models_dir

SAMPLE_RATE = 16000
# Frases que Whisper "inventa" con ruido, música o silencio (aprendidas de videos subtitulados).
_HALLUCINATIONS = re.compile(
    r"^(thank you( so much)?( for watching)?|thanks for watching|subscribe|please subscribe|"
    r"gracias por ver( el video)?|subt[ií]tulos (realizados )?por la comunidad de amara\.org|"
    r"obrigad[oa] por assistir|legendas pela comunidade amara\.org|sous-titres r[ée]alis[ée]s par.*|"
    r"you|bye|\.+|♪+|music)\W*$",
    re.IGNORECASE,
)


@dataclass
class Transcript:
    text: str
    language: str  # código ("en", "pt"...)
    probability: float  # qué tan seguro está del idioma
    seconds: float  # duración del audio


def pick_model(threads: int | None = None) -> str:
    threads = threads or os.cpu_count() or 4
    return "small" if threads >= 16 else "base"


def is_hallucination(text: str) -> bool:
    return bool(_HALLUCINATIONS.match(text.strip()))


class Transcriber:
    def __init__(self, model: str = "auto", progress: Progress | None = None) -> None:
        self.model_name = pick_model() if model in ("", "auto") else model
        self.progress = progress
        self._model = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        """Carga el modelo (la primera vez lo descarga: ~145 MB "base", ~480 MB "small")."""
        with self._lock:
            if self._model is None:
                from faster_whisper import WhisperModel

                if self.progress:
                    self.progress(f"reconocimiento de voz ({self.model_name})", 0.0)
                threads = max(2, min(8, (os.cpu_count() or 4) // 2))
                self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8", cpu_threads=threads,
                                           download_root=str(models_dir() / "whisper"))
                if self.progress:
                    self.progress(f"reconocimiento de voz ({self.model_name})", 1.0)

    def transcribe(self, audio: np.ndarray, language: str | None = None) -> Transcript | None:
        """`audio`: mono, float32, 16 kHz. `language`: si ya se sabe (tu voz), se salta la detección. None si no
        había voz de verdad (ruido, música, o una frase inventada por el modelo)."""
        self.load()
        with self._lock:
            segments, info = self._model.transcribe(
                audio, language=language.split("-")[0] if language else None, beam_size=1, vad_filter=True,
                without_timestamps=True, condition_on_previous_text=False,
            )
            kept = [s for s in segments if s.avg_logprob > -1.2 and not (s.no_speech_prob > 0.6 and s.avg_logprob < -0.8)]
        text = " ".join(s.text.strip() for s in kept).strip()
        if not text or is_hallucination(text):
            return None
        return Transcript(text, info.language, info.language_probability, len(audio) / SAMPLE_RATE)
