"""Hablar "por tu micrófono", como Soundpad: lo que escucha Roblox es tu voz real más la voz traducida.

Windows no deja que un programa meta sonido en un micrófono físico: hace falta un micrófono virtual (VB-Audio
Virtual Cable, gratis). Bubble pasa tu micrófono real al virtual en vivo (~20 ms de demora) y la voz traducida se
suma ahí. En Roblox se elige una sola vez «CABLE Output» como micrófono.

    tu micrófono ──► Bubble ──► CABLE Input ══► CABLE Output ──► Roblox
    voz traducida ──────────────┘
"""

from __future__ import annotations

import logging
import re
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from .models import Progress, models_dir

log = logging.getLogger(__name__)
RATE = 48000
BLOCK = 480  # 10 ms
CABLE_PAGE = "https://vb-audio.com/Cable/"
CABLE_FALLBACK = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
VIRTUAL = ("cable", "vb-audio", "voicemeeter")  # dispositivos virtuales: no son tu micrófono


def _sc():
    import warnings

    import soundcard

    warnings.filterwarnings("ignore", message="data discontinuity in recording")
    return soundcard


def microphones() -> list[str]:
    """Tus micrófonos de verdad (sin los virtuales), el predeterminado primero."""
    sc = _sc()
    names = [m.name for m in sc.all_microphones() if not any(v in m.name.lower() for v in VIRTUAL)]
    default = sc.default_microphone().name
    return sorted(names, key=lambda name: name != default)


def cable_input():
    """La entrada del micrófono virtual (donde se reproduce lo que Roblox va a escuchar), o None."""
    for speaker in _sc().all_speakers():
        name = speaker.name.lower()
        if "cable input" in name or ("voicemeeter" in name and "input" in name):
            return speaker
    return None


def _microphone(name: str):
    sc = _sc()
    if name:
        for mic in sc.all_microphones():
            if mic.name == name:
                return mic
    return sc.default_microphone()


class MicBridge:
    """Pasa tu micrófono real al micrófono virtual, todo el tiempo. Mientras suena la voz traducida, tu voz real
    baja (si no, se escucharían las dos encima)."""

    def __init__(self, mic_name: str = "", duck: float = 0.15) -> None:
        self.mic_name = mic_name
        self.duck_level = duck
        self.enabled = True  # pasar tu voz real (si no, solo se escucha la traducida)
        self.error = ""
        self._duck_until = 0.0
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> bool:
        if self.running:
            return True
        if cable_input() is None:
            self.error = "No hay micrófono virtual instalado"
            return False
        self._running.set()
        self._thread = threading.Thread(target=self._run, name="bubble-microfono", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running.clear()

    def duck(self, seconds: float) -> None:
        self._duck_until = max(self._duck_until, time.monotonic() + seconds)

    def _run(self) -> None:
        try:
            cable = cable_input()
            mic = _microphone(self.mic_name)
            with mic.recorder(RATE, channels=1, blocksize=BLOCK * 8) as recorder, \
                    cable.player(RATE, channels=1, blocksize=BLOCK * 4) as player:
                while self.running:
                    block = np.asarray(recorder.record(numframes=BLOCK), dtype=np.float32)
                    if block.ndim == 2:
                        block = block.mean(axis=1)
                    if not self.enabled:
                        block = np.zeros_like(block)
                    elif time.monotonic() < self._duck_until:
                        block = block * self.duck_level
                    player.play(block)
        except Exception as exc:  # noqa: BLE001 - se muestra en la ventana
            log.exception("Falló el puente del micrófono")
            self.error = str(exc)
            self._running.clear()


# ---------------------------------------------------------------- instalar el micrófono virtual
def _driver_url() -> str:
    try:
        with urllib.request.urlopen(urllib.request.Request(CABLE_PAGE, headers={"User-Agent": "Bubble"}),
                                    timeout=20) as response:
            page = response.read().decode("utf-8", "ignore")
        links = sorted(set(re.findall(r"https?://[^\"']*VBCABLE_Driver_Pack(\d+)\.zip", page)), key=int)
        if links:
            return f"https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack{links[-1]}.zip"
    except OSError:
        log.debug("No se pudo leer la página de VB-Cable", exc_info=True)
    return CABLE_FALLBACK


def install_cable(progress: Progress | None = None) -> str:
    """Baja el instalador oficial de VB-Audio Virtual Cable y lo abre (Windows pide permiso de administrador y hay
    que tocar «Install Driver»). Devuelve un mensaje para mostrar."""
    import ctypes

    from .models import download

    folder = models_dir().parent / "descargas" / "vbcable"
    url = _driver_url()
    archive = download(url, folder / Path(url).name, "micrófono virtual (VB-Cable)", progress)
    with zipfile.ZipFile(archive) as pack:
        pack.extractall(folder)
    setup = folder / "VBCABLE_Setup_x64.exe"
    if not setup.exists():
        return "No se encontró el instalador dentro del paquete descargado."
    # "runas": el instalador de controladores necesita permiso de administrador (Windows lo pregunta).
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(setup), None, str(folder), 1)
    if result <= 32:
        return "No se abrió el instalador (¿se canceló el permiso de administrador?)."
    return ("Se abrió el instalador: tocá «Install Driver». Cuando termine, reiniciá la PC si te lo pide y "
            "volvé a abrir Bubble.")
