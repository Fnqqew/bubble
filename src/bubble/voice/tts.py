"""Voz sintética local (Piper): voces naturales que corren en tu PC, sin internet una vez descargadas.

La voz de cada idioma se elige sola del catálogo oficial de Piper (rhasspy/piper-voices) y se descarga la primera
vez que hace falta (~60 MB por idioma). Se cargan y hablan en un proceso aparte (piper_worker.py): cargar una voz
congelaba la ventana ~2 s.

Cada idioma tiene voz femenina y masculina donde se pueda: si Piper tiene solo una, la otra puede ser de Windows
(si tenés ese idioma agregado en Windows). Si Windows no deja usar Piper (el «Control inteligente de aplicaciones»
bloquea una parte suya que no tiene firma digital), se usan solo las de Windows (ver windows_voices.py).

Todas las frases pasan por prosody.py: mismo volumen y mismo tono de una frase a la otra, y con tu expresión.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Progress, download, models_dir
from .prosody import POLISH, change_gender

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
FILE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}"
CATALOG_MAX_DAYS = 30  # el catálogo se vuelve a bajar cada tanto (así aparecen las voces nuevas)
# Cómo acompaña la voz lo que sentiste: (velocidad, expresividad). El volumen y el tono los pone prosody.py.
STYLES = {"": (1.0, 0.7), "shout": (1.1, 0.9), "exclaim": (1.05, 0.85), "soft": (0.95, 0.6)}
QUALITY_ORDER = {"medium": 0, "high": 1, "low": 2, "x_low": 3}
MAX_LOADED = 3  # voces sintéticas cargadas a la vez (la tuya, la femenina y la masculina, casi siempre)
# Idiomas cuya voz de Piper necesita paquetes que no se instalan (pesados o sin versión para este Python): sin
# ellos, la voz de Windows (si hay). Las voces chinas nuevas también (g2pW, transformers): se usa la clásica.
NEEDS = {"ja": ("pyopenjtalk",), "th": ("tltk", "unicode_rbnf")}
# Sin voz propia en ningún lado: la de un idioma que se lee casi igual (el tagalo se escribe como se dice, con las
# mismas vocales y sílabas que el indonesio).
BORROWED = {"tl": "id"}
# Variante preferida por idioma (la más neutra / más hablada entre jugadores).
PREFERRED_REGION = {"en": "en_US", "es": "es_MX", "pt": "pt_BR", "fr": "fr_FR", "de": "de_DE", "zh": "zh_CN",
                    "ar": "ar_JO", "hi": "hi_IN", "nl": "nl_NL"}
# Voces elegidas a mano: (femenina, masculina). Suenan naturales y claras. "voz#hablante": una voz con varios
# hablantes (la japonesa trae una mujer y un hombre). Si falta una, se busca en Windows y si no, se usa la otra.
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
    "zh": ("zh_CN-huayan-medium", None),
    "ja": ("ja_JP-hi_fi_captain-medium#0", "ja_JP-hi_fi_captain-medium#1"),
    "hi": ("hi_IN-priyamvada-medium", "hi_IN-pratham-medium"),
    "tr": (None, "tr_TR-dfki-medium"),
    "ar": (None, "ar_JO-kareem-medium"),
    "ko": ("ko_KR-kss-medium", None),
    "id": ("id_ID-news_tts-medium", None),
    "vi": ("vi_VN-vais1000-medium", None),
    "th": ("th_TH-tsync2-medium", None),
}
WINDOWS = "windows:"  # prefijo de una voz de Windows (en vez de una de Piper)
DERIVED = "~"  # "voz~masculina": la voz del otro género hecha a partir de esa (ver prosody.change_gender)

_piper_state: dict[str, str] = {}


def piper_blocked() -> str:
    """"" si las voces de Piper andan en esta PC; si no, por qué (se revisa una vez)."""
    if "error" not in _piper_state:
        try:
            import piper.espeakbridge  # noqa: F401 - la parte que Windows puede bloquear (una DLL sin firma)

            _piper_state["error"] = ""
        except ImportError as exc:
            import logging

            logging.getLogger(__name__).warning("Las voces de Piper no se pueden usar (%s): uso las de Windows", exc)
            _piper_state["error"] = str(exc) or "no se pudo cargar"
    return _piper_state["error"]


def _base(name: str) -> str:
    """"ja_JP-hi_fi_captain-medium#1" o "pt_BR-faber-medium~femenina" → el archivo de la voz."""
    return name.split(DERIVED)[0].split("#")[0]


def _has_modules(family: str) -> bool:
    return all(importlib.util.find_spec(module) is not None for module in NEEDS.get(family, ()))


@dataclass
class Speech:
    audio: np.ndarray  # float32, -1..1, mono
    sample_rate: int


class Voices:
    def __init__(self, folder: Path | None = None, progress: Progress | None = None, use_process: bool = True) -> None:
        self.folder = folder or models_dir() / "piper"
        self.progress = progress
        self.gender = "femenina"  # o "masculina"
        self.speed = 1.0
        self.use_process = use_process
        self._catalog: dict | None = None
        self._loaded: dict[str, object] = {}  # (si no se pudo usar el proceso aparte: las voces cargadas acá)
        self._lock = threading.Lock()
        self._windows = None
        self._process = None

    # ------------------------------------------------------------ qué voz
    def windows(self):
        """Las voces de Windows (None si no se pueden usar)."""
        if self._windows is None:
            try:
                from .windows_voices import WindowsVoices

                self._windows = WindowsVoices()
            except Exception:  # noqa: BLE001 - sin voces de Windows, solo Piper
                self._windows = False
        return self._windows or None

    def catalog(self) -> dict:
        if self._catalog is None:
            path = self.folder / "voices.json"
            try:
                import time

                if path.exists() and time.time() - path.stat().st_mtime > CATALOG_MAX_DAYS * 86400:
                    fresh = download(CATALOG_URL, path.with_suffix(".new"), "catálogo de voces", self.progress)
                    fresh.replace(path)
            except OSError:
                pass  # sin internet: sirve el que ya estaba
            path = download(CATALOG_URL, path, "catálogo de voces", self.progress)
            self._catalog = json.loads(path.read_text(encoding="utf-8"))
        return self._catalog

    def _piper_voice(self, family: str, gender: str) -> tuple[str | None, bool]:
        """(la voz de Piper, si es del género pedido) para ese idioma, o (None, False)."""
        if not _has_modules(family):
            return None, False
        female, male = CURATED.get(family, (None, None))
        wanted, other = (male, female) if gender.startswith("m") else (female, male)
        catalog = self.catalog()
        if wanted and wanted.split("#")[0] in catalog:
            return wanted, True
        if other and other.split("#")[0] in catalog:
            return other, False
        region = PREFERRED_REGION.get(family, "")
        candidates = [(key, info) for key, info in catalog.items()
                      if info.get("language", {}).get("family") == family]
        if not candidates:
            return None, False

        def rank(item):
            key, info = item
            return (info["language"].get("code") != region, QUALITY_ORDER.get(info.get("quality"), 9),
                    info.get("num_speakers", 1) > 1, key)

        return min(candidates, key=rank)[0], False

    def voice_for(self, language: str, gender: str = "femenina") -> str | None:
        """La mejor voz para `language` ("en", "pt", "es-AR"...) con ese género ("femenina" o "masculina"): el nombre
        de una de Piper ("es_AR-daniela-high"), "windows:<nombre>" para una de Windows, o None si no hay ninguna."""
        family = language.split("-")[0].lower()
        lookup = BORROWED[family] if family in BORROWED else language  # (en Windows, la región más parecida)
        family = BORROWED.get(family, family)
        windows = self.windows()
        if piper_blocked():
            chosen = windows.voice_for(lookup, gender) if windows else None
            return WINDOWS + chosen.name if chosen else None
        piper, exact = self._piper_voice(family, gender)
        if piper and exact:
            return piper
        # Piper no tiene ese género (o ese idioma): una de Windows que sí; si no, se hace a partir de la del otro género.
        chosen = windows.voice_for(lookup, gender) if windows else None
        if chosen is not None and (chosen.gender == gender or piper is None):
            return WINDOWS + chosen.name
        return f"{piper}{DERIVED}{gender}" if piper else None

    # ------------------------------------------------------------ bajar y cargar
    def _files(self, name: str) -> tuple[Path, Path]:
        """Baja la voz si hace falta: (modelo, configuración)."""
        paths = {}
        for path in self.catalog()[name]["files"]:
            if path.endswith((".onnx", ".onnx.json")):
                paths[path] = download(FILE_URL.format(path=path), self.folder / Path(path).name, f"voz {name}",
                                       self.progress)
        model = next(p for key, p in paths.items() if key.endswith(".onnx"))
        config = next(p for key, p in paths.items() if key.endswith(".onnx.json"))
        return model, config

    def _worker(self):
        """El proceso aparte de las voces (None si no se puede usar: entonces se cargan acá)."""
        if not self.use_process:
            return None
        if self._process is None:
            from .piper_worker import PiperProcess

            self._process = PiperProcess()
        return None if self._process.failed else self._process

    def _load_here(self, name: str):
        from piper import PiperVoice

        if name not in self._loaded:
            model, config = self._files(name)
            self._loaded[name] = PiperVoice.load(model, config_path=config)
            while len(self._loaded) > MAX_LOADED:
                del self._loaded[next(iter(self._loaded))]
        else:
            self._loaded[name] = self._loaded.pop(name)  # la más reciente, al final
        return self._loaded[name]

    def download(self, language: str, gender: str | None = None) -> bool:
        """Baja la voz (sin cargarla), para tenerla lista de antemano. False si no hay voz para ese idioma."""
        name = self.voice_for(language, gender or self.gender)
        if name is None:
            return False
        if not name.startswith(WINDOWS):
            self._files(_base(name))  # (las de Windows ya están: no se baja nada)
        return True

    def is_loaded(self, language: str, gender: str | None = None) -> bool:
        name = self.voice_for(language, gender or self.gender)
        if name is None or name.startswith(WINDOWS):
            return name is not None
        worker = self._worker()
        return _base(name) in (worker.loaded if worker is not None else self._loaded)

    def is_downloaded(self, language: str, gender: str | None = None) -> bool:
        name = self.voice_for(language, gender or self.gender)
        if name is None or name.startswith(WINDOWS):
            return name is not None
        return (self.folder / f"{_base(name)}.onnx").exists()

    def prepare(self, language: str, gender: str | None = None) -> bool:
        """Descarga (si hace falta) y carga la voz, sin decir nada: así la primera frase sale enseguida."""
        name = self.voice_for(language, gender or self.gender)
        if name is None:
            return False
        # La primera síntesis de cada voz es más lenta (prepara el modelo): se hace una de práctica.
        return self._say("ok", language, gender, None, "", polish=False) is not None or name.startswith(WINDOWS)

    # ------------------------------------------------------------ decir
    def synthesize(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
                   style: str = "") -> Speech | None:
        """Dice `text` con una voz de `language`. None si no hay voz para ese idioma. `style`: cómo lo dijiste vos
        ("shout", "exclaim", "soft", "question"…, ver voice/speech.py): la voz lo acompaña."""
        return self._say(text, language, gender, speed, style)

    def _say(self, text: str, language: str, gender: str | None, speed: float | None, style: str,
             polish: bool = True) -> Speech | None:
        gender = gender or self.gender
        name = self.voice_for(language, gender)
        if name is None or not text.strip():
            return None
        marks = set(style.split("+")) if style else set()
        mood = next((mark for mark in ("shout", "exclaim", "soft") if mark in marks), "")
        pace, expressive = STYLES[mood]
        speed = speed or self.speed
        if name.startswith(WINDOWS):
            windows = self.windows()
            spoken = windows.synthesize(text, language, gender, speed, style, name=name[len(WINDOWS):])                 if windows else None
            if spoken is None:
                return None
            audio, rate = spoken
        else:
            spoken = self._piper(name.split(DERIVED)[0], text, speed * pace, expressive)
            if spoken is None:
                return None
            audio, rate = spoken
            if DERIVED in name:
                audio = change_gender(audio, rate, name.split(DERIVED)[1])
        if polish:
            audio = POLISH.apply(audio, rate, name, style)
        return Speech(audio.astype(np.float32), rate)

    def _piper(self, name: str, text: str, pace: float, expressive: float) -> tuple[np.ndarray, int] | None:
        import logging

        base, _, speaker = name.partition("#")
        # Un poco de variación natural (no monótona) y la velocidad elegida.
        settings = {"length_scale": 1.0 / max(0.6, min(1.6, pace)), "noise_scale": expressive, "noise_w": 0.85,
                    "speaker": int(speaker) if speaker else None}
        worker = self._worker()
        if worker is not None:
            from .piper_worker import VoiceError

            model, config = self._files(base)
            try:
                return worker.say(base, model, config, text, settings)
            except VoiceError:
                logging.getLogger(__name__).exception("La voz %s no pudo decir el texto", name)
                return None
            except Exception as exc:  # noqa: BLE001 - el proceso no anda: se usa este
                logging.getLogger(__name__).warning("Las voces no andan en un proceso aparte (%s): uso este", exc)
                worker.failed = str(exc) or "no anda"
                worker.close()
        from piper.config import SynthesisConfig

        config = SynthesisConfig(speaker_id=settings["speaker"], length_scale=settings["length_scale"],
                                 noise_scale=expressive, noise_w_scale=0.85)
        with self._lock:  # una síntesis a la vez por voz
            try:
                voice = self._load_here(base)
                chunks = list(voice.synthesize(text, config))
            except Exception:  # noqa: BLE001 - una voz que falla no corta nada: se avisa que no hay voz
                logging.getLogger(__name__).exception("La voz %s no pudo decir el texto", name)
                return None
        if not chunks:
            return None
        return np.concatenate([chunk.audio_float_array for chunk in chunks]).astype(np.float32), chunks[0].sample_rate
