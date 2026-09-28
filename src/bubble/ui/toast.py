"""Avisos cortos dentro del juego ("✦ Bubble Pro activado"): una pastilla arriba al centro que aparece, se queda un
momento y se desvanece. No toma el foco ni tapa los clics (es una ventana con transparencia real, ver layered.py)."""

from __future__ import annotations

from PIL import Image, ImageDraw

from .. import pro
from ..geometry import Rect
from .inline import SUPERSAMPLE, _font

HOLD_MS = 1600
FADE_OUT_S = 0.3


STAR = "✦"


def symbol_font(size: int):
    """La letra de símbolos de Windows (la del chat no tiene ✦ y salía un cuadradito)."""
    from PIL import ImageFont

    try:
        return ImageFont.truetype("seguisym.ttf", size)
    except OSError:
        return _font(size)


def render_toast(text: str, gold: bool, scale: float = 1.0) -> Image.Image:
    star = text.startswith(STAR)
    body = text[len(STAR):].strip() if star else text
    size = int(17 * scale)
    font, symbols = _font(size, body), symbol_font(size)
    star_w = symbols.getlength(STAR) + 9 * scale if star else 0
    text_w = font.getlength(body) + star_w
    pad_x, height = int(22 * scale), int(44 * scale)
    width = int(text_w) + 2 * pad_x
    big = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    edge = (*pro.GOLD_RGB, 255) if gold else (95, 100, 110, 255)
    draw.rounded_rectangle((0, 0, big.width - 1, big.height - 1), big.height // 2, fill=(20, 21, 25, 238),
                           outline=edge, width=2 * SUPERSAMPLE)
    image = big.resize((width, height), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(image)
    color = (*pro.GOLD_RGB, 255) if gold else (225, 228, 234, 255)
    x = (width - text_w) / 2
    if star:
        draw.text((x, height / 2), STAR, font=symbols, fill=color, anchor="lm")
        x += star_w
    draw.text((x, height / 2), body, font=font, fill=color, anchor="lm")
    return image


class Toast:
    def __init__(self, root) -> None:
        from ..layered import LayeredWindow

        self.root = root
        self.window = LayeredWindow()
        self._job = None
        self._fading = None

    def show(self, text: str, gold: bool, area: Rect | None) -> None:
        for job in (self._job, self._fading):
            if job:
                self.root.after_cancel(job)
        self._job = self._fading = None
        image = render_toast(text, gold)
        if area is None:
            width, height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            area = Rect(0, 0, width, height)
        x = area.left + (area.width - image.width) // 2
        y = area.top + int(area.height * 0.1)
        self.window.hide()
        self.window.update(image, x, y)  # aparece desvaneciéndose (layered.py)
        self._job = self.root.after(HOLD_MS, self._fade_out)

    def _fade_out(self, step: int = 0, steps: int = 12) -> None:
        self._job = None
        if step >= steps or not self.window.visible:
            self.window.hide()
            self._fading = None
            return
        self.window.set_opacity(int(255 * (1 - (step + 1) / steps)))
        self._fading = self.root.after(int(FADE_OUT_S * 1000 / steps), lambda: self._fade_out(step + 1, steps))
