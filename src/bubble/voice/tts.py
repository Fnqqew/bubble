"""Voz sintética local (Piper): voces naturales que corren en tu PC, sin internet una vez descargadas.

La voz de cada idioma se elige sola del catálogo oficial de Piper (rhasspy/piper-voices) y se descarga la primera
vez que hace falta (~60 MB por idioma).
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Progress, download, models_dir

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
FILE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}"
QUALITY_ORDER = {"medium": 0, "high": 1, "low": 2, "x_low": 3}
# Variante preferida por idioma (la más neutra / más hablada entre jugadores).
PREFERRED_REGION = {"en": "en_US", "es": "es_MX", "pt": "pt_BR", "fr": "fr_FR", "de": "de_DE", "zh": "zh_CN",
                    "ar": "ar_JO", "hi": "hi_IN", "nl": "nl_NL"}


@dataclass
class Speech:
    audio: np.ndarray  # float32, -1..1, mono
    sample_rate: int


class Voices:
    def __init__(self, folder: Path | None = None, progress: Progress | None = None) -> None:
        self.folder = folder or models_dir() / "piper"
        self.progress = progress
        self._catalog: dict | None = None
        self._loaded: dict[str, object] = {}
        self._lock = threading.Lock()

    def catalog(self) -> dict:
        if self._catalog is None:
            path = download(CATALOG_URL, self.folder / "voices.json", "catálogo de voces", self.progress)
            self._catalog = json.loads(path.read_text(encoding="utf-8"))
        return self._catalog

    def voice_for(self, language: str) -> str | None:
        """Nombre de la mejor voz para `language` ("en", "pt", "es-AR"...), o None si Piper no tiene ese idioma."""
        family = language.split("-")[0].lower()
        region = PREFERRED_REGION.get(family, "")
        candidates = [(key, info) for key, info in self.catalog().items()
                      if info.get("language", {}).get("family") == family]
        if not candidates:
            return None

        def rank(item):
            key, info = item
            return (info["language"].get("code") != region, QUALITY_ORDER.get(info.get("quality"), 9),
                    info.get("num_speakers", 1) > 1, key)

        return min(candidates, key=rank)[0]

    def _load(self, name: str):
        from piper import PiperVoice

        if name not in self._loaded:
            files = self.catalog()[name]["files"]
            paths = {}
            for path in files:
                if path.endswith((".onnx", ".onnx.json")):
                    paths[path] = download(FILE_URL.format(path=path), self.folder / Path(path).name,
                                           f"voz {name}", self.progress)
            model = next(p for key, p in paths.items() if key.endswith(".onnx"))
            config = next(p for key, p in paths.items() if key.endswith(".onnx.json"))
            self._loaded[name] = PiperVoice.load(model, config_path=config)
        return self._loaded[name]

    def synthesize(self, text: str, language: str) -> Speech | None:
        """Dice `text` con una voz de `language`. None si no hay voz para ese idioma."""
        name = self.voice_for(language)
        if name is None or not text.strip():
            return None
        with self._lock:  # una síntesis a la vez por voz
            voice = self._load(name)
            chunks = list(voice.synthesize(text))
        if not chunks:
            return None
        audio = np.concatenate([chunk.audio_float_array for chunk in chunks]).astype(np.float32)
        return Speech(audio, chunks[0].sample_rate)
