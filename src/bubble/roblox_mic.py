"""Tu micrófono en Roblox: si estás muteado, Bubble te desmutea mientras suena tu voz traducida y te vuelve a mutear.

El botón del micrófono está en la barra de arriba a la izquierda de Roblox:
- muteado: el micrófono con una raya roja en diagonal;
- activo: el micrófono blanco con la base verde (y un puntito rojo).
Se reconoce por esos colores en una captura y se cambia con un clic en el botón (el mouse vuelve enseguida a donde
estaba). Nada de esto toca el proceso de Roblox: es lo mismo que hacer el clic vos.
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass
from ctypes import wintypes

import numpy as np
from PIL import Image

from . import win32
from .geometry import Rect

log = logging.getLogger(__name__)
TOP_BAR_WIDTH = 0.3  # parte del ancho de la ventana donde se busca el botón
TOP_BAR_HEIGHT = 0.1
MIN_SLASH = 25  # píxeles rojos de la raya (el puntito rojo del micrófono activo son ~10)
MIN_GREEN = 12


@dataclass
class MicState:
    muted: bool
    x: int  # centro del botón, relativo a la imagen
    y: int


def top_bar(width: int, height: int) -> tuple[int, int]:
    """Ancho y alto de la zona de arriba a la izquierda donde está el botón, para una ventana de ese tamaño."""
    return max(1, int(width * TOP_BAR_WIDTH)), max(40, int(height * TOP_BAR_HEIGHT))


def find_mic(image: Image.Image) -> MicState | None:
    """Estado del micrófono de Roblox en una captura de su ventana entera. None si no está."""
    return find_mic_in_bar(image.convert("RGB").crop((0, 0, *top_bar(*image.size))))


def find_mic_in_bar(image: Image.Image) -> MicState | None:
    """Lo mismo, en la zona de arriba a la izquierda ya recortada."""
    bar = np.asarray(image.convert("RGB"))
    r, g, b = (bar[..., i].astype(np.int16) for i in range(3))
    red = (r > 170) & (g < 110) & (b < 120) & (r - g > 80)
    green = (g > 130) & (r < 110) & (b < 150) & (g - r > 50)
    for ys, xs in _blobs(red, MIN_SLASH):
        # La raya es diagonal: ocupa un cuadrado, no una línea horizontal (la bandera de "Servidores", un texto rojo).
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if 12 <= w <= 80 and 12 <= h <= 80 and 0.5 <= w / h <= 2.0 and len(xs) <= 0.45 * w * h:
            return MicState(True, int(xs.mean()), int(ys.mean()))
    for ys, xs in _blobs(green, MIN_GREEN):
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if 8 <= w <= 60 and h <= w:
            # La base verde está abajo del micrófono: el centro del botón queda un poco más arriba.
            return MicState(False, int(xs.mean()), int(ys.mean()) - int(max(6, 1.2 * h)))
    return None


def _blobs(mask: np.ndarray, minimum: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Manchas separadas de la máscara (de más grande a más chica), con al menos `minimum` píxeles."""
    from scipy import ndimage

    labels, count = ndimage.label(mask, structure=np.ones((3, 3)))
    found = [np.nonzero(labels == index) for index in range(1, count + 1)]
    return sorted((f for f in found if len(f[0]) >= minimum), key=lambda f: -len(f[0]))


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("pad", ctypes.c_byte * 32)]

    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


MOVE, LEFT_DOWN, LEFT_UP, ABSOLUTE, VIRTUAL_DESK = 0x0001, 0x0002, 0x0004, 0x8000, 0x4000


def _send_mouse(flags: int, x: int = 0, y: int = 0) -> None:
    event = _INPUT()
    event.type = 0  # INPUT_MOUSE
    event.u.mi = _MOUSEINPUT(x, y, 0, flags, 0, 0)
    ctypes.windll.user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(_INPUT))


def _move(x: int, y: int) -> None:
    """Mueve el mouse como un mouse de verdad (con evento de movimiento): Roblox sigue el mouse por esos eventos, y si
    el cursor solo "aparecía" en el botón, el clic le llegaba donde creía que estaba antes."""
    metrics = ctypes.windll.user32.GetSystemMetrics
    left, top, width, height = metrics(76), metrics(77), metrics(78), metrics(79)  # el escritorio entero
    nx = round((x - left) * 65535 / max(1, width - 1))
    ny = round((y - top) * 65535 / max(1, height - 1))
    _send_mouse(MOVE | ABSOLUTE | VIRTUAL_DESK, nx, ny)


def _click(x: int, y: int) -> None:
    """Clic en (x, y) de la pantalla y el mouse vuelve a donde estaba."""
    before = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(before))
    _move(x + 4, y + 3)  # llega al botón y se apoya encima (Roblox resalta el botón antes de aceptar el clic)
    time.sleep(0.03)
    _move(x, y)
    time.sleep(0.06)
    _send_mouse(LEFT_DOWN)
    time.sleep(0.05)
    _send_mouse(LEFT_UP)
    time.sleep(0.05)
    _move(before.x, before.y)


class RobloxMic:
    """Desmutea para una frase y vuelve a mutear. Si no encuentra el botón o Roblox no está al frente, no hace nada."""

    WAIT_AFTER_UNMUTE_S = 0.35  # Roblox tarda un momento en empezar a mandar tu voz

    def __init__(self, grab=None) -> None:
        if grab is None:
            from .capture.screen import grab
        self.grab = grab
        self.last_error = ""

    def _look(self) -> tuple[MicState, Rect] | None:
        hwnd = win32.find_roblox_window()
        if not hwnd:
            return None
        if not win32.roblox_is_foreground():
            # Con Ctrl+Enter desde la barra de Bubble, Roblox no está al frente: el clic tiene que llegarle a él.
            win32.force_foreground(hwnd)
            time.sleep(0.2)
        client = win32.client_rect(hwnd)
        top = Rect(client.left, client.top, *top_bar(client.width, client.height))
        state = find_mic_in_bar(self.grab(top))
        if state is None:
            return None
        return state, top

    def unmute(self) -> bool:
        """True si estabas muteado y te desmuteó (entonces hay que llamar a `mute_again`)."""
        seen = self._look()
        if seen is None or not seen[0].muted:
            log.info("Micrófono de Roblox: %s", "no se vio el botón" if seen is None else "ya estaba activo")
            return False
        state, top = seen
        log.info("Micrófono de Roblox muteado (botón en %s, %s): se desmutea para la frase",
                 top.left + state.x, top.top + state.y)
        _click(top.left + state.x, top.top + state.y)
        time.sleep(0.35)  # Roblox cambia el ícono un momento después
        after = self._look()
        if after is not None and after[0].muted:
            self.last_error = "No pude desmutearte en Roblox: desmuteate a mano"
            log.info("El clic no desmuteó (¿el mouse está trabado en el juego?)")
            return False
        self._spot = (top.left + state.x, top.top + state.y)
        time.sleep(self.WAIT_AFTER_UNMUTE_S)
        return True

    def mute_again(self) -> None:
        seen = self._look()
        if seen is not None and not seen[0].muted:
            state, top = seen
            _click(top.left + state.x, top.top + state.y)
        elif seen is None and getattr(self, "_spot", None):
            _click(*self._spot)  # no se vio el botón (se movió la cámara, un menú...): donde estaba
