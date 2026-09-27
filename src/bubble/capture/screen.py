"""Captura de una región de la pantalla.

Si la PC lo permite se captura con la GPU (DXGI Desktop Duplication: funciona con placas AMD, NVIDIA e Intel,
integradas o dedicadas). Casi no usa CPU: en 1080p la captura baja de ~50 ms a ~3 ms. Las ventanas de Bubble
marcadas como invisibles para capturas tampoco salen acá. Si la GPU no está disponible (escritorio remoto, pantalla
rotada, un error del driver), se usa GDI (mss) como antes, sin que el resto del programa se entere.
"""

from __future__ import annotations

import logging
import threading
import time

import mss
import numpy as np
from PIL import Image

from ..geometry import Rect

log = logging.getLogger(__name__)
_local = threading.local()
# Mientras sacás una captura de pantalla, las traducciones se ven en las capturas (ver screenshots.py): en ese
# momento no se lee la pantalla (el OCR leería las traducciones en vez del chat).
_reading = {"paused": False, "mark": 0}


def pause_reading(paused: bool) -> None:
    _reading["paused"] = paused
    _reading["mark"] += 1


def reading_mark() -> int | None:
    """None si ahora no se puede leer la pantalla; si no, una marca para `still_readable`."""
    return None if _reading["paused"] else _reading["mark"]


def still_readable(mark: int) -> bool:
    """False si mientras se capturaba se empezó (o terminó) a sacar una captura: esa imagen no sirve."""
    return not _reading["paused"] and _reading["mark"] == mark


def _grab_gdi(rect: Rect) -> Image.Image:
    # mss no es seguro entre hilos: una instancia por hilo.
    if not hasattr(_local, "sct"):
        _local.sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    shot = _local.sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


class GpuScreen:
    """Captura por GPU de cada monitor. Guarda el último cuadro completo de cada uno: la GPU solo entrega un
    cuadro cuando algo cambió en pantalla, y el chat y las burbujas recortan su zona del mismo cuadro."""

    RETRY_AFTER_S = 5.0

    def __init__(self) -> None:
        import dxcam

        self._dxcam = dxcam
        factory = vars(dxcam).get("__factory") or dxcam.DXFactory()
        self.adapters = [device.description for device in factory.devices]
        self._outputs: list[tuple[int, int, Rect]] = []
        for device_idx, outputs in enumerate(factory.outputs):
            for output_idx, output in enumerate(outputs):
                if output.rotation_angle:
                    continue  # pantalla rotada: la imagen viene girada, mejor GDI
                c = output.desc.DesktopCoordinates
                self._outputs.append((device_idx, output_idx, Rect.from_points(c.left, c.top, c.right, c.bottom)))
        if not self._outputs:
            raise RuntimeError("sin monitores para capturar por GPU")
        self._cameras: dict[tuple[int, int], object] = {}
        self._frames: dict[tuple[int, int], np.ndarray] = {}
        self._broken_until: dict[tuple[int, int], float] = {}
        self._lock = threading.Lock()

    def _output_for(self, rect: Rect) -> tuple[tuple[int, int], Rect] | None:
        for device_idx, output_idx, area in self._outputs:
            if area.left <= rect.left and area.top <= rect.top and rect.right <= area.right and rect.bottom <= area.bottom:
                return (device_idx, output_idx), area
        return None  # la zona cruza dos monitores (o está fuera): GDI

    def grab(self, rect: Rect) -> Image.Image | None:
        found = self._output_for(rect)
        if found is None:
            return None
        key, area = found
        with self._lock:
            if time.monotonic() < self._broken_until.get(key, 0.0):
                return None
            try:
                camera = self._cameras.get(key)
                if camera is None:
                    camera = self._dxcam.create(device_idx=key[0], output_idx=key[1], output_color="BGRA",
                                                processor_backend="numpy")
                    self._cameras[key] = camera
                frame = camera.grab(new_frame_only=True)
                if frame is not None:
                    self._frames[key] = frame
                frame = self._frames.get(key)
                if frame is None:
                    # Todavía no hubo ningún cuadro (la pantalla está quieta desde que se abrió la captura).
                    frame = camera.grab(new_frame_only=False)
                    if frame is None:
                        return None
                    self._frames[key] = frame
            except Exception:  # noqa: BLE001 - cambio de resolución, driver reiniciado...: GDI por un rato
                log.warning("La captura por GPU falló; se usa la captura normal por unos segundos", exc_info=True)
                self._cameras.pop(key, None)
                self._frames.pop(key, None)
                self._broken_until[key] = time.monotonic() + self.RETRY_AFTER_S
                return None
            x0, y0 = rect.left - area.left, rect.top - area.top
            part = np.ascontiguousarray(frame[y0:y0 + rect.height, x0:x0 + rect.width])
        return Image.frombuffer("RGB", (rect.width, rect.height), part, "raw", "BGRX", 0, 1)


_gpu: GpuScreen | None = None
_gpu_state = {"tried": False, "enabled": True, "error": ""}
_gpu_lock = threading.Lock()


def set_gpu_capture(enabled: bool) -> None:
    _gpu_state["enabled"] = enabled


def gpu_screen() -> GpuScreen | None:
    """La captura por GPU, si esta PC la tiene (se prueba una sola vez)."""
    global _gpu
    if not _gpu_state["enabled"]:
        return None
    if not _gpu_state["tried"]:
        with _gpu_lock:
            if not _gpu_state["tried"]:
                try:
                    _gpu = GpuScreen()
                except Exception as exc:  # noqa: BLE001 - sin GPU utilizable: GDI
                    _gpu_state["error"] = str(exc) or type(exc).__name__
                    log.info("Captura por GPU no disponible: %s", _gpu_state["error"])
                _gpu_state["tried"] = True
    return _gpu


def capture_backend() -> str:
    return "gpu" if gpu_screen() is not None else "cpu"


def grab(rect: Rect) -> Image.Image:
    gpu = gpu_screen()
    if gpu is not None:
        image = gpu.grab(rect)
        if image is not None:
            return image
    return _grab_gdi(rect)
