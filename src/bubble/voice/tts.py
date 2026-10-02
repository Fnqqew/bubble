"""Voz sintética local (Piper): voces naturales que se ejecutan en el equipo, sin conexión a internet una vez
descargadas.

La voz de cada idioma se elige automáticamente del catálogo oficial de Piper (rhasspy/piper-voices) y se descarga la
primera vez que hace falta (~60 MB por idioma). Las voces se cargan y hablan en un proceso aparte (piper_worker.py),
porque cargar una voz bloqueaba la ventana ~2 s.

Cada idioma tiene voz femenina y masculina cuando es posible: si Piper ofrece solo una, la otra puede ser de Windows
(si ese idioma está agregado en el sistema). Si Windows no permite usar Piper (el «Control inteligente de
aplicaciones» bloquea un componente suyo sin firma digital), se usan solo las voces de Windows (ver
windows_voices.py).

Todas las frases pasan por prosody.py: mismo volumen y mismo tono de una frase a la otra, y respetando la expresión
del jugador.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Progress, download, models_dir
from .prosody import POLISH, change_gender, level_db

CATALOG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json"
FILE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{path}"
CATALOG_MAX_DAYS = 30  # el catálogo se vuelve a descargar periódicamente (incorpora voces nuevas)
# Acompañamiento de la voz según la expresión del jugador: (velocidad, expresividad). El volumen y el tono los define
# prosody.py.
STYLES = {"": (1.0, 0.7), "shout": (1.1, 0.9), "exclaim": (1.05, 0.85), "soft": (0.95, 0.6)}
QUALITY_ORDER = {"medium": 0, "high": 1, "low": 2, "x_low": 3}
MAX_LOADED = 3  # máximo de voces cargadas a la vez (normalmente la del jugador, la femenina y la masculina)
# Idiomas cuya voz de Piper requiere paquetes que no se instalan (pesados o sin versión para este Python): sin ellos se
# usa la voz de Windows, si existe. Lo mismo vale para las voces chinas nuevas (g2pW, transformers): se usa la clásica.
NEEDS = {"ja": ("pyopenjtalk",), "th": ("tltk", "unicode_rbnf")}
# Paquetes que se instalan solo cuando alguien elige ese idioma (son pesados: el japonés, unos 110 MB con su
# diccionario). pyopenjtalk no tiene versión para este Python; pyopenjtalk-plus es su variante compatible (mismo
# módulo). Los del tailandés (tltk) exigen versiones viejas de scikit-learn y gensim: no se instalan.
PACKS = {"ja": ("pyopenjtalk-plus",)}
PACK_MB = {"ja": 110}
# Idiomas sin voz propia, que usan la de otro idioma de lectura casi idéntica: el tagalo se escribe como se pronuncia,
# con las mismas vocales y sílabas que el indonesio; el malayo es casi indonesio; croata y serbio (en letras latinas) se
# leen como el esloveno; el macedonio como el búlgaro, el bielorruso como el ruso, el azerí como el turco y el afrikáans
# como el neerlandés. La voz «serbia» de Piper en realidad es sorabo. La voz lituana de Piper usa una fonética propia
# que Piper 1.8 no reconoce («'lithuanian' is not a valid PhonemeType»: no hablaba): el lituano lo lee la letona, que
# Whisper entiende igual de bien en lituano que en letón (57 % y 59 % de palabras mal en las mismas pruebas).
BORROWED = {"tl": "id", "ms": "id", "hr": "sl", "sr": "sl", "mk": "bg", "be": "ru", "az": "tr", "af": "nl",
            "lt": "lv"}
# Variante preferida por idioma (la más neutra o más hablada entre los jugadores).
PREFERRED_REGION = {"en": "en_US", "es": "es_MX", "pt": "pt_BR", "fr": "fr_FR", "de": "de_DE", "zh": "zh_CN",
                    "ar": "ar_JO", "hi": "hi_IN", "nl": "nl_NL"}
# Voces elegidas a mano: (femenina, masculina), por su sonido natural y claro. "voz#hablante" indica una voz con varios
# hablantes (la japonesa incluye una mujer y un hombre). Si falta una, se busca en Windows y, si tampoco está, se usa la
# del otro género.
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
    # El género de las voces cuyo nombre no lo indica se determinó midiendo el tono.
    "uk": ("uk_UA-ukrainian_tts-medium#2", "uk_UA-ukrainian_tts-medium#1"),  # Tetiana, Mykyta
    "sv": ("sv_SE-alma-medium", "sv_SE-nst-medium"),
    "no": ("no_NO-nvcc-medium#0", "no_NO-nvcc-medium#2"),  # K… mujer, M… hombre
    "da": (None, "da_DK-talesyntese-medium"),
    "fi": (None, "fi_FI-harri-medium"),
    "cs": ("cs_CZ-kasandra-medium", "cs_CZ-jirka-medium"),
    "sk": ("sk_SK-lili-medium", None),
    "hu": ("hu_HU-anna-medium", "hu_HU-imre-medium"),
    "ro": (None, "ro_RO-mihai-medium"),
    "el": ("el_GR-joy-medium", None),
    "bg": (None, "bg_BG-dimitar-medium"),
    "sl": (None, "sl_SI-artur-medium"),
    "lv": (None, "lv_LV-aivars-medium"),
    "et": ("et_EE-news-medium#2", "et_EE-news-medium#0"),  # Mari, Albert
    "he": (None, "he_IL-saspeech-medium"),
    "fa": (None, "fa_IR-amir-medium"),
    "ur": ("ur_PK-aegis_female-medium", "ur_PK-fasih-medium"),
    "bn": ("bn_BD-google-medium#12", "bn_BD-google-medium#0"),
    "mr": ("mr_IN-google-medium#0", None),
    "te": ("te_IN-maya-medium", "te_IN-venkatesh-medium"),
    "ka": ("ka_GE-natia-medium", None),
    "hy": (None, "hy_AM-gor-medium"),
    "kk": ("kk_KZ-issai-high#3", "kk_KZ-issai-high#1"),  # Raya, Iseke
    "ca": ("ca_ES-upc_ona-medium", "ca_ES-upc_pau-x_low"),
    "eu": ("eu_ES-maider-medium", "eu_ES-antton-medium"),
    "cy": ("cy_GB-bu_tts-medium#1", "cy_GB-bu_tts-medium#2"),  # benyw (mujer), gwryw (hombre)
    "is": ("is_IS-salka-medium", "is_IS-bui-medium"),
    "sq": (None, "sq_AL-edon-medium"),
    "sw": (None, "sw_CD-lanfrica-medium"),
}
# Idiomas sin ninguna voz de Piper (tamil, guyaratí, panyabí): se usan las de Windows, si ese idioma está agregado.
NO_PIPER = {"ta", "gu", "pa"}
WINDOWS = "windows:"  # prefijo de una voz de Windows (en lugar de una de Piper)
DERIVED = "~"  # "voz~masculina": voz del otro género generada a partir de esa (ver prosody.change_gender)
# Por debajo de este volumen, lo que generó la voz no se escucha: una voz que no pudo leer el texto devuelve silencio
# (o casi) en lugar de fallar, y la frase «salía» sin sonar.
MIN_LEVEL_DB = -50.0
MIN_SECONDS = 0.08  # (un audio más corto no tiene una palabra entera)

_piper_state: dict[str, str] = {}


def audible(audio: np.ndarray | None, rate: int) -> bool:
    """Indica si el audio tiene una voz que se escucha: no vacío, sin valores inválidos y con volumen suficiente."""
    if audio is None or rate <= 0 or len(audio) < rate * MIN_SECONDS:
        return False
    audio = np.asarray(audio, dtype=np.float32)
    if not np.all(np.isfinite(audio)):
        return False
    level = level_db(audio, rate)
    return level is not None and level > MIN_LEVEL_DB


def piper_blocked() -> str:
    """Devuelve "" si las voces de Piper funcionan en este equipo; si no, el motivo (se comprueba una sola vez)."""
    if "error" not in _piper_state:
        try:
            import piper.espeakbridge  # noqa: F401 - la parte que Windows puede bloquear (una DLL sin firma)

            _piper_state["error"] = ""
        except ImportError as exc:
            import logging

            logging.getLogger(__name__).warning("Las voces de Piper no se pueden usar (%s): uso las de Windows", exc)
            _piper_state["error"] = str(exc) or "no se pudo cargar"
    return _piper_state["error"]


def _unusable(error: str) -> bool:
    """Indica si el error es de la voz misma (no se puede cargar en esta PC), no de una frase en particular."""
    return any(sign in error for sign in ("PhonemeType", "No module named", "INVALID_PROTOBUF", "NO_SUCHFILE"))


def _base(name: str) -> str:
    """"ja_JP-hi_fi_captain-medium#1" o "pt_BR-faber-medium~femenina" → archivo de la voz."""
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
        self._loaded: dict[str, object] = {}  # (sin proceso aparte: voces cargadas en este proceso)
        self._lock = threading.Lock()
        self._windows = None
        self._process = None
        self._broken: set[str] = set()  # voces que no pueden hablar en esta PC (no se vuelven a intentar)
        self._pack_lock = threading.Lock()

    # ------------------------------------------------------------ qué voz
    def windows(self):
        """Voces de Windows (None si no se pueden usar)."""
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
                pass  # sin internet: se usa el ya existente
            path = download(CATALOG_URL, path, "catálogo de voces", self.progress)
            self._catalog = json.loads(path.read_text(encoding="utf-8"))
        return self._catalog

    def _piper_voice(self, family: str, gender: str) -> tuple[str | None, bool]:
        """Devuelve la voz de Piper para ese idioma si es del género pedido, o (None, False)."""
        if family in NO_PIPER or not _has_modules(family):
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
        """Devuelve la mejor voz para `language` ("en", "pt", "es-AR"...) con ese género ("femenina" o
        "masculina"): el nombre de una de Piper ("es_AR-daniela-high"), "windows:<nombre>" para una de Windows,
        o None si no hay ninguna.
        """
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
        # Si Piper no tiene ese género (o ese idioma): una voz de Windows que sí lo tenga; si no, se genera a partir de
        # la del otro género.
        chosen = windows.voice_for(lookup, gender) if windows else None
        if chosen is not None and (chosen.gender == gender or piper is None):
            return WINDOWS + chosen.name
        return f"{piper}{DERIVED}{gender}" if piper else None

    # ------------------------------------------------------------ paquetes de voz a pedido (japonés)
    def pack_missing(self, language: str) -> bool:
        """Indica si la voz de ese idioma necesita un paquete que todavía no está instalado (ver PACKS)."""
        family = language.split("-")[0].lower()
        family = BORROWED.get(family, family)
        return family in PACKS and not _has_modules(family) and not piper_blocked()

    def install_pack(self, language: str) -> bool:
        """Instala, una sola vez, el paquete que necesita la voz de ese idioma. Devuelve True si quedó lista."""
        import logging
        import subprocess

        from .piper_worker import NO_WINDOW, _python

        family = language.split("-")[0].lower()
        family = BORROWED.get(family, family)
        with self._pack_lock:
            if not self.pack_missing(language):
                return family not in PACKS or _has_modules(family)
            try:
                done = subprocess.run([_python(), "-m", "pip", "install", "--only-binary=:all:",
                                       "--disable-pip-version-check", "--quiet", *PACKS[family]],
                                      capture_output=True, text=True, timeout=1800, creationflags=NO_WINDOW)
            except (OSError, subprocess.SubprocessError) as exc:
                logging.getLogger(__name__).warning("No se pudo instalar la voz de %s: %s", family, exc)
                return False
            importlib.invalidate_caches()
            ready = done.returncode == 0 and _has_modules(family)
            if not ready:
                logging.getLogger(__name__).warning("No se pudo instalar la voz de %s: %s", family,
                                                    (done.stderr or done.stdout or "")[-400:])
            elif self._process is not None:
                self._process.close()  # el proceso de voces la encuentra al reiniciarse
            return ready

    def candidates(self, language: str, gender: str = "femenina") -> list[str]:
        """La voz elegida para el idioma y, detrás, las que la reemplazan si falla: las de Windows de ese idioma y la
        de Piper sin modificar. Sin otras opciones, solo la elegida.
        """
        first = self.voice_for(language, gender)
        found = [first] if first else []
        family = language.split("-")[0].lower()
        lookup = BORROWED[family] if family in BORROWED else language
        family = BORROWED.get(family, family)
        other = "masculina" if gender.startswith("f") else "femenina"
        windows = self.windows()
        if windows:
            for wanted in (gender, other):
                chosen = windows.voice_for(lookup, wanted)
                if chosen is not None:
                    found.append(WINDOWS + chosen.name)
        if not piper_blocked():
            try:
                piper, _exact = self._piper_voice(family, gender)
            except OSError:  # (sin el catálogo de voces: sin conexión la primera vez)
                piper = None
            if piper:
                found.append(piper)
        return [name for name in dict.fromkeys(found) if _base(name) not in self._broken]

    # ------------------------------------------------------------ bajar y cargar
    def _files(self, name: str) -> tuple[Path, Path]:
        """Descarga la voz si hace falta y devuelve (modelo, configuración)."""
        paths = {}
        for path in self.catalog()[name]["files"]:
            if path.endswith((".onnx", ".onnx.json")):
                paths[path] = download(FILE_URL.format(path=path), self.folder / Path(path).name, f"voz {name}",
                                       self.progress)
        model = next(p for key, p in paths.items() if key.endswith(".onnx"))
        config = next(p for key, p in paths.items() if key.endswith(".onnx.json"))
        return model, config

    def _worker(self):
        """Proceso aparte de las voces (None si no se puede usar: entonces se cargan en este proceso)."""
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
        """Descarga la voz (sin cargarla) para dejarla lista de antemano. Devuelve False si no hay voz para ese
        idioma.
        """
        name = self.voice_for(language, gender or self.gender)
        if name is None:
            return False
        if not name.startswith(WINDOWS):
            self._files(_base(name))  # (las de Windows ya están instaladas: no se descarga nada)
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
        """Descarga (si hace falta) y carga la voz sin reproducir nada, de modo que la primera frase salga sin
        demora.
        """
        name = self.voice_for(language, gender or self.gender)
        if name is None:
            return False
        # La primera síntesis de cada voz es más lenta (prepara el modelo): se hace una de práctica.
        return self._say("ok", language, gender, None, "", polish=False) is not None or name.startswith(WINDOWS)

    # ------------------------------------------------------------ decir
    def synthesize(self, text: str, language: str, gender: str | None = None, speed: float | None = None,
                   style: str = "") -> Speech | None:
        """Dice `text` con una voz de `language`. Devuelve None si no hay voz para ese idioma. `style`: cómo lo
        expresó el jugador ("shout", "exclaim", "soft", "question"…, ver voice/speech.py): la voz lo acompaña.
        """
        return self._say(text, language, gender, speed, style)

    def _say(self, text: str, language: str, gender: str | None, speed: float | None, style: str,
             polish: bool = True) -> Speech | None:
        import logging

        gender = gender or self.gender
        if not text.strip():
            return None
        options = self.candidates(language, gender)
        for number, name in enumerate(options):
            try:
                spoken = self._one(name, text, language, gender, speed, style)
            except Exception as exc:  # noqa: BLE001 - una voz que falla (o no se puede bajar) no deja sin voz
                logging.getLogger(__name__).warning("La voz %s falló (%s)", name, exc)
                spoken = None
            if spoken is not None and audible(*spoken):
                audio, rate = spoken
                if number:
                    logging.getLogger(__name__).warning("Para %s se usó la voz %s en lugar de %s", language, name,
                                                        options[0])
                if polish:
                    audio = POLISH.apply(audio, rate, name, style)
                return Speech(audio.astype(np.float32), rate)
            if spoken is not None:
                logging.getLogger(__name__).warning("La voz %s no produjo sonido para %r", name, text[:60])
        return None

    def _one(self, name: str, text: str, language: str, gender: str, speed: float | None,
             style: str) -> tuple[np.ndarray, int] | None:
        """Lo que dice una voz concreta, sin el ajuste final de tono y volumen (o None si no pudo)."""
        marks = set(style.split("+")) if style else set()
        mood = next((mark for mark in ("shout", "exclaim", "soft") if mark in marks), "")
        pace, expressive = STYLES[mood]
        speed = speed or self.speed
        if name.startswith(WINDOWS):
            windows = self.windows()
            if not windows:
                return None
            return windows.synthesize(text, language, gender, speed, style, name=name[len(WINDOWS):])
        spoken = self._piper(name.split(DERIVED)[0], text, speed * pace, expressive)
        if spoken is None:
            return None
        audio, rate = spoken
        if DERIVED in name:
            audio = change_gender(audio, rate, name.split(DERIVED)[1])
        return audio, rate

    def _piper(self, name: str, text: str, pace: float, expressive: float) -> tuple[np.ndarray, int] | None:
        import logging

        base, _, speaker = name.partition("#")
        # Variación natural (no monótona) y velocidad elegida.
        settings = {"length_scale": 1.0 / max(0.6, min(1.6, pace)), "noise_scale": expressive, "noise_w": 0.85,
                    "speaker": int(speaker) if speaker else None}
        worker = self._worker()
        if worker is not None:
            from .piper_worker import VoiceError

            model, config = self._files(base)
            try:
                return worker.say(base, model, config, text, settings)
            except VoiceError as exc:
                logging.getLogger(__name__).warning("La voz %s no pudo decir el texto: %s", name, exc)
                if _unusable(str(exc)):
                    self._broken.add(base)
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
            except Exception as exc:  # noqa: BLE001 - una voz que falla no corta nada: se avisa que no hay voz
                logging.getLogger(__name__).warning("La voz %s no pudo decir el texto: %s", name, exc)
                if _unusable(str(exc)):
                    self._broken.add(base)
                return None
        if not chunks:
            return None
        return np.concatenate([chunk.audio_float_array for chunk in chunks]).astype(np.float32), chunks[0].sample_rate
