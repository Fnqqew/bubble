"""Imágenes fijas del README: la ventana enmarcada y su funcionamiento."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, "src")
from bubble.ui.inline import ACCENT, CHAT_FILL, render_pill  # noqa: E402

SCRATCH = Path(sys.argv[1])
OUT = Path(sys.argv[2])
S = 2  # todo se dibuja al doble para que se vea nítido en pantallas de alta densidad


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (name, "seguisb.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def glow_backdrop(w: int, h: int, blobs, grain: float = 0.0) -> Image.Image:
    """Fondo oscuro con manchas de luz de color muy desenfocadas y algo de grano."""
    t = np.linspace(0, 1, h)[:, None, None]
    top, bottom = np.array((16, 18, 25), float), np.array((11, 12, 17), float)
    arr = np.repeat(top + (bottom - top) * t, w, axis=1)
    img = Image.fromarray(arr.astype(np.uint8), "RGB").convert("RGBA")
    light = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(light)
    for (cx, cy, r, color) in blobs:
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    img = Image.alpha_composite(img, light.filter(ImageFilter.GaussianBlur(max(w, h) // 9)))
    arr = np.asarray(img.convert("RGB")).astype(float)
    if grain:
        arr += np.random.default_rng(5).normal(0, grain, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")


def rounded(image: Image.Image, radius: int) -> Image.Image:
    image = image.convert("RGBA")
    mask = Image.new("L", (image.width * 3, image.height * 3), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), radius * 3, fill=255)
    image.putalpha(mask.resize(image.size, Image.Resampling.LANCZOS))
    return image


def drop(canvas: Image.Image, image: Image.Image, x: int, y: int, radius: int, blur: int = 28, offset: int = 14,
         strength: int = 150, outline: tuple | None = (255, 255, 255, 22)) -> None:
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x, y + offset, x + image.width, y + image.height + offset), radius,
                                             fill=(0, 0, 0, strength))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)))
    canvas.alpha_composite(image, (x, y))
    if outline:
        line = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        ImageDraw.Draw(line).rounded_rectangle((x, y, x + image.width - 1, y + image.height - 1), radius,
                                               outline=outline, width=1)
        canvas.alpha_composite(line)


def card_corners(image: Image.Image, radius: int) -> Image.Image:
    """Esquinas transparentes, para que la imagen se vea bien en el tema claro y en el oscuro de GitHub."""
    return rounded(image, radius)


# ------------------------------------------------------------------ la ventana
def interface() -> None:
    """Dos páginas de la ventana superpuestas: Inicio al frente y Ajustes detrás."""
    front_raw = Image.open(SCRATCH / "page_oscuro_inicio.png")
    back_raw = Image.open(SCRATCH / "page_oscuro_ajustes.png")
    crop = lambda im: rounded(im.crop((9, 0, im.width - 9, im.height - 9)), 8)  # noqa: E731 - sin bordes invisibles
    front, back = crop(front_raw), crop(back_raw)
    scale = 0.9
    back = back.resize((int(back.width * scale), int(back.height * scale)), Image.Resampling.LANCZOS)
    w, h = front.width + back.width - 120 + 140, front.height + 120
    canvas = glow_backdrop(w, h, [(int(w * 0.2), int(h * 0.25), 360, (60, 110, 230, 150)),
                                  (int(w * 0.85), int(h * 0.8), 380, (140, 80, 220, 120)),
                                  (int(w * 0.6), int(h * 0.1), 240, (40, 170, 200, 70))])
    drop(canvas, back, w - back.width - 70, 90, 8, blur=30, offset=18, strength=170)
    drop(canvas, front, 70, 50, 8, blur=36, offset=22, strength=200)
    card_corners(canvas, 18).save(OUT / "interfaz.png", optimize=True)


# ------------------------------------------------------------------ cómo funciona
def icon_screen(size: int) -> Image.Image:
    """Pantalla con renglones de chat, que representa a Bubble leyendo la pantalla."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 100
    d.rounded_rectangle((8 * u, 16 * u, 92 * u, 72 * u), 8 * u, outline=(210, 222, 245, 255), width=int(4 * u))
    d.rounded_rectangle((40 * u, 72 * u, 60 * u, 84 * u), 2 * u, fill=(210, 222, 245, 255))
    d.rounded_rectangle((28 * u, 82 * u, 72 * u, 88 * u), 3 * u, fill=(210, 222, 245, 255))
    for i, (w, color) in enumerate([(46, (2, 184, 87)), (58, (1, 162, 255)), (34, (253, 94, 94))]):
        y = 28 * u + i * 12 * u
        d.rounded_rectangle((18 * u, y, 26 * u, y + 6 * u), 3 * u, fill=color + (255,))
        d.rounded_rectangle((30 * u, y, (30 + w) * u, y + 6 * u), 3 * u, fill=(210, 222, 245, 200))
    return img


