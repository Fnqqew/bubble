"""GIF de demostración para el README.

Escena ilustrada (un mundo de Roblox al atardecer, avatares de bloques) y, encima, el chat de Roblox con las traducciones
dibujadas por el MISMO código de Bubble: píldoras del chat, burbuja traducida, barra para escribir (capturada de la
ventana real), subtítulos de voz y tu voz dicha con voz artificial.
"""

from __future__ import annotations

import json
import math
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, "src")
from bubble.ui.inline import InlineChatView, PillSpot, render_bubble, fit_bubble_text  # noqa: E402
from bubble.ui.subtitles import render_subtitles  # noqa: E402
from bubble.voice.captions import MINE, Line  # noqa: E402

W, H = 960, 540
FPS = 20
DT = 1 / FPS
SCRATCH = Path(sys.argv[1])
OUT = Path(sys.argv[2])
COMPOSE = SCRATCH / "media" / "compose"
ICON = Path("src/bubble/assets/bubble.png")

random.seed(7)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    for candidate in (name, "seguisb.ttf", "segoeui.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


CHAT_FONT = font("seguisb.ttf", 15)
CAPTION_FONT = font("seguisb.ttf", 15)
SMALL_FONT = font("segoeui.ttf", 13)


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


# ------------------------------------------------------------------ fondo
def vertical_gradient(size, top, bottom) -> Image.Image:
    w, h = size
    t = np.linspace(0, 1, h)[:, None]
    top, bottom = np.array(top, float), np.array(bottom, float)
    rows = top + (bottom - top) * t[..., None]
    return Image.fromarray(np.repeat(rows, w, axis=1).astype(np.uint8), "RGB")


def shade(color, factor):
    return tuple(max(0, min(255, int(c * factor))) for c in color)


def block(draw: ImageDraw.ImageDraw, box, color, depth=0, top_face=0):
    """Bloque con cara frontal, costado y tapa (un poco de volumen)."""
    x0, y0, x1, y1 = box
    if top_face:
        draw.polygon([(x0, y0), (x0 + depth, y0 - top_face), (x1 + depth, y0 - top_face), (x1, y0)],
                     fill=shade(color, 1.18))
    if depth:
        draw.polygon([(x1, y0), (x1 + depth, y0 - top_face), (x1 + depth, y1 - top_face), (x1, y1)],
                     fill=shade(color, 0.72))
    draw.rectangle(box, fill=color)


def avatar(size, shirt, pants, skin=(245, 205, 48), face_dir=1) -> Image.Image:
    """Avatar de bloques estilo R6 (dibujado a 3x y achicado)."""
    s = 3
    u = size * s
    img = Image.new("RGBA", (int(u * 4.6), int(u * 6.2)), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = img.width // 2
    depth, top = int(u * 0.35), int(u * 0.2)
    legs_y, torso_y, head_y = u * 3.9, u * 1.9, u * 0.55
    # piernas, brazos, torso, cabeza (de atrás hacia adelante)
    block(d, (cx - u, legs_y, cx - 0.04 * u, legs_y + 2 * u), shade(pants, 0.95), depth, 0)
    block(d, (cx + 0.04 * u, legs_y, cx + u, legs_y + 2 * u), pants, depth, 0)
    block(d, (cx - 2 * u, torso_y, cx - u, torso_y + 2 * u), skin, depth, 0)
    block(d, (cx + u, torso_y, cx + 2 * u, torso_y + 2 * u), skin, depth, top)
    block(d, (cx - u, torso_y, cx + u, torso_y + 2 * u), shirt, depth, top)
    hw = 0.62 * u
    block(d, (cx - hw, head_y, cx + hw, head_y + 1.3 * u), skin, depth, top)
    # cara
    ex = cx + face_dir * 0.08 * u
    for dx in (-0.25, 0.25):
        d.ellipse((ex + dx * u - 0.07 * u, head_y + 0.42 * u, ex + dx * u + 0.07 * u, head_y + 0.62 * u),
                  fill=(35, 35, 40))
    d.arc((ex - 0.3 * u, head_y + 0.5 * u, ex + 0.3 * u, head_y + 1.02 * u), 20, 160, fill=(35, 35, 40),
          width=max(2, int(0.07 * u)))
    return img.resize((img.width // s, img.height // s), Image.Resampling.LANCZOS)


AVATAR_A = dict(x=585, feet=452, size=22, shirt=(52, 142, 64), pants=(40, 52, 92), dir=1)
AVATAR_B = dict(x=790, feet=470, size=24, shirt=(196, 72, 70), pants=(52, 52, 60), dir=-1)


def gradient_stops(size, stops) -> Image.Image:
    """Degradé vertical con varios colores: [(posición 0-1, color), ...]."""
    w, h = size
    t = np.linspace(0, 1, h)
    positions = [p for p, _c in stops]
    channels = [np.interp(t, positions, [c[i] for _p, c in stops]) for i in range(3)]
    rows = np.stack(channels, axis=1)[:, None, :]
    return Image.fromarray(np.repeat(rows, w, axis=1).astype(np.uint8), "RGB")


def cloud(scale: float, alpha: int) -> Image.Image:
    """Nube de caricatura: bolas blancas, base plana y una sombra suave abajo."""
    w, h = int(240 * scale), int(110 * scale)
    img = Image.new("RGBA", (w, h), (255, 255, 255, 0))  # transparente pero blanco: el borde no se oscurece
    d = ImageDraw.Draw(img)
    base = int(h * 0.78)
    parts = [(0.22, 0.62, 0.20), (0.40, 0.42, 0.24), (0.60, 0.48, 0.22), (0.78, 0.64, 0.16), (0.50, 0.66, 0.22)]
    for color, lift in (((206, 216, 238), 0.0), ((255, 255, 255), 0.06)):
        for cx, cy, r in parts:
            r_px = r * w
            x, y = cx * w, (cy - lift) * h
            d.ellipse((x - r_px, y - r_px, x + r_px, y + r_px), fill=color + (alpha,))
    arr = np.asarray(img).copy()
    arr[base:, :, 3] = 0  # base plana
    img = Image.fromarray(arr, "RGBA")
    return img.filter(ImageFilter.GaussianBlur(0.6))


def soft_ridge(width: int, base: int, height: int, bumps: int, seed: int) -> list[tuple[float, float]]:
    """Lomas suaves (curva que pasa por cimas al azar), no picos."""
    rng = random.Random(seed)
    tops = [(i * width / bumps, base - rng.uniform(0.45, 1.0) * height) for i in range(bumps + 1)]
    points = []
    for (x0, y0), (x1, y1) in zip(tops, tops[1:]):
        for k in range(16):
            u = k / 16
            points.append((x0 + (x1 - x0) * u, y0 + (y1 - y0) * (1 - math.cos(math.pi * u)) / 2))
    points += [(width, base + 60), (0, base + 60)]
    return points


def tree(d: ImageDraw.ImageDraw, x: int, ground: int, size: int, green) -> None:
    """Árbol de bloques: tronco y copa de cubos."""
    trunk_w, trunk_h = max(4, size // 5), int(size * 0.55)
    block(d, (x - trunk_w // 2, ground - trunk_h, x + trunk_w // 2, ground), (122, 84, 52, 255), max(2, size // 12), 0)
    canopy = [(-0.55, -1.25, 1.1, 0.7), (-0.4, -1.75, 0.8, 0.55), (-0.25, -2.12, 0.5, 0.42)]
    for i, (dx, dy, w, h) in enumerate(canopy):
        box = (x + int(dx * size), ground - trunk_h + int(dy * size * 0.62), x + int((dx + w) * size),
               ground - trunk_h + int((dy + h) * size * 0.62))
        block(d, box, shade(green, 1.0 + 0.08 * i) + (255,), max(3, size // 8), max(2, size // 12))


def backdrop() -> Image.Image:
    """Un mundo de Roblox al atardecer: nítido y con color (antes era todo borroso y apagado)."""
    horizon = 330
    img = gradient_stops((W, H), [(0.0, (46, 104, 204)), (0.33, (110, 164, 236)), (0.52, (240, 186, 170)),
                                  (0.62, (255, 212, 168)), (1.0, (255, 212, 168))]).convert("RGBA")
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse((690, 150, 910, 370), fill=255)
    glow = glow.filter(ImageFilter.GaussianBlur(64))
    img = Image.composite(Image.new("RGBA", (W, H), (255, 232, 196, 255)), img, glow.point(lambda v: int(v * 0.75)))
    sun = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sun).ellipse((764, 222, 840, 298), fill=(255, 246, 216, 255))
    img = Image.alpha_composite(img, sun.filter(ImageFilter.GaussianBlur(1.2)))
    for x, y, scale, alpha in ((500, 36, 0.95, 240), (760, 70, 0.72, 225), (880, 150, 0.5, 200), (640, 160, 0.42, 185)):
        img.alpha_composite(cloud(scale, alpha), (x, y))
    # lomas: lejos (lavanda, se funden con el cielo) y cerca (verdes)
    for base, height, bumps, seed, color, blur in ((horizon - 4, 110, 6, 4, (160, 168, 214), 1.2),
                                                   (horizon + 6, 64, 8, 11, (104, 160, 132), 0.6)):
        layer = Image.new("RGBA", (W, H), color + (0,))  # transparente del mismo color: sin borde oscuro al suavizar
        ImageDraw.Draw(layer).polygon(soft_ridge(W, base, height, bumps, seed), fill=color + (255,))
        img = Image.alpha_composite(img, layer.filter(ImageFilter.GaussianBlur(blur)))
    # suelo: la baseplate verde con su grilla en perspectiva
    ground = gradient_stops((W, H - horizon), [(0, (132, 208, 98)), (1, (62, 150, 66))]).convert("RGBA")
    grid = Image.new("RGBA", ground.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for i in range(1, 14):
        y = int(ground.height * (i / 14) ** 1.8)
        gd.line((0, y, W, y), fill=(255, 255, 255, 30), width=2)
    vanishing = (W * 0.7, -320)
    for i in range(-24, 40):
        gd.line((vanishing[0], vanishing[1], i * 52, ground.height), fill=(255, 255, 255, 26), width=2)
    img.alpha_composite(Image.alpha_composite(ground, grid), (0, horizon))
    # camino de baldosas hasta la torre
    world = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(world)
    lane = [(640, H), (780, H), (738, horizon + 2), (704, horizon + 2)]
    d.polygon(lane, fill=(230, 200, 146, 255))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon(lane, fill=255)
    seams = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(seams)
    for i in range(1, 10):
        y = horizon + 2 + int((H - horizon) * (i / 10) ** 1.6)
        sd.line((600, y, 820, y), fill=(198, 166, 114, 255), width=2)
    world.paste(seams, (0, 0), Image.composite(seams.getchannel("A"), Image.new("L", (W, H), 0), mask))
    # hilera de árboles en el horizonte, la torre y las plataformas del obby
    rng = random.Random(21)
    x = 440
    while x < W + 20:
        if not 690 <= x <= 770:
            tree(d, x, horizon + 6 + rng.randint(0, 6), rng.randint(18, 30),
                 rng.choice([(64, 156, 78), (80, 172, 88), (58, 144, 72)]))
        x += rng.randint(26, 44)
    block(d, (694, 98, 752, horizon + 4), (240, 240, 248, 255), 16, 10)
    block(d, (686, 82, 760, 100), (84, 152, 255, 255), 16, 10)
    for yy in range(118, horizon - 8, 28):
        d.rounded_rectangle((707, yy, 739, yy + 13), 3, fill=(122, 178, 242, 255))
    for x0, y0, w, h, color in [(520, 250, 84, 16, (255, 96, 96)), (590, 196, 70, 16, (255, 200, 70)),
                                (800, 196, 90, 18, (90, 176, 255)), (860, 256, 70, 16, (176, 116, 244))]:
        block(d, (x0, y0, x0 + w, y0 + h), color + (255,), 12, 8)
    img = Image.alpha_composite(img, world)
    # avatares con su sombra
    people = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for spec in (AVATAR_A, AVATAR_B):
        sprite = avatar(spec["size"], spec["shirt"], spec["pants"], face_dir=spec["dir"])
        shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse((spec["x"] - 44, spec["feet"] - 7, spec["x"] + 50, spec["feet"] + 9),
                                       fill=(0, 0, 0, 90))
        people = Image.alpha_composite(people, shadow.filter(ImageFilter.GaussianBlur(4)))
        people.alpha_composite(sprite, (spec["x"] - sprite.width // 2, spec["feet"] - sprite.height + 4))
    img = Image.alpha_composite(img, people)
    # viñeta suave y grano fino (el grano evita las bandas del GIF)
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt(((xx - W / 2) / (W / 1.5)) ** 2 + ((yy - H / 2) / (H / 1.3)) ** 2)
    vignette = np.clip(1 - (dist - 0.6) * 0.45, 0.82, 1)[..., None]
    arr = np.asarray(img.convert("RGB")).astype(float) * vignette
    arr += np.random.default_rng(3).normal(0, 1.6, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def head_top(spec) -> tuple[int, int]:
    return spec["x"], spec["feet"] - int(spec["size"] * 6.2) + int(spec["size"] * 0.3)


# ------------------------------------------------------------------ chat de Roblox
PANEL = (16, 16, 470, 238)  # chat
INPUT = (16, 244, 470, 280)  # barra "Para chatear…"
ROW_H = 24
NAME_COLORS = {
    "Ana_luz": (232, 120, 180), "lucas_br": (2, 184, 87), "xXShadowXx": (1, 162, 255), "kenji": (245, 205, 48),
    "rahul_07": (218, 133, 65), "sofi_uy": (180, 128, 255), "tomi_ar": (253, 94, 94),
}


@dataclass
class Message:
    at: float
    name: str
    text: str
    translation: str | None = None
    pending_after: float = 0.25
    done_after: float = 1.35


MESSAGES = [
    Message(-5, "Ana_luz", "bonjour tout le monde !", "¡hola a todos!"),
    Message(0.6, "lucas_br", "vlw mano, tmj", "gracias hermano, estamos"),
    Message(1.9, "xXShadowXx", "ngl this obby is kinda mid", "posta, este obby está medio flojo"),
    Message(3.1, "kenji", "lol", "jaja", 0.0, 0.2),
    Message(3.8, "rahul_07", "bhai kaha ho tum", "che, ¿dónde estás?"),
    Message(5.2, "sofi_uy", "vamos juntos a la torre?"),  # ya está en tu idioma: no se toca
    Message(11.75, "tomi_ar", "sure, wait for me at the tower"),  # lo que mandaste vos
]


def draw_chat(canvas: Image.Image, t: float) -> None:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(PANEL, 10, fill=(22, 24, 30, 150))
    typed = "sure, wait for me at the tower" if 11.3 <= t < 11.75 else ""
    d.rounded_rectangle(INPUT, 9, fill=(22, 24, 30, 175 if typed or 7.5 < t < 11.75 else 140))
    if typed:
        d.text((INPUT[0] + 12, (INPUT[1] + INPUT[3]) / 2), typed, font=CHAT_FONT, fill=(255, 255, 255, 255),
               anchor="lm")
    else:
        d.text((INPUT[0] + 12, (INPUT[1] + INPUT[3]) / 2), "Para chatear, hacé clic acá o apretá /",
               font=SMALL_FONT, fill=(200, 204, 210, 190), anchor="lm")
    pills = []
    row = 0
    for message in MESSAGES:
        if t < message.at:
            continue
        y = PANEL[1] + 12 + row * ROW_H
        row += 1
        appear = ease((t - message.at) / 0.18)
        x = PANEL[0] + 12
        name = f"{message.name}: "
        color = NAME_COLORS[message.name]
        alpha = int(255 * appear)
        d.text((x, y + ROW_H / 2), name, font=CHAT_FONT, fill=color + (alpha,), anchor="lm",
               stroke_width=1, stroke_fill=(0, 0, 0, int(90 * appear)))
        text_left = x + CHAT_FONT.getlength(name)
        d.text((text_left, y + ROW_H / 2), message.text, font=CHAT_FONT, fill=(255, 255, 255, alpha), anchor="lm",
               stroke_width=1, stroke_fill=(0, 0, 0, int(90 * appear)))
        if not message.translation:
            continue
        age = t - message.at
        if age < message.pending_after:
            continue
        waiting = age < message.done_after
        line = message.text if waiting else message.translation
        spot = PillSpot(left=int(text_left) - 5, top=int(y) - 1, bottom=int(y + ROW_H) + 1,
                        cover_right=int(text_left + CHAT_FONT.getlength(message.text)) + 4, max_right=PANEL[2] + 6)
        if waiting:
            continue  # como en la app: la píldora aparece con la traducción terminada
        pills.append((InlineChatView._pill(spot, line, 15), spot.left, spot.top))
    canvas.alpha_composite(layer)
    for pill, px, py in pills:
        canvas.alpha_composite(pill, (px, py))


# ------------------------------------------------------------------ burbuja sobre la cabeza
def roblox_bubble(text: str) -> tuple[Image.Image, tuple[int, int, int, int]]:
    f = font("seguisb.ttf", 15)
    w = int(f.getlength(text)) + 26
    h = 34
    s = 3
    img = Image.new("RGBA", ((w + 2) * s, (h + 12) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * s, h * s), 12 * s, fill=(255, 255, 255, 250))
    cx = w * s // 2
    d.polygon([(cx - 9 * s, h * s - 1), (cx + 9 * s, h * s - 1), (cx, (h + 10) * s)], fill=(255, 255, 255, 250))
    img = img.resize((w + 2, h + 12), Image.Resampling.LANCZOS)
    ImageDraw.Draw(img).text((w / 2, h / 2), text, font=f, fill=(57, 59, 61, 255), anchor="mm")
    return img, (0, 0, w, h)


BUBBLE_AT, BUBBLE_DONE, BUBBLE_END = 2.3, 3.9, 10.5


def draw_bubble(canvas: Image.Image, t: float) -> None:
    if not BUBBLE_AT <= t < BUBBLE_END:
        return
    original, box = roblox_bubble("oi, alguém quer trocar?")
    hx, hy = head_top(AVATAR_A)
    left = hx - original.width // 2
    top = hy - original.height - 8 + int(3 * math.sin(t * 2.2))  # flota apenas, como en el juego
    fade = ease((t - BUBBLE_AT) / 0.25) * (1 - ease((t - (BUBBLE_END - 0.3)) / 0.3))
    bubble = original.copy()
    if t >= BUBBLE_DONE:
        width, height = box[2] - 2, box[3] - 2
        item = SimpleNamespace(rows=1, background=(255, 255, 255), foreground=(57, 59, 61))
        size, lines, out_w, out_h = fit_bubble_text("¿alguien quiere cambiar?", width, height, 1)
        patch = render_bubble((out_w, out_h), lines, size, item.background, item.foreground, radius=min(height // 2, 12))
        grow = max(0, patch.width - width)
        if grow:
            wider = Image.new("RGBA", (bubble.width + grow, bubble.height), (0, 0, 0, 0))
            wider.alpha_composite(bubble, (grow // 2, 0))
            bubble, left = wider, left - grow // 2
        bubble.alpha_composite(patch, (1 + (width - patch.width) // 2 + grow // 2, 1 + height - patch.height))
    if fade < 1:
        bubble.putalpha(bubble.getchannel("A").point(lambda v: int(v * fade)))
    canvas.alpha_composite(bubble, (left, top))


# ------------------------------------------------------------------ barra para escribir (capturas reales)
COMPOSE_FRAMES = json.loads((COMPOSE / "frames.json").read_text())
COMPOSE_SCALE = 0.86
_compose_cache: dict[str, Image.Image] = {}


def compose_image(name: str) -> Image.Image:
    if name not in _compose_cache:
        raw = Image.open(COMPOSE / f"{name}.png").convert("RGBA")
        raw = raw.resize((int(raw.width * COMPOSE_SCALE), int(raw.height * COMPOSE_SCALE)), Image.Resampling.LANCZOS)
        mask = Image.new("L", (raw.width * 3, raw.height * 3), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, mask.width - 1, mask.height - 1), 10 * 3, fill=255)
        raw.putalpha(mask.resize(raw.size, Image.Resampling.LANCZOS))
        pad = 24
        framed = Image.new("RGBA", (raw.width + 2 * pad, raw.height + 2 * pad), (0, 0, 0, 0))
        shadow = Image.new("RGBA", framed.size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 6, pad + raw.width, pad + raw.height + 6), 12,
                                                 fill=(0, 0, 0, 120))
        framed = Image.alpha_composite(framed, shadow.filter(ImageFilter.GaussianBlur(10)))
        border = ImageDraw.Draw(framed)
        framed.alpha_composite(raw, (pad, pad))
        border.rounded_rectangle((pad, pad, pad + raw.width - 1, pad + raw.height - 1), 10, outline=(58, 62, 72, 255))
        _compose_cache[name] = framed
    return _compose_cache[name]


TEXT = "dale, esperame en la torre"
BAR_OPEN, TYPE_START, TYPE_CPS, WORKING, DONE, SEND = 7.7, 8.2, 17.0, 9.95, 10.55, 11.3


def compose_state(t: float) -> str | None:
    if not BAR_OPEN <= t < SEND:
        return None
    if t < TYPE_START:
        return "empty"
    typed = min(len(TEXT), int((t - TYPE_START) * TYPE_CPS) + 1)
    if typed < len(TEXT) or t < WORKING:
        return f"type_{typed:02d}"
    if t < DONE:
        return f"working_{int((t - WORKING) / 0.24) % 3}"
    return "done"


def draw_compose(canvas: Image.Image, t: float) -> None:
    name = compose_state(t)
    if name is None:
        return
    image = compose_image(name)
    rise = 1 - ease((t - BAR_OPEN) / 0.22)
    x = (W - image.width) // 2
    y = H - 34 - image.height + 24 + int(14 * rise)
    if rise > 0:
        image = image.copy()
        image.putalpha(image.getchannel("A").point(lambda v: int(v * (1 - rise))))
    canvas.alpha_composite(image, (x, y))


def draw_keycap(canvas: Image.Image, t: float) -> None:
    """La tecla ° apretada justo antes de que aparezca la barra."""
    start = BAR_OPEN - 0.55
    if not start <= t < BAR_OPEN + 0.35:
        return
    fade = ease((t - start) / 0.15) * (1 - ease((t - BAR_OPEN - 0.15) / 0.2))
    pressed = BAR_OPEN - 0.3 <= t < BAR_OPEN - 0.1
    s = 3
    size = 46
    img = Image.new("RGBA", (size * s, (size + 6) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    off = 3 * s if pressed else 0
    d.rounded_rectangle((0, 6 * s, size * s, (size + 6) * s), 9 * s, fill=(12, 13, 16, 230))
    d.rounded_rectangle((0, off, size * s, size * s + off), 9 * s, fill=(38, 41, 48, 240),
                        outline=(84, 152, 255, 255) if pressed else (70, 74, 84, 255), width=2 * s)
    d.text((size * s / 2, size * s / 2 + off), "°", font=font("seguisb.ttf", 26 * s), fill=(240, 242, 246, 255),
           anchor="mm")
    img = img.resize((size, size + 6), Image.Resampling.LANCZOS)
    img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
    canvas.alpha_composite(img, ((W - size) // 2, H - 150))


# ------------------------------------------------------------------ voz
VOICE_TALK, VOICE_SUB, VOICE_END = 12.9, 14.3, 18.2


def draw_voice(canvas: Image.Image, t: float) -> None:
    if VOICE_TALK <= t < VOICE_SUB + 0.6:
        # indicador de "hablando" sobre el avatar, como el de Roblox
        hx, hy = head_top(AVATAR_B)
        fade = ease((t - VOICE_TALK) / 0.2) * (1 - ease((t - VOICE_SUB) / 0.6))
        chip = Image.new("RGBA", (58 * 3, 28 * 3), (0, 0, 0, 0))
        d = ImageDraw.Draw(chip)
        d.rounded_rectangle((0, 0, chip.width - 1, chip.height - 1), 14 * 3, fill=(18, 20, 26, 215))
        for i in range(5):
            level = 0.35 + 0.65 * abs(math.sin(t * 9 + i * 1.3))
            bh = int(16 * 3 * level)
            bx = (12 + i * 7.5) * 3
            d.rounded_rectangle((bx, chip.height / 2 - bh / 2, bx + 3.5 * 3, chip.height / 2 + bh / 2), 6,
                                fill=(139, 226, 139, 255))
        chip = chip.resize((58, 28), Image.Resampling.LANCZOS)
        chip.putalpha(chip.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(chip, (hx - 29, hy - 40))
    if VOICE_SUB <= t < VOICE_END:
        card = render_subtitles([Line(1, 2, "pt", "oi, alguém quer ir comigo no boss?",
                                      "che, ¿alguien quiere venir conmigo al jefe?", True, True)])
        card = card.resize((int(card.width * 0.8), int(card.height * 0.8)), Image.Resampling.LANCZOS)
        fade = ease((t - VOICE_SUB) / 0.25) * (1 - ease((t - (VOICE_END - 0.35)) / 0.35))
        rise = int(10 * (1 - ease((t - VOICE_SUB) / 0.25)))
        if fade < 1:
            card.putalpha(card.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(card, ((W - card.width) // 2, H - 58 - card.height + rise))


# ------------------------------------------------------------------ tu voz para los demás (voz artificial)
SPEAK_PRESS, SPEAK_TALK, SPEAK_SAY, SPEAK_REPLY, SPEAK_END = 18.55, 18.9, 20.8, 23.1, 25.3
MY_WORDS = "¿alguien viene conmigo a la torre?"
SPOKEN = "anyone coming with me to the tower?"


def mic_button(pressed: bool, fade: float) -> Image.Image:
    """El botón para hablar (el lateral del mouse): una tecla con un micrófono."""
    s, size = 3, 46
    img = Image.new("RGBA", (size * s, (size + 6) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    off = 3 * s if pressed else 0
    d.rounded_rectangle((0, 6 * s, size * s, (size + 6) * s), 9 * s, fill=(12, 13, 16, 230))
    d.rounded_rectangle((0, off, size * s, size * s + off), 9 * s, fill=(38, 41, 48, 240),
                        outline=(84, 152, 255, 255) if pressed else (70, 74, 84, 255), width=2 * s)
    cx, cy = size * s / 2, size * s / 2 + off
    d.rounded_rectangle((cx - 5 * s, cy - 13 * s, cx + 5 * s, cy + 3 * s), 5 * s, fill=(240, 242, 246, 255))
    d.arc((cx - 9 * s, cy - 8 * s, cx + 9 * s, cy + 8 * s), 20, 160, fill=(240, 242, 246, 255), width=2 * s)
    d.line((cx, cy + 8 * s, cx, cy + 12 * s), fill=(240, 242, 246, 255), width=2 * s)
    img = img.resize((size, size + 6), Image.Resampling.LANCZOS)
    img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
    return img


def voice_bubble(text: str, t: float) -> Image.Image:
    """Lo que dice tu voz artificial, sobre tu avatar: ondas de sonido y la frase en el idioma del otro."""
    f = font("seguisb.ttf", 15)
    small = font("segoeui.ttf", 11)
    s = 3
    w, h = int(f.getlength(text)) + 62, 46
    img = Image.new("RGBA", (w * s, (h + 10) * s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), 14 * s, fill=(18, 21, 29, 232))
    d.polygon([(w * s / 2 - 8 * s, h * s - 2), (w * s / 2 + 8 * s, h * s - 2), (w * s / 2, (h + 9) * s)],
              fill=(18, 21, 29, 232))
    for i in range(4):  # ondas que se mueven mientras suena
        level = 0.35 + 0.65 * abs(math.sin(t * 10 + i * 1.4))
        bh = 18 * s * level
        bx = (14 + i * 6) * s
        d.rounded_rectangle((bx, h * s / 2 - bh / 2, bx + 3 * s, h * s / 2 + bh / 2), 3 * s, fill=(84, 152, 255, 255))
    img = img.resize((w, h + 10), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(img)
    d.text((48, 17), text, font=f, fill=(246, 248, 252, 255), anchor="lm")
    d.text((48, 34), "voz artificial · en su idioma", font=small, fill=(150, 184, 250, 255), anchor="lm")
    return img


def draw_speak(canvas: Image.Image, t: float) -> None:
    if not SPEAK_PRESS - 0.2 <= t < SPEAK_END:
        return
    # 1. tocás el botón para hablar
    if t < SPEAK_TALK + 0.4:
        fade = ease((t - SPEAK_PRESS + 0.2) / 0.15) * (1 - ease((t - SPEAK_TALK - 0.1) / 0.3))
        button = mic_button(SPEAK_PRESS <= t < SPEAK_PRESS + 0.2, fade)
        canvas.alpha_composite(button, ((W - button.width) // 2, H - 150))
    hx, hy = head_top(AVATAR_A)
    # 2. hablás en tu idioma (el indicador de voz de Roblox sobre tu avatar)
    if SPEAK_TALK <= t < SPEAK_SAY:
        fade = ease((t - SPEAK_TALK) / 0.2) * (1 - ease((t - SPEAK_SAY + 0.25) / 0.25))
        chip = Image.new("RGBA", (58 * 3, 28 * 3), (0, 0, 0, 0))
        d = ImageDraw.Draw(chip)
        d.rounded_rectangle((0, 0, chip.width - 1, chip.height - 1), 14 * 3, fill=(18, 20, 26, 215))
        for i in range(5):
            level = 0.35 + 0.65 * abs(math.sin(t * 9 + i * 1.3))
            bh = int(16 * 3 * level)
            bx = (12 + i * 7.5) * 3
            d.rounded_rectangle((bx, chip.height / 2 - bh / 2, bx + 3.5 * 3, chip.height / 2 + bh / 2), 6,
                                fill=(139, 226, 139, 255))
        chip = chip.resize((58, 28), Image.Resampling.LANCZOS)
        chip.putalpha(chip.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(chip, (hx - 29, hy - 40))
    # 3. Bubble lo dice con voz artificial, en el idioma del otro
    if SPEAK_SAY <= t < SPEAK_END - 0.3:
        bubble = voice_bubble(SPOKEN, t if t < SPEAK_REPLY else SPEAK_REPLY)
        fade = ease((t - SPEAK_SAY) / 0.25) * (1 - ease((t - (SPEAK_END - 0.6)) / 0.3))
        rise = int(8 * (1 - ease((t - SPEAK_SAY) / 0.25)))
        bubble.putalpha(bubble.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(bubble, (hx - bubble.width // 2, hy - bubble.height - 10 + rise))
    # subtítulo: lo que dijiste y cómo sonó
    if SPEAK_TALK + 0.4 <= t < SPEAK_END:
        said = t >= SPEAK_SAY
        line = Line(7, MINE, "es", MY_WORDS, SPOKEN if said else "", True, said)
        card = render_subtitles([line])
        card = card.resize((int(card.width * 0.8), int(card.height * 0.8)), Image.Resampling.LANCZOS)
        fade = ease((t - SPEAK_TALK - 0.4) / 0.25) * (1 - ease((t - (SPEAK_END - 0.35)) / 0.35))
        card.putalpha(card.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(card, ((W - card.width) // 2, H - 58 - card.height))
    # 4. el otro te contesta (y su burbuja también se traduce)
    if SPEAK_REPLY <= t < SPEAK_END:
        original, box = roblox_bubble("omw!!")
        bx, by = head_top(AVATAR_B)
        fade = ease((t - SPEAK_REPLY) / 0.25) * (1 - ease((t - (SPEAK_END - 0.35)) / 0.3))
        bubble = original.copy()
        if t >= SPEAK_REPLY + 1.1:
            width, height = box[2] - 2, box[3] - 2
            size, lines, out_w, out_h = fit_bubble_text("¡ya voy!", width, height, 1)
            patch = render_bubble((out_w, out_h), lines, size, (255, 255, 255), (57, 59, 61), radius=min(height // 2, 12))
            bubble.alpha_composite(patch, (1 + (width - patch.width) // 2, 1 + height - patch.height))
        bubble.putalpha(bubble.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(bubble, (bx - bubble.width // 2, by - bubble.height - 8))


# ------------------------------------------------------------------ títulos de cada parte
CAPTIONS = [
    (0.2, 7.2, "1", "Te escriben en otro idioma: lo leés en el tuyo"),
    (7.2, 12.6, "2", "Apretás ° y escribís como hablás"),
    (12.6, 18.2, "3", "Te hablan por voz: subtítulos al instante"),
    (18.2, 25.3, "4", "Hablás en tu idioma: te escuchan en el suyo, con voz artificial"),
]


def draw_caption(canvas: Image.Image, t: float) -> None:
    for start, end, number, text in CAPTIONS:
        if not start <= t < end:
            continue
        fade = ease((t - start) / 0.3) * (1 - ease((t - (end - 0.3)) / 0.3))
        s = 3
        text_w = CAPTION_FONT.getlength(text)
        w, h = int(text_w + 58), 36
        img = Image.new("RGBA", (w * s, h * s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w * s - 1, h * s - 1), h * s // 2, fill=(14, 16, 21, 215))
        d.ellipse((7 * s, 7 * s, 29 * s, 29 * s), fill=(84, 152, 255, 255))
        img = img.resize((w, h), Image.Resampling.LANCZOS)
        d = ImageDraw.Draw(img)
        d.text((18, h / 2), number, font=font("seguisb.ttf", 13), fill=(255, 255, 255, 255), anchor="mm")
        d.text((40, h / 2), text, font=CAPTION_FONT, fill=(244, 246, 250, 255), anchor="lm")
        img.putalpha(img.getchannel("A").point(lambda v: int(v * fade)))
        canvas.alpha_composite(img, (W - w - 16, 16 + int(6 * (1 - fade))))


def draw_brand(canvas: Image.Image) -> None:
    icon = Image.open(ICON).convert("RGBA").resize((22, 22), Image.Resampling.LANCZOS)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    layer.alpha_composite(icon, (W - 96, H - 34))
    ImageDraw.Draw(layer).text((W - 68, H - 23), "Bubble", font=font("seguisb.ttf", 15), fill=(255, 255, 255, 235),
                               anchor="lm")
    layer.putalpha(layer.getchannel("A").point(lambda v: int(v * 0.85)))
    canvas.alpha_composite(layer)


# ------------------------------------------------------------------ armado
TOTAL = 25.7
LOOP_FADE = 0.4


def frame_at(base: Image.Image, t: float) -> Image.Image:
    canvas = base.copy()
    draw_bubble(canvas, t)
    draw_chat(canvas, t)
    draw_voice(canvas, t)
    draw_speak(canvas, t)
    draw_keycap(canvas, t)
    draw_compose(canvas, t)
    draw_caption(canvas, t)
    draw_brand(canvas)
    return canvas.convert("RGB")


def main() -> None:
    base = backdrop()
    times = [i * DT for i in range(int(TOTAL / DT))]
    frames = [frame_at(base, t) for t in times]
    # vuelta suave al principio
    first = frames[0]
    fade_n = int(LOOP_FADE / DT)
    for k in range(fade_n):
        frames[-fade_n + k] = Image.blend(frames[-fade_n + k], first, (k + 1) / (fade_n + 1))
    OUT.mkdir(parents=True, exist_ok=True)
    for name, t in (("still_chat", 5.9), ("still_compose", 10.9), ("still_voice", 15.5), ("still_speak", 22.4)):
        frame_at(base, t).save(SCRATCH / f"{name}.png")

    # GIF con ffmpeg: paleta pensada para lo que cambia y tramado ordenado (lo que no cambia queda idéntico)
    import shutil
    import subprocess

    import imageio_ffmpeg

    folder = SCRATCH / "frames"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    for index, frame in enumerate(frames):
        frame.save(folder / f"{index:04d}.png")
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    path = OUT / "demo.gif"
    graph = ("split[a][b];[a]palettegen=max_colors=256:stats_mode=diff[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(folder / "%04d.png"),
                    "-vf", graph, "-loop", "0", str(path)], check=True)
    video = OUT / "demo.mp4"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", str(folder / "%04d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", str(video)],
                   check=True)
    print(path, round(path.stat().st_size / 1e6, 2), "MB ·", video.name, round(video.stat().st_size / 1e6, 2), "MB ·",
          len(frames), "cuadros")


if __name__ == "__main__":
    main()
