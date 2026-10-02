"""Requisitos de Bubble y su instalación automática (ver ui/setup_window.py).

Al abrir se hace una revisión rápida, sin descargar nada. Si falta algo necesario, la ventana «Preparar Bubble» lo
instala: la parte de voz (paquetes de Python), el reconocimiento de voz del equipo, el de voces y una voz sintética. Lo
que modifica Windows o la cuenta del usuario (Claude Code, el micrófono virtual) no se instala de forma automática:
tiene su propio botón y requiere autorización.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
VOICE_MODULES = ("faster_whisper", "piper", "soundcard", "websockets", "onnxruntime")
# Se instala sin dependencias (las que usa están en el extra «voz» de pyproject.toml; PyAV no hace falta). La misma
# versión figura en Iniciar.bat y en update_helper.py.
WHISPER_PACKAGE = "faster-whisper==1.2.1"
# Paquetes que Bubble dejó de usar en la versión 4.2: en las PCs que los tenían se desinstalan (~180 MB, ver tidy).
LEFTOVERS = ("scipy", "av")
# Sin guardar copia de lo descargado: pip la conservaba en su caché (~350 MB que nadie volvía a usar).
PIP_INSTALL = ("-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir")
Progress = Callable[[str, float], None]  # (descripción de la tarea, 0..1; -1 si se desconoce el total)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
log = logging.getLogger(__name__)


@dataclass
class Step:
    key: str
    title: str
    detail: str
    check: Callable[[], bool]
    run: Callable[[Progress], str] | None = None  # None: no se instala automáticamente (ver `action`)
    required: bool = True  # sin este paso, una parte de Bubble no funciona
    action: str = ""  # texto del botón, si requiere aprobación del usuario
    after: list[str] = field(default_factory=list)  # pasos que deben completarse antes


# ---------------------------------------------------------------- revisar
def _has_modules(names=VOICE_MODULES) -> bool:
    importlib.invalidate_caches()
    return all(importlib.util.find_spec(name) is not None for name in names)


def _whisper_names() -> list[str]:
    from .voice.asr import pick_models

    return [name for name in pick_models() if name]


def _whisper_ready() -> bool:
    from .voice.models import whisper_path

    return all(whisper_path(name) for name in _whisper_names())


def _speakers_ready() -> bool:
    from .voice.models import models_dir

    return (models_dir() / "speaker").is_dir() and any((models_dir() / "speaker").glob("*.onnx"))


def _voice_ready() -> bool:
    from .voice.tts import Voices

    try:
        return Voices().is_downloaded("en")
    except Exception:  # noqa: BLE001 - sin catálogo todavía
        return False


def _claude_ready() -> bool:
    from .claude_cli import ClaudeNotFoundError, find_claude_cli

    try:
        find_claude_cli()
        return True
    except ClaudeNotFoundError:
        return False


def _runtime_ready() -> bool:
    """Componentes de Visual C++ de Windows (sin ellos, la parte de voz no carga: "DLL load failed")."""
    import ctypes.util

    return all(ctypes.util.find_library(name) for name in ("msvcp140", "vcruntime140", "vcruntime140_1"))


def _session_ready() -> bool:
    from .system import claude_status

    status = claude_status()
    return status.installed and status.logged_in is not False  # (si no se puede determinar, no se insiste)


def _cable_ready() -> bool:
    try:
        from .voice import audio as audio_io

        return audio_io.virtual_cable() is not None
    except Exception:  # noqa: BLE001 - sin la parte de voz no se puede saber
        return False


# ---------------------------------------------------------------- instalar
def _pip_install(progress: Progress) -> str:
    """Paquetes de la parte de voz, instalados en el mismo Python con el que corre Bubble."""
    if (PROJECT_DIR / "pyproject.toml").exists():
        target = [f"{PROJECT_DIR}[voz]"] if not (PROJECT_DIR / ".git").exists() else ["-e", f"{PROJECT_DIR}[voz]"]
    else:
        target = ["ctranslate2>=4.0,<5", "huggingface-hub>=0.21", "tokenizers>=0.13,<1", "onnxruntime>=1.14,<2",
                  "tqdm", "piper-tts>=1.3", "soundcard>=0.4", "websockets>=14"]
    whisper = [sys.executable, *PIP_INSTALL, "--no-deps", WHISPER_PACKAGE]
    for command in ([sys.executable, *PIP_INSTALL, *target], whisper):
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                   encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        for line in process.stdout:
            line = line.strip()
            if line.startswith(("Collecting", "Downloading", "Installing collected", "Building")):
                progress(line.split(" (")[0][:80], -1)
        if process.wait() != 0:
            raise RuntimeError("pip no pudo instalar la parte de voz (¿hay conexión?)")
    importlib.invalidate_caches()
    return "Parte de voz instalada."


def _download_whisper(progress: Progress) -> str:
    from .voice.models import WHISPER_LIGHT, download_whisper, models_dir, whisper_path

    names = _whisper_names()
    for index, name in enumerate(names):
        if whisper_path(name):
            continue
        label = f"Reconocimiento de voz «{name}» ({index + 1} de {len(names)})"
        progress(label, -1)
        if name in WHISPER_LIGHT:
            try:
                download_whisper(name, label, progress)
                continue
            except (OSError, ValueError) as exc:  # (GitHub bloqueado, descarga dañada): se usa el de Systran
                log.warning("No se pudo bajar el Whisper liviano «%s»: %s", name, exc)
        from . import voice  # noqa: F401 - prepara faster-whisper para funcionar sin PyAV
        from faster_whisper import download_model

        download_model(name, cache_dir=str(models_dir() / "whisper"))
    return "Reconocimiento de voz listo."


def _download_speakers(progress: Progress) -> str:
    from .voice.models import download, models_dir
    from .voice.speakers import MODEL_NAME, MODEL_URL

    download(MODEL_URL, models_dir() / "speaker" / MODEL_NAME, "Reconocimiento de voces", progress)
    return "Reconocimiento de voces listo."


def _download_voice(progress: Progress) -> str:
    from .voice.tts import Voices

    voices = Voices(progress=progress)
    for gender in ("femenina", "masculina"):
        voices.download("en", gender)
    return "Voces en inglés listas."


def _install_claude(progress: Progress) -> str:
    """Instalador oficial de Claude Code, en una ventana visible (luego hay que iniciar sesión)."""
    script = ("irm https://claude.ai/install.ps1 | iex; "
              "Write-Host ''; Write-Host 'Ahora inicia sesion con tu suscripcion:' -ForegroundColor Yellow; "
              "& \"$env:USERPROFILE\\.local\\bin\\claude.exe\"")
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", script],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return "Se abrió el instalador de Claude Code. Cuando termine, iniciá sesión y volvé a abrir Bubble."


def _install_runtime(progress: Progress) -> str:
    """Instalador oficial de Microsoft (Windows solicita permisos de administrador)."""
    import ctypes
    import shutil

    if shutil.which("winget"):
        args = "install -e --id Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements"
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", "winget", args, None, 1) > 32:
            return "Se está instalando. Cuando termine, volvé a abrir Bubble."
    import webbrowser

    webbrowser.open("https://aka.ms/vs/17/release/vc_redist.x64.exe")
    return "Ya descargué el instalador de Microsoft. Abrilo, instalalo y volvé a abrir Bubble."


def _login_claude(progress: Progress) -> str:
    """Inicia sesión en Claude Code con la suscripción del jugador: se abre el navegador en una ventana pequeña y
    visible. También sirve tras suscribirse: Claude Code detecta el plan nuevo al volver a iniciar sesión.
    """
    from .claude_cli import find_claude_cli

    subprocess.Popen([find_claude_cli(), "auth", "login", "--claudeai"],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return "Se abrió Claude en el navegador. Entrá con tu cuenta (Pro o Max)."


def _install_cable(progress: Progress) -> str:
    from .voice.bridge import install_cable

    return install_cable(progress)


def steps() -> list[Step]:
    return [
        Step("windows", "Componentes de Windows", "Visual C++ de Microsoft: los necesita la parte de voz.",
             _runtime_ready, _install_runtime, action="Instalar componentes"),
        Step("voz", "Parte de voz", "Paquetes para entender y decir voces (~80 MB).", _has_modules, _pip_install),
        Step("whisper", "Reconocimiento de voz", "Entiende voces en tu PC (Basic), hasta ~330 MB.", _whisper_ready,
             _download_whisper, after=["voz"]),
        Step("voces", "Reconocimiento de voces", "Sabe quién habla (Voz 1, Voz 2…), ~30 MB.", _speakers_ready,
             _download_speakers, after=["voz"]),
        Step("tts", "Voces sintéticas", "Tu voz traducida en inglés (femenina y masculina), ~120 MB.", _voice_ready,
             _download_voice, after=["voz"]),
        # (Claude no es obligatorio: sin él, Bubble Pro traduce con créditos; ver ui/no_claude_window.py)
        Step("claude", "Claude Code", "Es lo que traduce, con tu cuenta de Claude. Se instala y después "
                                      "iniciás sesión.",
             _claude_ready, _install_claude, required=False, action="Instalar Claude Code"),
        Step("sesion", "Tu cuenta de Claude", "Claude Code con tu sesión iniciada (Pro o Max). Si no tenés, "
                                              "Bubble Pro traduce mientras tanto con créditos gratis.", _session_ready,
             _login_claude, required=False, action="Iniciar sesión", after=["claude"]),
        Step("cable", "Micrófono virtual", "Para que los demás escuchen tu voz traducida. Windows te va a "
                                           "pedir permiso.", _cable_ready, _install_cable, required=False, action="Instalar micrófono virtual",
             after=["voz"]),
    ]


def missing(only_required: bool = True) -> list[Step]:
    """Pasos pendientes (revisión rápida, sin descargar nada)."""
    result = []
    for step in steps():
        if only_required and not step.required:
            continue
        try:
            ready = step.check()
        except Exception:  # noqa: BLE001 - si no se puede revisar, se da por faltante
            ready = False
        if not ready:
            result.append(step)
    return result


def automatic(step: Step) -> bool:
    """Indica si el paso se instala automáticamente, sin intervención del usuario."""
    return step.run is not None and not step.action


def voice_ready() -> bool:
    return _has_modules()


def _installed(name: str) -> bool:
    try:
        importlib.metadata.distribution(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def tidy() -> list[str]:
    """Desinstala los paquetes que Bubble ya no usa (ver LEFTOVERS) y devuelve cuáles. No lo hace en la PC de
    desarrollo, donde los usan las pruebas y el laboratorio de voz.
    """
    if _installed("pytest"):
        return []
    present = [name for name in LEFTOVERS if _installed(name)]
    if not present:
        return []
    executable = Path(sys.executable)
    python = executable.with_name("python.exe") if executable.name.lower() == "pythonw.exe" else executable
    done = subprocess.run([str(python if python.exists() else executable), "-m", "pip", "uninstall", "-y",
                           "--disable-pip-version-check", *present], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=600, creationflags=NO_WINDOW)
    if done.returncode != 0:
        log.info("No se pudieron desinstalar %s: %s", present, (done.stderr or done.stdout)[-300:])
        return []
    log.info("Desinstalados (Bubble ya no los usa): %s", ", ".join(present))
    return present


def environment_note() -> str:
    """Entorno en el que corre Bubble (para mostrar en la interfaz)."""
    return f"Python {sys.version.split()[0]} · {os.path.dirname(sys.executable)}"
