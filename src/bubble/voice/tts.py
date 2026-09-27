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
# Cómo acompaña la voz lo que sentiste: (velocidad, expresividad, volumen).
STYLES = {"": (1.0, 0.7, 1.0), "shout": (1.1, 0.9, 1.4), "exclaim": (1.05, 0.85, 1.2), "soft": (0.95, 0.6, 0.75)}
QUALITY_ORDER = {"medium": 0, "high": 1, "low": 2, "x_low": 3}
# Idiomas cuya voz de Piper necesita programas extra que no vienen instalados (tailandés: tltk; japonés: pyopenjtalk).
MAX_LOADED = 3  # voces sintéticas cargadas a la vez (la tuya, la femenina y la masculina, casi siempre)
NO_VOICE = {"th", "ja"}
# Variante preferida por idioma (la más neutra / más hablada entre jugadores).
PREFERRED_REGION = {"en": "en_US", "es": "es_MX", "pt": "pt_BR", "fr": "fr_FR", "de": "de_DE", "zh": "zh_CN",
                    "ar": "ar_JO", "hi": "hi_IN", "nl": "nl_NL"}
# Voces elegidas a mano para los idiomas base: (femenina, masculina). Suenan naturales y claras; si un idioma no tiene
# voz de uno de los dos, se usa la otra.
CURATED: dict[str, tuple[str | None, str | None]] = {
    "es": ("es_AR-daniela-high", "es_MX-ald-medium"),
    "en": ("en_US-amy-medium", "en_US-ryan-medium"),
    "pt": (None, "pt_BR-faber-medium"),
    "fr": ("fr_FR-siwis-medium", "fr_FR-tom-medium"),
    "de": ("de_DE-kerstin-low", "de_DE-thorsten-medium"),
    "it": ("it_IT-paola-medium", "it_IT-riccardo-x_low"),
    "ru": ("ru_RU-irina-medium", "ru_RU-dmitri-medium"),
    "pl": ("pl_PL-gosia-medium", "pl_PL-darkman-medium"),
    "nl": ("nl_BE-nathalie-medium", "nl_NL-pim-medium"),
    "zh": ("zh_CN-huayan-medium", "zh_CN-chaowen-medium"),
    "hi": ("hi_IN-priyamvada-medium", "hi_IN-pratham-medium"),
    "tr": (None, "tr_TR-dfki-medium"),
    "ar": (None, "ar_JO-kareem-medium"),
    "ko": ("ko_KR-kss-medium", None),
    "id": ("id_ID-news_tts-medium", None),
    "vi": ("vi_VN-vais1000-medium", None),
}


@dataclass
class Speech:
    audio: np.ndarray  # float32, -1..1, mono
    sample_rate: int


class Voices:
    def __init__(self, folder: Path | None = None, progress: Progress | None = None) -> None:
        self.folder = folder or models_dir() / "piper"
        self.progress = progress
        self.gender = "femenina"  # o "masculina"
        self.speed = 1.0
        self._catalog: dict | None = None
        self._loaded: dict[str, object] = {}
        self._lock = threading.Lock()

    def catalog(self) -> dict:
        if self._catalog is None:
            path = download(CATALOG_URL, self.folder / "voices.json", "catálogo de voces", self.progress)
            self._catalog = json.loads(path.read_text(encoding="utf-8"))
        return self._catalog

    def voice_for(self, language: str, gender: str = "femenina") -> str | None:
        """Nombre de la mejor voz para `language` ("en", "pt", "es-AR"...) con ese género ("femenina" o
        "masculina"), o None si Piper no tiene ese idioma."""
        family = language.split("-")[0].lower()
        if family in NO_VOICE:
            return None
        female, male = CURATED.get(family, (None, None))
        wanted, other = (male, female) if gender.startswith("m") else (female, male)
        for name in (wanted, other):
            if name and name in self.catalog():
                return name
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
            # Cada voz ocupa 60-100 MB: quedan cargadas las últimas usadas (las demás se vuelven a cargar si hacen
            # falta, en ~1 s). Antes quedaban todas las que alguna vez se usaron.
            while len(self._loaded) > MAX_LOADED:
                del self._loaded[next(iter(self._loaded))]
        else:
            self._loaded[name] = self._loaded.pop(name)  # la más reciente, al final
        return self._loaded[name]

    def is_loaded(self, language: str, gender: str | None = None) -> bool:
        name = self.voice_for(language, gender or self.gender)
        return name is not None and name in self._loaded

    def is_downloaded(self, language: str, gender: str | None = None) -> bool:
        name = self.voice_for(language, gender or self.gender)
        return name is not None and (self.folder / f"{name}.onnx").exists()

    def prepare(self, language: str, gender: str | None = None) -> bool:
        """Descarga (si hace falta) y carga la voz, sin decir nada: así la primera frase sale enseguida."""
        name = self.voice_for(language, gender or self.gender)
        if name is None:
            return False
        with self._lock:
            voice = self._load(name)
        # La primera síntesis de cada voz es más lenta (prepara el modelo): se hace una de práctica.
        self.synthesize("ok", language, gender)
        return voice is not None

    def synthesize(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
                   style: str = "") -> Speech | None:
        """Dice `text` con una voz de `language`. None si no hay voz para ese idioma. `style`: cómo lo dijiste vos
        ("shout", "exclaim", "soft"…, ver voice/speech.py): la voz lo acompaña (más rápida y fuerte, o más suave)."""
        name = self.voice_for(language, gender or self.gender)
        if name is None or not text.strip():
            return None
        from piper.config import SynthesisConfig

        marks = set(style.split("+")) if style else set()
        mood = next((mark for mark in ("shout", "exclaim", "soft") if mark in marks), "")
        pace, expressive, gain = STYLES[mood]
        # Un poco de variación natural (no monótona) y la velocidad elegida.
        config = SynthesisConfig(length_scale=1.0 / max(0.6, min(1.6, (speed or self.speed) * pace)),
                                 noise_scale=expressive, noise_w_scale=0.85)
        with self._lock:  # una síntesis a la vez por voz
            voice = self._load(name)
            try:
                chunks = list(voice.synthesize(text, config))
            except Exception:  # noqa: BLE001 - una voz que falla no corta nada: se avisa que no hay voz
                import logging

                logging.getLogger(__name__).exception("La voz %s no pudo decir el texto", name)
                return None
        if not chunks:
            return None
        audio = np.concatenate([chunk.audio_float_array for chunk in chunks]).astype(np.float32)
        if gain != 1.0:
            peak = float(np.max(np.abs(audio))) or 1.0
            audio = audio * min(gain, 0.98 / peak)  # más fuerte, sin saturar
        return Speech(audio, chunks[0].sample_rate)
