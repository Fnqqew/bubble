"""Subtítulos de voz sobre el juego: abajo al centro, como en una película.

Cada frase muestra la traducción grande y, arriba, el original chico con su idioma. Quedan las últimas dos y se van
solas a los pocos segundos. Como las demás traducciones, no salen en capturas de pantalla ni reciben clics.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PIL import Image, ImageDraw

from .inline import SUPERSAMPLE, _font, layout_text, Slot

WIDTH = 760
SHOW_S = 7.0
FILL = (14, 16, 20, 225)
TEXT = (246, 247, 250, 255)
ORIGINAL = (150, 156, 168, 255)
ACCENT = (139, 226, 139, 255)  # verde: voz (el chat usa azul)


@dataclass
class _Line:
    original: str
    translation: str
    language: str
    shown_at: float


def render_subtitles(lines: list[_Line]) -> Image.Image:
    """Tarjeta con las frases (la más nueva abajo)."""
    blocks = []
    for line in lines:
        size, wrapped = layout_text(line.translation, [Slot(0, 0, WIDTH - 60, 30)] * 2, 22)
        blocks.append((line, size, [w for w in wrapped if w]))
    height = 18 + sum(22 + 30 * len(wrapped) + 10 for _line, _size, wrapped in blocks)
    big = Image.new("RGBA", (WIDTH * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), 16 * SUPERSAMPLE, fill=FILL)
    image = big.resize((WIDTH, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    y = 12
    for line, size, wrapped in blocks:
        draw.ellipse((22, y + 6, 30, y + 14), fill=ACCENT)
        draw.text((38, y), f"{line.language.upper()} · {line.original}"[:110], font=_font(13), fill=ORIGINAL)
        y += 22
        for text in wrapped:
            draw.text((WIDTH / 2, y + 14), text, font=_font(size), fill=TEXT, anchor="mm")
            y += 30
        y += 10
    return image


class SubtitleView:
    def __init__(self) -> None:
        from ..layered import LayeredWindow

        self.window = LayeredWindow()
        self.lines: list[_Line] = []
        self._image: Image.Image | None = None
        self._drawn: Image.Image | None = None  # la imagen que tiene la ventana (si no cambió, solo se mueve)

    def add(self, original: str, translation: str, language: str) -> None:
        self.lines = (self.lines + [_Line(original, translation, language, time.monotonic())])[-2:]
        self._image = render_subtitles(self.lines)

    def update(self, area, visible: bool) -> None:
        """Llamar seguido (desde el bucle de la ventana): ubica, oculta y hace que se vayan solos."""
        now = time.monotonic()
        alive = [line for line in self.lines if now - line.shown_at < SHOW_S]
        if len(alive) != len(self.lines):
            self.lines = alive
            self._image = render_subtitles(alive) if alive else None
        if not visible or area is None or self._image is None:
            self.window.hide()
            return
        x = area.left + (area.width - self._image.width) // 2
        y = area.bottom - self._image.height - int(area.height * 0.16)
        if self._image is not self._drawn:
            self.window.update(self._image, x, y)
            self._drawn = self._image
        else:
            self.window.move(x, y)
