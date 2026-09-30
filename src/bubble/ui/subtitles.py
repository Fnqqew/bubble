"""Subtítulos de voz sobre el juego: abajo al centro, como en una película.

Cada frase dice quién habla (Voz 1, Voz 2…, cada una con su color) y en qué idioma. Mientras la persona habla se
ve lo que va diciendo, en gris; apenas llega la traducción la reemplaza, en blanco. Quedan las últimas frases y se
van solas. Como las demás traducciones, no salen en capturas de pantalla ni reciben clics.
"""

from __future__ import annotations

import time

from PIL import Image, ImageDraw

from ..voice.captions import MINE, NOTICE, Line
from .inline import SUPERSAMPLE, Slot, _font, layout_text
from ..i18n import t
from .rtl import visual

WIDTH = 760
FILL = (14, 16, 20, 225)
TEXT = (246, 247, 250, 255)
PENDING = (150, 156, 168, 255)
MUTED = (140, 146, 158, 255)
SPEAKER_COLORS = [(139, 226, 139), (120, 190, 255), (255, 176, 96), (236, 136, 222), (255, 222, 96),
                  (110, 226, 214), (255, 128, 128), (186, 160, 255)]
BODY = 22
LINE_H = 30


class Settings:
    """Ajustes de los subtítulos (se cambian desde la ventana)."""

    scale = 1.0
    position = "abajo"  # "abajo" | "arriba"
    show_original = True
    version = 0


SETTINGS = Settings()


def set_subtitles(scale: float = 1.0, position: str = "abajo", show_original: bool = True) -> None:
    SETTINGS.scale = max(0.8, min(1.5, scale))
    SETTINGS.position = position
    SETTINGS.show_original = show_original
    SETTINGS.version += 1


def speaker_color(number: int) -> tuple[int, int, int]:
    if number == MINE:
        return (255, 255, 255)
    return SPEAKER_COLORS[(number - 1) % len(SPEAKER_COLORS)] if number > 0 else (200, 204, 212)


def speaker_name(number: int) -> str:
    if number == MINE:
        return t("Vos")
    if number == NOTICE:
        return "Bubble"
    return t(f"Voz {number}") if number > 0 else t("Voz")


def _body(line: Line) -> tuple[str, tuple]:
    if line.translation.strip():
        return line.translation.strip(), TEXT
    return (line.original + ("" if line.final else " …")).strip(), PENDING


FRESH_S = 0.28  # una frase nueva aparece deslizándose y desvaneciéndose durante este tiempo


def freshness(lines: list[Line], now: float) -> dict[int, float]:
    """Frases que recién aparecen → cuánto de su animación ya pasó (0 a 1, suavizado)."""
    fresh = {}
    for line in lines:
        if line.heard_at and now - line.heard_at < FRESH_S:
            t = max(0.0, (now - line.heard_at) / FRESH_S)
            fresh[line.id] = round(1 - (1 - t) ** 3, 2)
    return fresh


_corners: dict[tuple, Image.Image] = {}


