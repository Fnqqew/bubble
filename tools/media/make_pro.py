"""Genera la imagen de Bubble Pro para el README (docs/pro.png): Basic y Pro lado a lado, con las ventanas reales, la
barra de escritura de Pro y el aviso que se muestra en el juego al cambiar con Ctrl+P.

Uso: make_pro.py <carpeta con las capturas> <carpeta de salida> Capturas (ver tools/media/README.md):
page_oscuro_inicio.png (Basic), page_oscuro_pro_pro.png (Pro) y compose_pro/done.png (la barra con Pro).
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent))
from make_stills import card_corners, drop, font, glow_backdrop, rounded  # noqa: E402

from bubble.ui.toast import STAR, render_toast, symbol_font  # noqa: E402

SCRATCH, OUT = Path(sys.argv[1]), Path(sys.argv[2])
GOLD = (242, 193, 78)


def window(name: str, scale: float) -> Image.Image:
    raw = Image.open(SCRATCH / name)
    image = rounded(raw.crop((9, 0, raw.width - 9, raw.height - 9)), 8)  # recorta los bordes invisibles de Windows
    return image.resize((int(image.width * scale), int(image.height * scale)), Image.Resampling.LANCZOS)


def glow(canvas: Image.Image, box: tuple[int, int, int, int], color: tuple, blur: int, alpha: int) -> None:
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(box, 24, fill=(*color, alpha))
    canvas.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def label(canvas: Image.Image, x: int, y: int, text: str, size: int, color: tuple, sub: str = "") -> None:
    draw = ImageDraw.Draw(canvas)
    if text.startswith(STAR):  # usa la fuente de símbolos, que la otra no incluye ✦
        draw.text((x, y), STAR, font=symbol_font(size), fill=color, anchor="ls")
        x += int(symbol_font(size).getlength(STAR)) + 10
        text = text[len(STAR):].strip()
    draw.text((x, y), text, font=font("seguisb.ttf", size), fill=color, anchor="ls")
    if sub:
        width = font("seguisb.ttf", size).getlength(text)
        draw.text((x + width + 14, y), sub, font=font("segoeui.ttf", int(size * 0.6)), fill=(140, 147, 160),
                  anchor="ls")


def main() -> None:
    basic = window("page_oscuro_inicio.png", 0.78)
    pro = window("page_oscuro_pro_pro.png", 0.9)
    bar = rounded(Image.open(SCRATCH / "compose_pro" / "done.png"), 14)
    toast = render_toast("✦  Bubble Pro activado", True, 1.15)
    w, h = 1480, 980
    canvas = glow_backdrop(w, h, [(int(w * 0.18), int(h * 0.35), 380, (60, 110, 230, 130)),
                                  (int(w * 0.8), int(h * 0.4), 420, (242, 170, 60, 120)),
                                  (int(w * 0.55), int(h * 0.95), 260, (242, 193, 78, 60))], grain=2.0)
    bx, by = 90, 170
    px, py = w - pro.width - 90, 120
    label(canvas, bx, by - 26, "Basic", 34, (225, 229, 236), "gratis · todo en tu PC")
    label(canvas, px, py - 26, "✦ Pro", 38, GOLD, "la voz en la nube")
    drop(canvas, basic, bx, by, 8, blur=30, offset=18, strength=170)
    glow(canvas, (px - 6, py - 6, px + pro.width + 6, py + pro.height + 6), GOLD, 26, 150)
    drop(canvas, pro, px, py, 8, blur=36, offset=22, strength=210, outline=(*GOLD, 150))
    bar_x, bar_y = (w - bar.width) // 2 - 40, h - bar.height - 60
    drop(canvas, bar, bar_x, bar_y, 14, blur=26, offset=14, strength=220, outline=(*GOLD, 90))
    canvas.alpha_composite(toast, ((w - toast.width) // 2 - 40, bar_y - toast.height - 26))
    card_corners(canvas, 18).save(OUT / "pro.png", optimize=True)
    print("ok")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    main()
