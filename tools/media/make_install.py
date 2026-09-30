"""Guía de instalación animada (docs/instalacion.gif) y un cuadro por paso (docs/instalacion/paso-N.png), generados con
las ventanas reales de Bubble.

Uso: make_install.py <carpeta con las capturas> <carpeta de salida> Capturas (ver tools/media/README.md): setup_*.png
y setup.json (shot_install.py) y page_oscuro_inicio.png (shot_main.py). La carpeta y la consola se dibujan, con los
textos reales de Iniciar.bat.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).parent))
from make_stills import drop, font, glow_backdrop, rounded  # noqa: E402

from bubble.ui.inline import CHAT_FILL, render_pill  # noqa: E402

SCRATCH, OUT = Path(sys.argv[1]), Path(sys.argv[2])
W, H = 1100, 620
FPS = 15
SCENE_S = 4.2
FADE_S = 0.45
ACCENT = (87, 169, 255)
TEXT = (236, 239, 244)
MUTED = (150, 157, 170)
FAINT = (255, 255, 255, 38)
LOGO = Image.open("src/bubble/assets/bubble.png").convert("RGBA")
BUTTONS = json.loads((SCRATCH / "setup.json").read_text(encoding="utf-8"))
EDGE = 9  # bordes invisibles de Windows alrededor de cada ventana capturada

STEPS = [
    ("Bajá Bubble", "Descargá el ZIP de la última versión (en GitHub, «Releases») y descomprimilo donde quieras: "
                    "el Escritorio, Documentos…"),
    ("Doble clic en Iniciar.bat", "La primera vez prepara todo solo. Si te falta Python, te ofrece instalarlo: "
                                  "tocá S y Enter."),
    ("Bubble se arma solo", "Descarga lo que necesita para entender y decir voces. Tarda unos minutos y podés seguir "
                            "usando la PC mientras."),
    ("Conectá tu Claude", "Tocá «Iniciar sesión» y entrá con tu cuenta de Claude (Pro o Max): es lo que traduce. "
                          "Si querés que te escuchen traducido, instalá también el micrófono virtual."),
    ("¡A jugar!", "Elegí tu idioma, abrí Roblox y listo: Bubble encuentra el chat solo y traduce mientras jugás."),
]


def ease(t: float) -> float:
    t = min(1.0, max(0.0, t))
    return 1 - (1 - t) ** 3


def window(name: str, scale: float, crop: tuple | None = None) -> Image.Image:
    raw = Image.open(SCRATCH / name).convert("RGBA")
    raw = raw.crop((EDGE, 0, raw.width - EDGE, raw.height - EDGE))
    if crop:
        raw = raw.crop(crop)
    image = rounded(raw, 8)
    return image.resize((round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS)


def wrap(text: str, typeface, width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        test = f"{line} {word}".strip()
        if typeface.getlength(test) > width and line:
            lines.append(line)
            line = word
        else:
            line = test
    return lines + [line] if line else lines


# ------------------------------------------------------------------ lo fijo: fondo, marca y textos
BACKDROP = glow_backdrop(W, H, [(int(W * 0.12), int(H * 0.2), 300, (60, 110, 230, 150)),
                                (int(W * 0.78), int(H * 0.75), 360, (130, 80, 220, 120)),
                                (int(W * 0.55), int(H * 0.05), 200, (40, 170, 200, 60))])


def text_layer(index: int) -> Image.Image:
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    title, body = STEPS[index]
    draw.text((62, 118), f"PASO {index + 1} DE {len(STEPS)}", font=font("seguisb.ttf", 15), fill=ACCENT)
    y = 146
    for line in wrap(title, font("segoeuib.ttf", 38), 400):
        draw.text((60, y), line, font=font("segoeuib.ttf", 38), fill=TEXT)
        y += 48
    y += 10
    for line in wrap(body, font("segoeui.ttf", 18), 390):
        draw.text((62, y), line, font=font("segoeui.ttf", 18), fill=MUTED)
        y += 27
    return layer


def chrome(canvas: Image.Image, index: int, progress: float) -> None:
    """Dibuja la marca arriba y los pasos abajo; el paso actual se va llenando, como en las historias."""
    logo = LOGO.resize((30, 30), Image.Resampling.LANCZOS)
    canvas.alpha_composite(logo, (60, 44))
    draw = ImageDraw.Draw(canvas)
    draw.text((100, 59), "Bubble", font=font("seguisb.ttf", 18), fill=TEXT, anchor="lm")
    draw.text((100 + font("seguisb.ttf", 18).getlength("Bubble") + 8, 59), "· instalación en 5 pasos",
              font=font("segoeui.ttf", 16), fill=MUTED, anchor="lm")
    gap, left, right, y = 8, 60, 460, H - 58
    size = (right - left - gap * (len(STEPS) - 1)) / len(STEPS)
    track = Image.new("RGBA", (W, H), (0, 0, 0, 0))  # la transparencia se mezcla aparte; de lo contrario queda blanco
    ImageDraw.Draw(track).rounded_rectangle((left, y, right, y + 5), 3, fill=(0, 0, 0, 0))
    lines = ImageDraw.Draw(track)
    for step in range(len(STEPS)):
        x0 = left + step * (size + gap)
        lines.rounded_rectangle((x0, y, x0 + size, y + 5), 3, fill=FAINT)
    canvas.alpha_composite(track)
    for step in range(len(STEPS)):
        x0 = left + step * (size + gap)
        filled = 1.0 if step < index else progress if step == index else 0.0
        if filled > 0:
            draw.rounded_rectangle((x0, y, x0 + max(6.0, size * filled), y + 5), 3, fill=ACCENT)


# ------------------------------------------------------------------ paso 1: la carpeta
FILES = [("carpeta", "docs"), ("carpeta", "src"), ("carpeta", "tools"), ("bat", "Desinstalar.bat"),
         ("bat", "Iniciar.bat"), ("texto", "README.md")]


def file_icon(draw: ImageDraw.ImageDraw, kind: str, x: int, y: int) -> None:
    if kind == "carpeta":
        draw.rounded_rectangle((x, y + 2, x + 9, y + 6), 2, fill=(222, 170, 60))
        draw.rounded_rectangle((x, y + 5, x + 20, y + 18), 3, fill=(247, 196, 76))
    elif kind == "bat":
        draw.rounded_rectangle((x + 1, y, x + 19, y + 18), 3, fill=(64, 70, 84))
        draw.text((x + 10, y + 9), "›_", font=font("consolab.ttf", 11), fill=(140, 220, 150), anchor="mm")
    else:
        draw.rounded_rectangle((x + 3, y, x + 17, y + 18), 2, fill=(200, 206, 216))
        for line in range(3):
            draw.line((x + 6, y + 5 + line * 4, x + 14, y + 5 + line * 4), fill=(120, 126, 138), width=1)


def cursor(canvas: Image.Image, x: float, y: float) -> None:
    points = [(0, 0), (0, 22), (6, 17), (10, 26), (14, 24), (10, 16), (17, 16)]
    shape = [(x + px, y + py) for px, py in points]
    draw = ImageDraw.Draw(canvas)
    draw.polygon([(px + 1, py + 2) for px, py in shape], fill=(0, 0, 0, 90))
    draw.polygon(shape, fill=(255, 255, 255), outline=(20, 20, 24))


def scene_folder(t: float) -> Image.Image:
    w, h = 470, 330
    card = Image.new("RGBA", (w, h), (32, 33, 38, 255))
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, w, 40), fill=(40, 41, 47))
    file_icon(draw, "carpeta", 16, 11)
    draw.text((46, 20), "bubble-3.3.0", font=font("seguisb.ttf", 14), fill=TEXT, anchor="lm")
    for index, glyph in enumerate(("—", "□", "✕")):
        draw.text((w - 92 + index * 34, 20), glyph, font=font("segoeui.ttf", 13), fill=MUTED, anchor="mm")
    draw.text((20, 58), "Nombre", font=font("segoeui.ttf", 12), fill=MUTED, anchor="lm")
    draw.line((16, 72, w - 16, 72), fill=(52, 54, 62))
    click = t >= 2.1
    for row, (kind, name) in enumerate(FILES):
        y = 84 + row * 38
        if name == "Iniciar.bat" and click:
            draw.rounded_rectangle((10, y - 4, w - 10, y + 30), 6, fill=(38, 72, 112))
        file_icon(draw, kind, 22, y + 3)
        draw.text((54, y + 13), name, font=font("segoeui.ttf", 15), fill=TEXT, anchor="lm")
    card = rounded(card, 12)
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x0, y0 = 560, 150
    drop(frame, card, x0, y0, 12, blur=26, offset=16, strength=170)
    target = (x0 + 110, y0 + 84 + 4 * 38 + 13)
    start = (x0 + 380, y0 + 300)
    p = ease((t - 0.5) / 1.3)
    cx, cy = start[0] + (target[0] - start[0]) * p, start[1] + (target[1] - start[1]) * p
    draw = ImageDraw.Draw(frame)
    for tap in (2.1, 2.35):  # doble clic: dos ondas
        age = t - tap
        if 0 <= age < 0.6:
            radius = 8 + 36 * ease(age / 0.6)
            alpha = int(200 * (1 - age / 0.6))
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=(*ACCENT, alpha), width=3)
    cursor(frame, cx, cy)
    return frame


# ------------------------------------------------------------------ paso 2: la consola
CONSOLE = [
    (0.2, "  Preparando Bubble por primera vez (unos minutos, se descarga)..."),
    (0.8, ""),
    (0.9, "  Falta Python 3.12 de 64 bits (gratis)."),
    (1.3, "   Lo instalo ahora [S,N]?S"),
    (1.9, "  Collecting faster-whisper>=1.2"),
    (2.2, "  Downloading ctranslate2 (19.4 MB)"),
    (2.5, "  Collecting piper-tts>=1.3"),
    (2.8, "  Installing collected packages: ..."),
    (3.3, "  Successfully installed bubble-3.3.0"),
]


def scene_console(t: float) -> Image.Image:
    w, h = 500, 330
    card = Image.new("RGBA", (w, h), (12, 12, 14, 255))
    draw = ImageDraw.Draw(card)
    draw.rectangle((0, 0, w, 36), fill=(30, 31, 36))
    draw.rounded_rectangle((14, 11, 30, 25), 3, fill=(64, 70, 84))
    draw.text((22, 18), "›", font=font("consolab.ttf", 11), fill=(140, 220, 150), anchor="mm")
    draw.text((40, 18), "Bubble - primera vez", font=font("segoeui.ttf", 13), fill=TEXT, anchor="lm")
    mono = font("consola.ttf", 13)
    y = 50
    shown = [text for at, text in CONSOLE if t >= at]
    for line in shown:
        color = (130, 220, 140) if line.strip().startswith("Successfully") else (212, 216, 222)
        if "[S,N]" in line:
            color = (247, 200, 110)
        draw.text((10, y), line, font=mono, fill=color)
        y += 22
    if int(t * 2.5) % 2 == 0:  # cursor con parpadeo
        draw.rectangle((12, y + 2, 20, y + 16), fill=(212, 216, 222))
    card = rounded(card, 12)
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    drop(frame, card, 545, 150, 12, blur=26, offset=16, strength=170)
    return frame


# ------------------------------------------------------------------ pasos 3 y 4: «Preparar Bubble»
SETUP_SCALE = 0.8


def setup_frame(names: list[tuple[float, str]], t: float) -> Image.Image:
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    first = window(names[0][1], SETUP_SCALE)
    x0, y0 = 800 - first.width // 2, (H - first.height) // 2 - 6
    image = first
    for at, name in names[1:]:
        if t >= at:
            image = Image.blend(image, window(name, SETUP_SCALE), ease((t - at) / 0.5))
    drop(frame, image, x0, y0, 8, blur=30, offset=18, strength=180)
    return frame


def scene_setup(t: float) -> Image.Image:
    return setup_frame([(0, "setup_instalando.png"), (2.0, "setup_voces.png")], t)


def scene_login(t: float) -> Image.Image:
    frame = setup_frame([(0, "setup_sesion.png")], t)
    first = window("setup_sesion.png", SETUP_SCALE)
    x0, y0 = 800 - first.width // 2, (H - first.height) // 2 - 6
    draw = ImageDraw.Draw(frame)
    for name, delay in (("Iniciar sesión", 0.4), ("Instalar micrófono virtual", 2.2)):
        left, top, right, bottom = BUTTONS["sesion"][name]
        box = [x0 + (left - EDGE) * SETUP_SCALE, y0 + top * SETUP_SCALE,
               x0 + (right - EDGE) * SETUP_SCALE, y0 + bottom * SETUP_SCALE]
        if t < delay:
            continue
        pulse = ((t - delay) % 1.2) / 1.2
        grow = 3 + 9 * ease(pulse)
        alpha = int(230 * (1 - pulse))
        draw.rounded_rectangle((box[0] - grow, box[1] - grow, box[2] + grow, box[3] + grow), 8 + grow,
                               outline=(*ACCENT, alpha), width=2)
        draw.rounded_rectangle((box[0] - 3, box[1] - 3, box[2] + 3, box[3] + 3), 8, outline=(*ACCENT, 255), width=2)
    return frame


# ------------------------------------------------------------------ paso 5: a jugar
def scene_play(t: float) -> Image.Image:
    frame = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    main = window("page_oscuro_inicio.png", 0.74, crop=(0, 0, 600, 505))
    x0, y0 = 600, 70
    drop(frame, main, x0, y0, 8, blur=30, offset=18, strength=180)
    lines = [("lucas_br:", "gracias hermano, estamos"), ("xXShadowXx:", "posta, este obby está re meh")]
    shown = ease((t - 0.5) / 0.4)
    if shown > 0:
        ImageDraw.Draw(frame).text((x0 - 88, y0 + main.height + 4), "Y en el juego, encima del chat:",
                                   font=font("segoeui.ttf", 14), fill=(*MUTED, int(255 * shown)))
    for index, (name, text) in enumerate(lines):
        appear = ease((t - 0.8 - index * 0.7) / 0.45)
        if appear <= 0:
            continue
        label = f"{name} {text}"
        width = int(font("seguisb.ttf", 16).getlength(label)) + 34
        pill = render_pill(width, 34, label, 15, fill=CHAT_FILL, text_color=(246, 247, 250), accent=ACCENT)
        faded = pill.copy()
        faded.putalpha(pill.getchannel("A").point(lambda a: int(a * appear)))
        frame.alpha_composite(faded, (int(x0 - 90 + 24 * (1 - appear)), int(y0 + main.height + 30 + index * 44)))
    return frame


SCENES = [scene_folder, scene_console, scene_setup, scene_login, scene_play]
TEXTS = [text_layer(index) for index in range(len(STEPS))]


def compose(index: int, t: float, enter: float = 1.0) -> Image.Image:
    canvas = BACKDROP.copy()
    text = TEXTS[index]
    visual = SCENES[index](t)
    if enter < 1:
        shift = int(28 * (1 - enter))
        moved = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        moved.alpha_composite(visual, (shift, 0))
        visual = moved
    canvas.alpha_composite(text)
    canvas.alpha_composite(visual)
    chrome(canvas, index, min(1.0, t / SCENE_S))
    return canvas.convert("RGB")


def main() -> None:
    frames: list[Image.Image] = []
    stills = OUT / "instalacion"
    stills.mkdir(parents=True, exist_ok=True)
    count = int(SCENE_S * FPS)
    fade = int(FADE_S * FPS)
    for index in range(len(STEPS)):
        previous = compose(index - 1, SCENE_S) if index else compose(len(STEPS) - 1, SCENE_S)
        for frame_number in range(count):
            t = frame_number / FPS
            enter = ease(t / FADE_S)
            frame = compose(index, t, enter)
            if frame_number < fade:
                frame = Image.blend(previous, frame, ease((frame_number + 1) / (fade + 1)))
            frames.append(frame)
        compose(index, SCENE_S - 0.01).save(stills / f"paso-{index + 1}.png", optimize=True)
    import imageio_ffmpeg

    folder = SCRATCH / "frames_install"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir()
    for number, frame in enumerate(frames):
        frame.save(folder / f"{number:04d}.png")
    path = OUT / "instalacion.gif"
    graph = ("split[a][b];[a]palettegen=max_colors=256:stats_mode=full[p];"
             "[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-framerate", str(FPS), "-i",
                    str(folder / "%04d.png"), "-vf", graph, "-loop", "0", str(path)], check=True)
    print(path, round(path.stat().st_size / 1e6, 2), "MB ·", len(frames), "cuadros")


if __name__ == "__main__":
    main()