def card(width: int, height: int, radius: int, fill: tuple) -> Image.Image:
    """Tarjeta redondeada de cualquier tamaño. Las esquinas suaves se dibujan una sola vez (grandes y achicadas) y el
    resto se pinta directo: ~1 ms. Antes se dibujaba toda la tarjeta al triple y se achicaba en cada cuadro (~45 ms),
    y con subtítulos que cambian seguido eso trababa a Bubble y le sacaba procesador al juego."""
    radius = max(1, min(radius, width // 2, height // 2))
    key = (radius, fill)
    if key not in _corners:
        big = Image.new("RGBA", (2 * radius * SUPERSAMPLE,) * 2, (0, 0, 0, 0))
        ImageDraw.Draw(big).rounded_rectangle((0, 0, big.width - 1, big.height - 1), radius * SUPERSAMPLE, fill=fill)
        _corners[key] = big.resize((2 * radius, 2 * radius), Image.Resampling.LANCZOS)
    corners = _corners[key]
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    image.paste(fill, (radius, 0, width - radius, height))
    image.paste(fill, (0, radius, width, height - radius))
    r = radius
    image.paste(corners.crop((0, 0, r, r)), (0, 0))
    image.paste(corners.crop((r, 0, 2 * r, r)), (width - r, 0))
    image.paste(corners.crop((0, r, r, 2 * r)), (0, height - r))
    image.paste(corners.crop((r, r, 2 * r, 2 * r)), (width - r, height - r))
    return image


MAX_LINES = 6  # renglones por frase: antes eran 2 y lo que no entraba se cortaba con "…" (charlas largas)
MAX_HEIGHT = 0.42  # parte del alto del juego que pueden ocupar los subtítulos


def fit_lines(text: str, width: int, line_h: int, size: int) -> tuple[int, list[str]]:
    """El texto en los renglones que hagan falta (hasta MAX_LINES), con la letra casi del mismo tamaño: primero se
    agregan renglones y recién al final se achica la letra."""
    for count in range(2, MAX_LINES + 1):
        font_size, wrapped = layout_text(text, [Slot(0, 0, width, line_h)] * count, size)
        if font_size >= int(size * 0.9) and not (wrapped and wrapped[-1].endswith("…")):
            return font_size, wrapped
    return layout_text(text, [Slot(0, 0, width, line_h)] * MAX_LINES, size)


def render_subtitles(lines: list[Line], scale: float | None = None, show_original: bool | None = None,
                     fresh: dict[int, float] | None = None) -> Image.Image:
    """Tarjeta con las frases (la más nueva abajo). `fresh`: frases que están apareciendo (ver `freshness`)."""
    fresh = fresh or {}
    k = SETTINGS.scale if scale is None else scale
    original_too = SETTINGS.show_original if show_original is None else show_original
    width, line_h, body, head = int(WIDTH * k), int(LINE_H * k), int(BODY * k), int(24 * k)
    blocks = []
    for line in lines:
        text, color = _body(line)
        size, wrapped = fit_lines(text, width - int(60 * k), line_h, body)
        blocks.append((line, text, color, size, [w for w in wrapped if w] or [text[:40]]))
    height = int(14 * k) + sum(head + line_h * len(wrapped) + int(10 * k) for *_rest, wrapped in blocks)
    image = card(width, height, int(16 * k), FILL)
    draw = ImageDraw.Draw(image)
    y = int(12 * k)
    label_font = _font(int(14 * k))
    base = image
    for line, _text, color, size, wrapped in blocks:
        appearing = fresh.get(line.id, 1.0)
        top = y
        if appearing < 1.0:
            # La frase nueva se dibuja aparte y entra subiendo un poco y desvaneciéndose.
            image = Image.new("RGBA", base.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            y += int(10 * k * (1 - appearing))
        accent = speaker_color(line.speaker)
        dot = int(22 * k)
        draw.ellipse((dot, y + int(5 * k), dot + int(9 * k), y + int(14 * k)), fill=(*accent, 255))
        name = speaker_name(line.speaker)
        name_x = int(39 * k)
        draw.text((name_x, y + int(10 * k)), name, font=label_font, fill=(*accent, 255), anchor="lm")
        x = name_x + label_font.getlength(name) + 8
        details = f"· {line.language.upper()}" if line.language and line.language != "→" else ""
        if line.translation.strip() and original_too:
            details += f"   {line.original}"
        details_font = _font(int(13 * k), details)
        room = width - x - int(22 * k)
        if details_font.getlength(details) > room:  # el original, entero si entra; si no, hasta donde entra, con "…"
            while details and details_font.getlength(details + "…") > room:
                details = details[:-1]
            details = details.rstrip() + "…"
        draw.text((x, y + int(10 * k)), visual(details), font=details_font, fill=MUTED, anchor="lm")
        y += head
        for text in wrapped:
            draw.text((width / 2, y + line_h / 2), visual(text), font=_font(size, text), fill=color, anchor="mm")
            y += line_h
        y += int(10 * k)
        if appearing < 1.0:
            layer = image
            layer.putalpha(layer.getchannel("A").point(lambda value: int(value * appearing)))
            base.alpha_composite(layer)
            image, draw = base, ImageDraw.Draw(base)
            y = top + head + line_h * len(wrapped) + int(10 * k)
    return base


class SubtitleView:
    def __init__(self) -> None:
        from ..layered import LayeredWindow

        self.window = LayeredWindow()
        self._key: tuple | None = None
        self._image: Image.Image | None = None
        self.animating = False  # hay una frase apareciendo: conviene llamar más seguido

    def update(self, lines: list[Line], area, visible: bool) -> None:
        """Llamar seguido (desde el bucle de la ventana) con las frases a la vista."""
        if not visible or area is None or not lines:
            self.window.hide()
            self._key = None
            return
        fresh = freshness(lines, time.monotonic())
        self.animating = bool(fresh)
        key = (SETTINGS.version, tuple(sorted(fresh.items())),
               *((line.id, line.speaker, line.language, line.original, line.translation, line.final)
                 for line in lines))
        if key != self._key:
            self._image = render_subtitles(lines, fresh=fresh)
            # Frases largas: la tarjeta no tapa más que MAX_HEIGHT del juego (se van primero las más viejas).
            shown = list(lines)
            while len(shown) > 1 and self._image.height > area.height * MAX_HEIGHT:
                shown = shown[1:]
                self._image = render_subtitles(shown, fresh=fresh)
            self._key = key
            drawn = True
        else:
            drawn = False
        x = area.left + (area.width - self._image.width) // 2
        if SETTINGS.position == "arriba":
            y = area.top + int(area.height * 0.12)
        else:
            y = area.bottom - self._image.height - int(area.height * 0.16)
        if drawn:
            self.window.update(self._image, x, y)
        else:
            self.window.move(x, y)
