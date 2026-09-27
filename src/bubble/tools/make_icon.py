"""Genera el ícono de Bubble: burbuja de diálogo 3D inclinada al estilo Roblox con una letra romana.

Uso:  python -m bubble.tools.make_icon [--letter B]
Requiere Pillow (pip install Pillow).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SIZE = 1024  # se dibuja grande y se reduce para que los bordes queden suaves
TILT_DEG = 15  # inclinación característica del logo de Roblox
DEPTH = 46  # grosor del volumen 3D (px a 1024)
OUTLINE = 26  # grosor del borde blanco
FONT_CANDIDATES = ["timesbd.ttf", "georgiab.ttf", "times.ttf"]  # tipografías romanas de Windows
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    raise SystemExit("No se encontró una tipografía romana (Times New Roman / Georgia)")


def _bubble_mask() -> Image.Image:
    """Burbuja + pata como UNA sola figura, así el borde la rodea sin cortes."""
    mask = Image.new("L", (SIZE, SIZE), 0)
    draw = ImageDraw.Draw(mask)
    left, top, right, bottom = 250, 170, 774, 694
    draw.rounded_rectangle((left, top, right, bottom), radius=64, fill=255)
    # La pata nace bien adentro de la burbuja para que la unión no deje escalones.
    draw.polygon([(left + 70, bottom - 40), (left + 250, bottom - 40), (left + 10, bottom + 190)], fill=255)
    return mask.rotate(TILT_DEG, resample=Image.Resampling.BICUBIC, center=(512, 512))


def _grow(mask: Image.Image, px: int) -> Image.Image:
    """Dilata la máscara `px` píxeles (borde uniforme alrededor de la figura)."""
    return mask.filter(ImageFilter.GaussianBlur(px / 2)).point(lambda v: 255 if v > 8 else 0).filter(
        ImageFilter.GaussianBlur(1.5)
    )


def _vertical_gradient(top_rgb: tuple[int, int, int], bottom_rgb: tuple[int, int, int]) -> Image.Image:
    gradient = Image.new("RGBA", (SIZE, SIZE))
    draw = ImageDraw.Draw(gradient)
    for y in range(SIZE):
        t = y / (SIZE - 1)
        draw.line([(0, y), (SIZE, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top_rgb, bottom_rgb)) + (255,))
    return gradient


def _shift(mask: Image.Image, dx: int, dy: int) -> Image.Image:
    return ImageChops.offset(mask, dx, dy)


def _fill(canvas: Image.Image, color_or_image, mask: Image.Image) -> None:
    layer = color_or_image if isinstance(color_or_image, Image.Image) else Image.new("RGBA", (SIZE, SIZE), color_or_image)
    canvas.paste(layer, (0, 0), mask)


def render(letter: str = "B") -> Image.Image:
    face = _bubble_mask()
    outline = _grow(face, OUTLINE)
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))

    # Sombra proyectada suave.
    shadow = _shift(outline, 18, DEPTH + 26).filter(ImageFilter.GaussianBlur(22)).point(lambda v: v * 0.55)
    _fill(canvas, (0, 0, 0, 255), shadow)

    # Volumen: el borde se "extruye" hacia abajo, más oscuro cuanto más profundo.
    for d in range(DEPTH, 0, -2):
        shade = round(150 - 70 * d / DEPTH)
        _fill(canvas, (shade, shade, shade + 6, 255), _shift(outline, round(d * 0.35), d))

    # Borde blanco con leve degradé y la cara oscura de la burbuja.
    _fill(canvas, _vertical_gradient((255, 255, 255), (214, 218, 224)), outline)
    _fill(canvas, _vertical_gradient((64, 68, 74), (22, 23, 26)), face)

    # Brillo superior (reflejo tipo plástico) recortado a la cara.
    gloss = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(gloss).ellipse((170, -330, 900, 470), fill=70)
    gloss = gloss.rotate(TILT_DEG, center=(512, 512)).filter(ImageFilter.GaussianBlur(18))
    _fill(canvas, (255, 255, 255, 255), ImageChops.multiply(gloss, face))

    # Letra romana en relieve: sombra, cuerpo con degradé y la misma inclinación.
    text_mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(text_mask).text((512, 420), letter, font=_font(430), fill=255, anchor="mm")
    text_mask = text_mask.rotate(TILT_DEG, resample=Image.Resampling.BICUBIC, center=(512, 512))
    _fill(canvas, (0, 0, 0, 255), _shift(text_mask, 7, 16).filter(ImageFilter.GaussianBlur(5)).point(lambda v: v * 0.8))
    for d in range(10, 0, -2):
        _fill(canvas, (150, 156, 166, 255), _shift(text_mask, round(d * 0.35), d))
    _fill(canvas, _vertical_gradient((255, 255, 255), (206, 212, 222)), text_mask)

    return _square(canvas)


def _square(img: Image.Image) -> Image.Image:
    """Recorta al contenido, centra en un cuadrado con margen y reduce a 256 px."""
    content = img.crop(img.getbbox())
    side = int(max(content.size) * 1.04)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(content, ((side - content.width) // 2, (side - content.height) // 2), content)
    return canvas.resize((256, 256), Image.Resampling.LANCZOS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera assets/bubble.ico")
    parser.add_argument("--letter", default="B")
    args = parser.parse_args()
    ASSETS.mkdir(exist_ok=True)
    icon = render(args.letter)
    icon.save(ASSETS / "bubble.png")
    icon.save(ASSETS / "bubble.ico", sizes=ICO_SIZES)
    print(f"Ícono generado en {ASSETS / 'bubble.ico'}")
    from ..shortcut import ensure_desktop_shortcut

    ensure_desktop_shortcut(force=True)
    print("Acceso directo del escritorio actualizado.")


if __name__ == "__main__":
    main()
