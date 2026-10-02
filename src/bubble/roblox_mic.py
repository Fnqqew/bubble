"""Detecta si el micrófono está silenciado en Roblox, para avisar al jugador cuando habla con el micrófono apagado.
Bubble no lo activa ni lo desactiva: el jugador debe tenerlo activado para que lo escuchen.

El botón del micrófono está en la barra superior, a la izquierda de la ventana de Roblox: - silenciado: micrófono con
una raya roja diagonal; - activo: micrófono blanco con la base verde (y un punto rojo pequeño). Se distingue por esos
colores en una captura de la pantalla. Solo se observa: no hay clics ni teclas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import win32
from .geometry import Rect

log = logging.getLogger(__name__)
TOP_BAR_WIDTH = 0.3  # fracción del ancho de la ventana donde se busca el botón
TOP_BAR_HEIGHT = 0.1
MIN_SLASH = 25  # píxeles rojos de la raya (el punto rojo del micrófono activo tiene ~10)
MIN_GREEN = 12


@dataclass
class MicState:
    muted: bool
    x: int  # centro del botón, relativo a la imagen
    y: int


def top_bar(width: int, height: int) -> tuple[int, int]:
    """Ancho y alto de la zona superior izquierda donde está el botón, para una ventana de ese tamaño."""
    return max(1, int(width * TOP_BAR_WIDTH)), max(40, int(height * TOP_BAR_HEIGHT))


def find_mic(image: Image.Image) -> MicState | None:
    """Estado del micrófono de Roblox en una captura de su ventana completa. None si no se encuentra."""
    return find_mic_in_bar(image.convert("RGB").crop((0, 0, *top_bar(*image.size))))


def find_mic_in_bar(image: Image.Image) -> MicState | None:
    """Igual que find_mic, sobre la zona superior izquierda ya recortada."""
    bar = np.asarray(image.convert("RGB"))
    r, g, b = (bar[..., i].astype(np.int16) for i in range(3))
    red = (r > 170) & (g < 110) & (b < 120) & (r - g > 80)
    green = (g > 130) & (r < 110) & (b < 150) & (g - r > 50)
    for ys, xs in _blobs(red, MIN_SLASH):
        # La raya es diagonal: ocupa un cuadrado, no una línea horizontal (como la bandera de "Servidores", un texto
        # rojo).
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if 12 <= w <= 80 and 12 <= h <= 80 and 0.5 <= w / h <= 2.0 and len(xs) <= 0.45 * w * h:
            return MicState(True, int(xs.mean()), int(ys.mean()))
    for ys, xs in _blobs(green, MIN_GREEN):
        w, h = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
        if 8 <= w <= 60 and h <= w:
            # La base verde queda debajo del micrófono: el centro del botón está un poco más arriba.
            return MicState(False, int(xs.mean()), int(ys.mean()) - int(max(6, 1.2 * h)))
    return None


def _blobs(mask: np.ndarray, minimum: int) -> list[tuple[np.ndarray, np.ndarray]]:
    """Manchas separadas de la máscara (de mayor a menor), con al menos `minimum` píxeles."""
    from .capture.regions import label

    labels, count = label(mask, diagonal=True)
    found = [np.nonzero(labels == index) for index in range(1, count + 1)]
    return sorted((f for f in found if len(f[0]) >= minimum), key=lambda f: -len(f[0]))


def roblox_muted(grab=None) -> bool | None:
    """True si el micrófono está silenciado en Roblox; None si no se puede determinar (Roblox no está en primer plano o
    no se ve el botón).
    """
    hwnd = win32.find_roblox_window()
    if not hwnd or not win32.roblox_is_foreground():
        return None
    if grab is None:
        from .capture.screen import grab
    client = win32.client_rect(hwnd)
    top = Rect(client.left, client.top, *top_bar(client.width, client.height))
    state = find_mic_in_bar(grab(top))
    return None if state is None else state.muted
