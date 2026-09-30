"""Captura de una región de la pantalla.

Si el equipo lo permite, se captura con la GPU (DXGI Desktop Duplication, compatible con placas AMD, NVIDIA e Intel,
integradas o dedicadas). Casi no usa CPU: en 1080p la captura baja de ~50 ms a ~3 ms. Las ventanas de Bubble marcadas
como invisibles para capturas tampoco aparecen. Si la GPU no está disponible (escritorio remoto, pantalla rotada, error
del driver), se usa GDI (mss) sin que el resto del programa lo note.
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
# Mientras el jugador toma una captura de pantalla, las traducciones son visibles en ella (ver screenshots.py), por lo
# que no se lee la pantalla: el OCR leería las traducciones en lugar del chat.
_reading = {"paused": False, "mark": 0}


def pause_reading(paused: bool) -> None:
    _reading["paused"] = paused
    _reading["mark"] += 1


def reading_mark() -> int | None:
    """None si en este momento no se puede leer la pantalla; en caso contrario, una marca para `still_readable`."""
    return None if _reading["paused"] else _reading["mark"]


def still_readable(mark: int) -> bool:
    """False si durante la captura comenzó o terminó una captura de pantalla del jugador: la imagen no es válida."""
    return not _reading["paused"] and _reading["mark"] == mark


def _grab_gdi(rect: Rect) -> Image.Image:
    # mss no es seguro entre hilos: se usa una instancia por hilo.
    if not hasattr(_local, "sct"):
        _local.sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
    shot = _local.sct.grab({"left": rect.left, "top": rect.top, "width": rect.width, "height": rect.height})
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


class GpuScreen:
    """Captura por GPU de cada monitor. Conserva el último cuadro completo de cada uno: la GPU solo entrega un cuadro
    cuando cambia algo en pantalla, y el chat y las burbujas recortan su zona del mismo cuadro.
    """

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
                    continue  # pantalla rotada: la imagen viene girada, se usa GDI
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
        return None  # la zona cruza dos monitores o queda fuera: GDI

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
                    # Aún no hay ningún cuadro (la pantalla permanece quieta desde que se abrió la captura).
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
    """Captura por GPU, si el equipo la admite (se comprueba una sola vez)."""
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
    if _window["active"]:
        return "ventana"
    return "gpu" if gpu_screen() is not None else "cpu"


# ---------------------------------------------------------------- leer solo la ventana de Roblox
# Permite que las traducciones aparezcan en las capturas y grabaciones del jugador (ver window_capture.py). Se activa
# solo si en este equipo la imagen de la ventana coincide con la pantalla; si luego falla (p. ej. Roblox en pantalla
# completa exclusiva), las traducciones vuelven a ocultarse de las capturas y se lee la pantalla.
PROBE_EVERY_S = 5.0
FAILS_TO_GIVE_UP = 8
_window = {"wanted": False, "active": False, "capture": None, "probed_at": 0.0, "fails": 0,
           "on_change": None}


def set_window_capture(wanted: bool, on_change=None) -> None:
    """`on_change(activo)`: se invoca cuando empieza o deja de leerse la ventana sola (las traducciones pasan a ser
    visibles, o dejan de serlo, en las capturas).
    """
    _window["wanted"] = wanted
    if on_change is not None:
        _window["on_change"] = on_change
    if not wanted and _window["active"]:
        _set_window_active(False)


def window_mode() -> bool:
    """Indica si se está leyendo solo la ventana de Roblox (las traducciones aparecen en capturas y grabaciones)."""
    return bool(_window["active"])


def _set_window_active(active: bool) -> None:
    _window["active"], _window["fails"] = active, 0
    log.info("Lectura de la ventana de Roblox sola: %s", "sí" if active else "no")
    callback = _window["on_change"]
    if callback is not None:
        try:
            callback(active)
        except Exception:  # noqa: BLE001
            log.debug("Falló el aviso del modo ventana", exc_info=True)


def _window_capture():
    if _window["capture"] is None:
        from .window_capture import WindowCapture

        _window["capture"] = WindowCapture()
    return _window["capture"]


def _grab_screen(rect: Rect) -> Image.Image:
    gpu = gpu_screen()
    if gpu is not None:
        image = gpu.grab(rect)
        if image is not None:
            return image
    return _grab_gdi(rect)


def grab(rect: Rect) -> Image.Image:
    if _window["active"]:
        image = _window_capture().grab(rect)
        if image is not None:
            _window["fails"] = 0
            return image
        _window["fails"] += 1
        if _window["fails"] < FAILS_TO_GIVE_UP:
            return Image.new("RGB", (rect.width, rect.height))  # (transitorio: Roblox minimizado o cambiando)
        _set_window_active(False)  # no funciona en este equipo (o dejó de hacerlo): se ocultan de nuevo
        _window["probed_at"] = time.monotonic()
    screen = _grab_screen(rect)
    if _window["wanted"] and time.monotonic() - _window["probed_at"] > PROBE_EVERY_S:
        _window["probed_at"] = time.monotonic()
        try:
            from .window_capture import similar

            image = _window_capture().grab(rect)
            if image is not None and similar(image, screen):
                _set_window_active(True)
                return image
        except Exception:  # noqa: BLE001 - se sigue leyendo la pantalla
            log.debug("No se pudo probar la lectura de la ventana", exc_info=True)
    return screen
