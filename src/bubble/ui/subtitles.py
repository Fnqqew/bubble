"""Subtítulos de voz sobre el juego: abajo al centro, como en una película.

Cada frase dice quién habla (Voz 1, Voz 2…, cada una con su color) y en qué idioma. Mientras la persona habla se
ve lo que va diciendo, en gris; apenas llega la traducción la reemplaza, en blanco. Quedan las últimas frases y se
van solas. Como las demás traducciones, no salen en capturas de pantalla ni reciben clics.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from ..voice.captions import MINE, Line
from .inline import SUPERSAMPLE, Slot, _font, layout_text

WIDTH = 760
FILL = (14, 16, 20, 225)
TEXT = (246, 247, 250, 255)
PENDING = (150, 156, 168, 255)
MUTED = (140, 146, 158, 255)
SPEAKER_COLORS = [(139, 226, 139), (120, 190, 255), (255, 176, 96), (236, 136, 222), (255, 222, 96),
                  (110, 226, 214), (255, 128, 128), (186, 160, 255)]
BODY = 22
LINE_H = 30


def speaker_color(number: int) -> tuple[int, int, int]:
    if number == MINE:
        return (255, 255, 255)
    return SPEAKER_COLORS[(number - 1) % len(SPEAKER_COLORS)] if number > 0 else (200, 204, 212)


def speaker_name(number: int) -> str:
    if number == MINE:
        return "Vos"
    return f"Voz {number}" if number > 0 else "Voz"


def _body(line: Line) -> tuple[str, tuple]:
    if line.translation.strip():
        return line.translation.strip(), TEXT
    return (line.original + ("" if line.final else " …")).strip(), PENDING


def render_subtitles(lines: list[Line]) -> Image.Image:
    """Tarjeta con las frases (la más nueva abajo)."""
    blocks = []
    for line in lines:
        text, color = _body(line)
        size, wrapped = layout_text(text, [Slot(0, 0, WIDTH - 60, LINE_H)] * 2, BODY)
        blocks.append((line, text, color, size, [w for w in wrapped if w] or [text[:40]]))
    height = 14 + sum(24 + LINE_H * len(wrapped) + 10 for *_rest, wrapped in blocks)
    big = Image.new("RGBA", (WIDTH * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), 16 * SUPERSAMPLE, fill=FILL)
    image = big.resize((WIDTH, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    y = 12
    label_font = _font(14)
    for line, _text, color, size, wrapped in blocks:
        accent = speaker_color(line.speaker)
        draw.ellipse((22, y + 5, 31, y + 14), fill=(*accent, 255))
        name = speaker_name(line.speaker)
        draw.text((39, y + 10), name, font=label_font, fill=(*accent, 255), anchor="lm")
        x = 39 + label_font.getlength(name) + 8
        details = f"· {line.language.upper()}" if line.language else ""
        if line.translation.strip():
            details += f"   {line.original}"
        draw.text((x, y + 10), details[:120], font=_font(13, details), fill=MUTED, anchor="lm")
        y += 24
        for text in wrapped:
            draw.text((WIDTH / 2, y + LINE_H / 2), text, font=_font(size, text), fill=color, anchor="mm")
            y += LINE_H
        y += 10
    return image


class SubtitleView:
    def __init__(self) -> None:
        from ..layered import LayeredWindow

        self.window = LayeredWindow()
        self._key: tuple | None = None
        self._image: Image.Image | None = None

    def update(self, lines: list[Line], area, visible: bool) -> None:
        """Llamar seguido (desde el bucle de la ventana) con las frases a la vista."""
        if not visible or area is None or not lines:
            self.window.hide()
            self._key = None
            return
        key = tuple((line.id, line.speaker, line.language, line.original, line.translation, line.final)
                    for line in lines)
        if key != self._key:
            self._image = render_subtitles(lines)
            self._key = key
            drawn = True
        else:
            drawn = False
        x = area.left + (area.width - self._image.width) // 2
        y = area.bottom - self._image.height - int(area.height * 0.16)
        if drawn:
            self.window.update(self._image, x, y)
        else:
            self.window.move(x, y)