def icon_translate(size: int) -> Image.Image:
    """Dos globos de diálogo cruzados, con idiomas distintos."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 100
    d.rounded_rectangle((6 * u, 12 * u, 60 * u, 52 * u), 12 * u, fill=(210, 222, 245, 255))
    d.polygon([(16 * u, 50 * u), (30 * u, 50 * u), (14 * u, 64 * u)], fill=(210, 222, 245, 255))
    d.text((33 * u, 32 * u), "Aa", font=font("seguisb.ttf", int(22 * u)), fill=(20, 24, 32, 255), anchor="mm")
    d.rounded_rectangle((40 * u, 40 * u, 94 * u, 80 * u), 12 * u, fill=(84, 152, 255, 255))
    d.polygon([(70 * u, 78 * u), (84 * u, 78 * u), (86 * u, 92 * u)], fill=(84, 152, 255, 255))
    d.text((67 * u, 60 * u), "文", font=font("msyh.ttc", int(24 * u)), fill=(255, 255, 255, 255), anchor="mm")
    return img


def icon_pill(size: int) -> Image.Image:
    """La píldora real de Bubble cubriendo un mensaje."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    u = size / 100
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((4 * u, 18 * u, 96 * u, 82 * u), 12 * u, outline=(210, 222, 245, 255), width=int(4 * u))
    d.rounded_rectangle((14 * u, 30 * u, 26 * u, 38 * u), 4 * u, fill=(2, 184, 87, 255))
    d.rounded_rectangle((30 * u, 30 * u, 84 * u, 38 * u), 4 * u, fill=(210, 222, 245, 110))
    pill = render_pill(int(72 * u), int(22 * u), "hola!", int(13 * u), fill=CHAT_FILL, text_color=(246, 247, 250),
                       accent=ACCENT, shadow=False)
    border = Image.new("RGBA", (pill.width + 4, pill.height + 4), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle((0, 0, border.width - 1, border.height - 1), 11 * u,
                                             fill=(84, 152, 255, 90))
    img.alpha_composite(border, (int(14 * u) - 2, int(48 * u) - 2))
    img.alpha_composite(pill, (int(14 * u), int(48 * u)))
    return img


def how_it_works() -> None:
    w, h = 1600, 560
    W, H = w * S, h * S
    canvas = glow_backdrop(W, H, [(int(W * 0.15), int(H * 0.1), 260 * S, (60, 110, 230, 110)),
                                  (int(W * 0.9), int(H * 0.95), 300 * S, (140, 80, 220, 90))])
    d = ImageDraw.Draw(canvas)
    title = font("seguisb.ttf", 30 * S)
    body = font("segoeui.ttf", 19 * S)
    small = font("seguisb.ttf", 14 * S)
    d.text((W / 2, 64 * S), "Cómo funciona", font=font("seguisb.ttf", 34 * S), fill=(244, 246, 250), anchor="mm")
    steps = [
        (icon_screen, "Mira", ["Lee el chat y las burbujas", "de la pantalla, como una", "app de grabación."]),
        (icon_translate, "Entiende", ["Tu Claude traduce con la", "jerga de cada país, en", "un par de segundos."]),
        (icon_pill, "Muestra", ["La traducción aparece", "encima del original, en", "su lugar exacto."]),
    ]
    card_w, card_h, gap = 430 * S, 360 * S, 70 * S
    x0 = (W - (3 * card_w + 2 * gap)) // 2
    y0 = 120 * S
    for index, (icon, name, lines) in enumerate(steps):
        x = x0 + index * (card_w + gap)
        card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        cd = ImageDraw.Draw(card)
        cd.rounded_rectangle((0, 0, card_w - 1, card_h - 1), 22 * S, fill=(24, 27, 36, 235),
                             outline=(255, 255, 255, 26), width=S)
        art = icon(110 * S)
        card.alpha_composite(art, ((card_w - art.width) // 2, 34 * S))
        cd.ellipse((card_w / 2 - 16 * S, 160 * S, card_w / 2 + 16 * S, 192 * S), fill=(84, 152, 255))
        cd.text((card_w / 2, 176 * S), str(index + 1), font=small, fill=(255, 255, 255), anchor="mm")
        cd.text((card_w / 2, 224 * S), name, font=title, fill=(244, 246, 250), anchor="mm")
        for li, line in enumerate(lines):
            cd.text((card_w / 2, (268 + li * 27) * S), line, font=body, fill=(168, 176, 190), anchor="mm")
        drop(canvas, card, x, y0, 22 * S, blur=24 * S, offset=10 * S, strength=140, outline=None)
        if index < 2:
            ax = x + card_w + gap // 2
            ay = y0 + card_h // 2
            arrow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            ad = ImageDraw.Draw(arrow)
            ad.line((ax - 18 * S, ay, ax + 14 * S, ay), fill=(120, 132, 156, 255), width=3 * S)
            ad.polygon([(ax + 20 * S, ay), (ax + 8 * S, ay - 9 * S), (ax + 8 * S, ay + 9 * S)],
                       fill=(120, 132, 156, 255))
            canvas.alpha_composite(arrow)
    footer = "Todo corre en tu PC · con tu suscripción de Claude · nunca toca el programa de Roblox"
    d = ImageDraw.Draw(canvas)
    d.text((W / 2, (y0 + card_h) + 48 * S), footer, font=font("segoeui.ttf", 17 * S), fill=(130, 138, 152),
           anchor="mm")
    card_corners(canvas, 18 * S).save(OUT / "como-funciona.png", optimize=True)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    interface()
    how_it_works()
    print("ok")
