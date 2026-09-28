"""Lo que Bubble necesita para andar, y cómo instalarlo solo (ver ui/setup_window.py).

Al abrir se revisa rápido (sin descargar nada). Si falta algo necesario, la ventana «Preparar Bubble» lo instala sola:
la parte de voz (paquetes de Python), el reconocimiento de voz de tu PC, el de voces y una voz sintética. Lo que toca
Windows o tu cuenta (Claude Code, el micrófono virtual) no se instala a escondidas: tiene su botón y pide permiso.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
VOICE_MODULES = ("faster_whisper", "piper", "soundcard", "websockets", "onnxruntime")
Progress = Callable[[str, float], None]  # (qué está haciendo, 0..1; -1 si no se sabe cuánto falta)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Step:
    key: str
    title: str
    detail: str
    check: Callable[[], bool]
    run: Callable[[Progress], str] | None = None  # None: no se instala solo (ver `action`)
    required: bool = True  # sin esto, una parte de Bubble no anda
    action: str = ""  # texto del botón, si hace falta que lo apruebes
    after: list[str] = field(default_factory=list)  # pasos que tienen que estar antes


# ---------------------------------------------------------------- revisar
def _has_modules(names=VOICE_MODULES) -> bool:
    importlib.invalidate_caches()
    return all(importlib.util.find_spec(name) is not None for name in names)


def _whisper_names() -> list[str]:
    from .voice.asr import pick_models

    return [name for name in pick_models() if name]


def _whisper_ready() -> bool:
    from .voice.models import models_dir

    folder = models_dir() / "whisper"
    return all(any(folder.glob(f"models--*faster-whisper-{name}*/snapshots/*/model.bin")) for name in _whisper_names())


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
    """Los componentes de Visual C++ de Windows (sin ellos, la parte de voz no carga: "DLL load failed")."""
    import ctypes.util

    return all(ctypes.util.find_library(name) for name in ("msvcp140", "vcruntime140", "vcruntime140_1"))


def _session_ready() -> bool:
    from .system import claude_status

    status = claude_status()
    return status.installed and status.logged_in is not False  # (si no se puede saber, no se insiste)


def _cable_ready() -> bool:
    try:
        from .voice import audio as audio_io

        return audio_io.virtual_cable() is not None
    except Exception:  # noqa: BLE001 - sin la parte de voz no se puede saber
        return False


# ---------------------------------------------------------------- instalar
def _pip_install(progress: Progress) -> str:
    """Los paquetes de la parte de voz, en el mismo Python con el que corre Bubble."""
    if (PROJECT_DIR / "pyproject.toml").exists():
        target = [f"{PROJECT_DIR}[voz]"] if not (PROJECT_DIR / ".git").exists() else ["-e", f"{PROJECT_DIR}[voz]"]
    else:
        target = ["faster-whisper>=1.2", "piper-tts>=1.3", "soundcard>=0.4", "websockets>=14"]
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", *target]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                               encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    for line in process.stdout:
        line = line.strip()
        if line.startswith(("Collecting", "Downloading", "Installing collected", "Building")):
            progress(line.split(" (")[0][:80], -1)
    if process.wait() != 0:
        raise RuntimeError("pip no pudo instalar la parte de voz (¿hay internet?)")
    importlib.invalidate_caches()
    return "Parte de voz instalada."


def _download_whisper(progress: Progress) -> str:
    from faster_whisper import download_model

    from .voice.models import models_dir

    names = _whisper_names()
    for index, name in enumerate(names):
        progress(f"Reconocimiento de voz «{name}» ({index + 1} de {len(names)})", -1)
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
    """El instalador oficial de Claude Code, en una ventana a la vista (después hay que iniciar sesión)."""
    script = ("irm https://claude.ai/install.ps1 | iex; "
              "Write-Host ''; Write-Host 'Ahora inicia sesion con tu suscripcion:' -ForegroundColor Yellow; "
              "& \"$env:USERPROFILE\\.local\\bin\\claude.exe\"")
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", script],
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return "Se abrió el instalador de Claude Code: cuando termine, iniciá sesión y volvé a abrir Bubble."


def _install_runtime(progress: Progress) -> str:
    """El instalador oficial de Microsoft (Windows pide permiso de administrador)."""
    import ctypes
    import shutil

    if shutil.which("winget"):
        args = "install -e --id Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements"
        if ctypes.windll.shell32.ShellExecuteW(None, "runas", "winget", args, None, 1) > 32:
            return "Se está instalando (Windows pidió permiso). Cuando termine, volvé a abrir Bubble."
    import webbrowser

    webbrowser.open("https://aka.ms/vs/17/release/vc_redist.x64.exe")
    return "Se descargó el instalador de Microsoft: abrilo, instalalo y volvé a abrir Bubble."


def _login_claude(progress: Progress) -> str:
    """Abre Claude Code a la vista para que inicies sesión con tu cuenta de Claude."""
    from .claude_cli import find_claude_cli

    subprocess.Popen([find_claude_cli()], creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    return "Se abrió Claude Code: iniciá sesión con tu cuenta de Claude (Pro o Max) y volvé a abrir Bubble."


def _install_cable(progress: Progress) -> str:
    from .voice.bridge import install_cable

    return install_cable(progress)


def steps() -> list[Step]:
    return [
        Step("windows", "Componentes de Windows", "Visual C++ de Microsoft: los necesita la parte de voz.",
             _runtime_ready, _install_runtime, action="Instalar componentes"),
        Step("voz", "Parte de voz", "Paquetes para entender y decir voces (~300 MB).", _has_modules, _pip_install),
        Step("whisper", "Reconocimiento de voz", "Entiende voces en tu PC (Basic), hasta ~500 MB.", _whisper_ready,
             _download_whisper, after=["voz"]),
        Step("voces", "Reconocimiento de voces", "Sabe quién habla (Voz 1, Voz 2…), ~30 MB.", _speakers_ready,
             _download_speakers, after=["voz"]),
        Step("tts", "Voces sintéticas", "Tu voz traducida en inglés (femenina y masculina), ~120 MB.", _voice_ready,
             _download_voice, after=["voz"]),
        Step("claude", "Claude Code", "Traduce con tu suscripción de Claude. Se instala y después iniciás sesión.",
             _claude_ready, _install_claude, action="Instalar Claude Code"),
        Step("sesion", "Tu cuenta de Claude", "Claude Code con la sesión iniciada (plan Pro o Max).", _session_ready,
             _login_claude, action="Iniciar sesión", after=["claude"]),
        Step("cable", "Micrófono virtual", "Para que los demás escuchen tu voz traducida. Windows pide permiso de "
             "administrador.", _cable_ready, _install_cable, required=False, action="Instalar micrófono virtual",
             after=["voz"]),
    ]


def missing(only_required: bool = True) -> list[Step]:
    """Lo que falta (rápido: no descarga nada)."""
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
    """¿Se instala solo (sin pedirte nada)?"""
    return step.run is not None and not step.action


def voice_ready() -> bool:
    return _has_modules()


def environment_note() -> str:
    """Dónde corre Bubble (para mostrar)."""
    return f"Python {sys.version.split()[0]} · {os.path.dirname(sys.executable)}"
