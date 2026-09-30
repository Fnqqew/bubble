"""Imagen de la ventana de Roblox por sí sola, sin lo que tenga encima (las traducciones de Bubble u otras ventanas).

Bubble lee el chat y las burbujas a partir de una captura de la pantalla. Si las traducciones aparecieran en esa
captura, se leerían a sí mismas en lugar del chat original, por lo que se ocultaban de todas las capturas, incluidas las
capturas de pantalla y grabaciones del jugador (OBS, Xbox Game Bar, AMD/NVIDIA, la grabadora de Roblox).

Con la imagen de la ventana de Roblox por sí sola (PrintWindow con PW_RENDERFULLCONTENT: Windows la compone a partir de
lo que Roblox dibujó, aunque haya otras ventanas encima), las traducciones pueden verse en todas las capturas y
grabaciones, y el lector sigue viendo el chat original. Antes de usarla se comprueba que en este equipo el resultado sea
correcto (idéntico a lo que se ve en pantalla, no negro); si no lo es, se recurre a la captura de pantalla.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from ctypes import wintypes

import numpy as np
from PIL import Image

from ..geometry import Rect

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
PW_CLIENTONLY, PW_RENDERFULLCONTENT = 0x1, 0x2
FRAME_MAX_AGE_S = 0.04  # el chat y las burbujas reutilizan la misma imagen si se piden juntos


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def window_image(hwnd: int) -> np.ndarray | None:
    """Interior de la ventana (BGRA, alto × ancho × 4), o None si Windows no pudo componerlo."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    width, height = rect.right, rect.bottom
    if width <= 0 or height <= 0:
        return None
    window_dc = user32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    memory = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    try:
        gdi32.SelectObject(memory, bitmap)
        if not user32.PrintWindow(hwnd, memory, PW_CLIENTONLY | PW_RENDERFULLCONTENT):
            return None
        buffer = ctypes.create_string_buffer(width * height * 4)
        header = _BITMAPINFOHEADER(ctypes.sizeof(_BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        if not gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0):
            return None
        return np.frombuffer(buffer, np.uint8).reshape(height, width, 4)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, window_dc)


def looks_blank(pixels: np.ndarray) -> bool:
    """Indica si la imagen es negra o de un solo color, es decir, si Windows no pudo componer la ventana."""
    sample = pixels[::8, ::8, :3]
    return float(sample.std()) < 2.0


def similar(a: Image.Image, b: Image.Image) -> bool:
    """Indica si ambas imágenes coinciden (lo que Windows compone de la ventana y lo que se ve en pantalla, sin Bubble
    encima).
    """
    if a.size != b.size:
        return False
    first = np.asarray(a.convert("L"), dtype=np.int16)[::4, ::4]
    second = np.asarray(b.convert("L"), dtype=np.int16)[::4, ::4]
    return float(np.mean(np.abs(first - second))) < 12.0


class WindowCapture:
    """Capturas de la ventana de Roblox, recortadas a la región solicitada (en coordenadas de pantalla)."""

    def __init__(self, find_window=None) -> None:
        from .. import win32

        self.find_window = find_window or win32.find_roblox_window
        self._lock = threading.Lock()
        self._hwnd: int | None = None
        self._hwnd_at = 0.0
        self._frame: np.ndarray | None = None
        self._frame_at = 0.0
        self._origin = (0, 0)

    def _window(self) -> int | None:
        now = time.monotonic()
        if self._hwnd is None or now - self._hwnd_at > 1.0 or not user32.IsWindow(self._hwnd):
            self._hwnd, self._hwnd_at = self.find_window(), now
        return self._hwnd

    def grab(self, rect: Rect) -> Image.Image | None:
        """Región `rect` de la ventana de Roblox, o None si Roblox está cerrado o minimizado, o si Windows no pudo
        componer la imagen.
        """
        from .. import win32

        with self._lock:
            hwnd = self._window()
            if not hwnd or user32.IsIconic(hwnd):
                return None
            now = time.monotonic()
            if self._frame is None or now - self._frame_at > FRAME_MAX_AGE_S:
                client = win32.client_rect(hwnd)
                frame = window_image(hwnd)
                if frame is None or looks_blank(frame):
                    self._frame = None
                    return None
                self._frame, self._frame_at, self._origin = frame, now, (client.left, client.top)
            frame, (left, top) = self._frame, self._origin
        x0, y0 = rect.left - left, rect.top - top
        height, width = frame.shape[:2]
        if x0 < 0 or y0 < 0 or x0 + rect.width > width or y0 + rect.height > height:
            return None  # la región pedida no está completa dentro de Roblox
        part = frame[y0:y0 + rect.height, x0:x0 + rect.width]
        return Image.frombuffer("RGBA", (rect.width, rect.height), np.ascontiguousarray(part), "raw", "BGRA", 0,
                                1).convert("RGB")

    def whole(self) -> Image.Image | None:
        """Ventana de Roblox completa (para adjuntarla a un mensaje de Soporte), o None."""
        from .. import win32

        hwnd = self.find_window()
        if not hwnd or user32.IsIconic(hwnd):
            return None
        client = win32.client_rect(hwnd)
        return self.grab(Rect(client.left, client.top, client.width, client.height))
