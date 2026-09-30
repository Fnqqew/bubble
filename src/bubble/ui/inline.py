"""Traducciones dibujadas encima del chat y de las burbujas de Roblox.

Cada mensaje traducido se tapa con una "píldora": fondo oscuro casi opaco con bordes redondeados, la
traducción en letra blanca con sombra suave y una rayita azul que indica que es una traducción. El nombre
del jugador queda visible. Mientras se traduce, la píldora muestra el original en gris.

Las píldoras son ventanas con transparencia real (ver layered.py): dejan pasar los clics, no salen en las
capturas de pantalla (así el OCR sigue leyendo el chat original de debajo) y, como no dependen del fondo,
se reutilizan: mover una traducción es solo mover su ventana.
"""

from __future__ import annotations

import itertools
import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..translate.base import ChatLine
from .rtl import visual

FONT_FILES = ["seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"]  # parecidas a la letra del chat de Roblox
MIN_SCALE = 0.6  # la letra se achica hasta 60% antes de cortar con "…"
MAX_BUBBLE_SPEED = 1800  # px/s: más rápido que esto es un giro brusco de cámara
RENDER_DELAY_S = 0.03  # lo que tarda en verse la ventana después de moverla

# Estilo de las píldoras del chat (los valores por defecto; se pueden cambiar en Ajustes, ver `set_style`).
CHAT_FILL = (17, 19, 24, 252)  # casi opaca: con menos se transparentaba el texto original debajo
CHAT_TEXT = (246, 247, 250)
ACCENT = (84, 152, 255, 240)
PILL_COLORS = {"grafito": (17, 19, 24), "medianoche": (14, 24, 48), "violeta": (38, 22, 56),
               "bosque": (14, 34, 28), "negro": (0, 0, 0)}
ACCENTS = {"dorado": (242, 193, 78), "azul": (84, 152, 255), "verde": (92, 214, 140), "rosa": (255, 120, 180), "naranja": (255, 164, 72),
           "ninguno": None}


@dataclass
class Style:
    fill: tuple = CHAT_FILL
    accent: tuple | None = ACCENT
    scale: float = 1.0  # tamaño de la letra
    version: int = 0  # cambia con cada ajuste: las imágenes guardadas se redibujan


STYLE = Style()


def set_style(color: str = "grafito", opacity: float = 0.98, accent: str = "azul", scale: float = 1.0) -> None:
    """Aplica los ajustes de apariencia a las traducciones (se ven en la próxima captura)."""
    rgb = PILL_COLORS.get(color, PILL_COLORS["grafito"])
    STYLE.fill = (*rgb, int(255 * max(0.6, min(1.0, opacity))))
    tint = ACCENTS.get(accent, ACCENTS["azul"])
    STYLE.accent = (*tint, 240) if tint else None
    STYLE.scale = max(0.8, min(1.5, scale))
    STYLE.version += 1
SHADOW = (0, 0, 0, 170)
PILL_BEFORE_TEXT = 5  # la píldora arranca un poco antes del texto (tapa el espacio después de "Nombre:")
TEXT_INSET = 10  # dentro de la píldora: rayita azul + espacio
RIGHT_PAD = 8
SUPERSAMPLE = 3  # bordes redondeados suaves: se dibujan 3 veces más grandes y se achican

# Letras de Windows para otras escrituras (la del chat no tiene hindi, coreano, chino…).
SCRIPT_FONTS = [
    (re.compile(r"[\u0900-\u0DFF]"), ["Nirmala.ttc"]),  # hindi, bengalí, tamil… (indias)
    (re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]"), ["malgunbd.ttf", "malgun.ttf"]),  # coreano
    (re.compile(r"[\u3040-\u30FF]"), ["YuGothB.ttc", "msyhbd.ttc"]),  # japonés
    (re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF]"), ["msyhbd.ttc", "msyh.ttc"]),  # chino
    (re.compile(r"[\u0E00-\u0E7F]"), ["LeelaUIb.ttf", "LeelawUI.ttf"]),  # tailandés
]

_fonts: dict[tuple[int, str], ImageFont.FreeTypeFont] = {}


def _font(size: int, text: str = "") -> ImageFont.FreeTypeFont:
    """La letra del chat en ese tamaño; si `text` usa otra escritura, una letra de Windows que la tenga."""
    size = max(6, size)
    files = next((names for pattern, names in SCRIPT_FONTS if text and pattern.search(text)), FONT_FILES)
    key = (size, files[0])
    if key not in _fonts:
        for name in [*files, *FONT_FILES]:
            try:
                _fonts[key] = ImageFont.truetype(name, size)
                break
            except OSError:
                continue
        else:
            _fonts[key] = ImageFont.load_default()
    return _fonts[key]


# ---------------------------------------------------------------- disposición del texto
@dataclass(frozen=True)
class Slot:
    """Espacio disponible para texto: una línea (coordenadas de la captura)."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return max(1, self.right - self.left)

    @property
    def height(self) -> int:
        return max(1, self.bottom - self.top)


def layout_text(text: str, slots: list[Slot], base_size: int, balance: bool = False) -> tuple[int, list[str]]:
    """Reparte el texto en las líneas disponibles achicando la letra si hace falta.

    Devuelve (tamaño de letra, texto por línea). Si ni con la letra mínima entra, corta con "…". Con `balance`, si
    sobra lugar el texto se reparte parejo entre todas las líneas (en el chat, cada línea tapa un renglón del
    mensaje original: una vacía se veía como un rectángulo negro sin nada).
    """
    words = text.split()
    size = base_size
    min_size = max(8, int(base_size * MIN_SCALE))
    while True:
        font = _font(size, text)
        lines = _fill(words, slots, font)
        if lines is not None or size <= min_size:
            break
        size -= 1
    if lines is None:
        lines = _fill_truncated(words, slots, font)
    elif balance and len(slots) > 1 and not all(lines) and len(words) >= len(slots):
        lines = _balanced(words, slots, font) or lines
    return size, lines


def _balanced(words: list[str], slots: list[Slot], font) -> list[str] | None:
    """Las palabras repartidas en todas las líneas, de largo parecido."""
    target = font.getlength(" ".join(words)) / len(slots)
    lines: list[str] = []
    index = 0
    for number, slot in enumerate(slots):
        remaining_slots = len(slots) - number
        line = ""
        while index < len(words) and len(words) - index > remaining_slots - 1:
            candidate = f"{line} {words[index]}".strip()
            last = number == len(slots) - 1
            if font.getlength(candidate) > slot.width - 4 or (line and not last and font.getlength(candidate) > target):
                break
            line = candidate
            index += 1
        lines.append(line)
    return lines if index >= len(words) and all(lines) else None


def _fill(words: list[str], slots: list[Slot], font) -> list[str] | None:
    lines: list[str] = []
    index = 0
    for slot in slots:
        line = ""
        while index < len(words):
            candidate = f"{line} {words[index]}".strip()
            if font.getlength(candidate) <= slot.width - 4:
                line = candidate
                index += 1
            elif not line:
                return None  # una sola palabra no entra en la línea
            else:
                break
        lines.append(line)
    return lines if index >= len(words) else None


def _fill_truncated(words: list[str], slots: list[Slot], font) -> list[str]:
    lines: list[str] = []
    index = 0
    for number, slot in enumerate(slots):
        line = ""
        last = number == len(slots) - 1
        while index < len(words):
            candidate = f"{line} {words[index]}".strip()
            limit = slot.width - 4 - (font.getlength("…") if last else 0)
            if font.getlength(candidate) <= limit:
                line = candidate
                index += 1
            else:
                break
        if last and index < len(words):
            line = (line + "…") if line else words[index][:3] + "…"
        lines.append(line)
    return lines


# ---------------------------------------------------------------- dibujo
def render_pill(width: int, height: int, text: str, size: int, *, fill: tuple, text_color: tuple,
                accent: tuple | None = None, radius: int | None = None, align: str = "left",
                shadow: bool = True) -> Image.Image:
    """Píldora con bordes redondeados suaves (RGBA, con transparencia) y un texto."""
    width, height = max(4, width), max(4, height)
    radius = min(height // 2, 9) if radius is None else radius
    big = Image.new("RGBA", (width * SUPERSAMPLE, height * SUPERSAMPLE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    draw.rounded_rectangle((0, 0, big.width - 1, big.height - 1), radius * SUPERSAMPLE, fill=fill)
    if accent:
        s = SUPERSAMPLE
        draw.rounded_rectangle((3 * s, 4 * s, 6 * s - 1, big.height - 4 * s), 1.5 * s, fill=accent)
    image = big.resize((width, height), Image.Resampling.LANCZOS)
    if text:
        draw = ImageDraw.Draw(image)
        font = _font(size, text)
        x, anchor = (TEXT_INSET, "lm") if align == "left" else (width / 2, "mm")
        y = height / 2
        shown = visual(text)  # (hebreo, árabe…: en el orden en que se leen)
        if shadow:
            draw.text((x + 1, y + 1), shown, font=font, fill=SHADOW, anchor=anchor)
        draw.text((x, y), shown, font=font, fill=text_color, anchor=anchor)
    return image


def _wrap(words: list[str], font, max_width: float) -> list[str]:
    lines: list[str] = []
    for word in words:
        candidate = f"{lines[-1]} {word}" if lines else word
        if lines and font.getlength(candidate) <= max_width:
            lines[-1] = candidate
        else:
            lines.append(word)
    return lines


def fit_bubble_text(text: str, width: int, height: int, rows: int) -> tuple[int, list[str], int, int]:
    """(tamaño de letra, líneas, ancho, alto) de la burbuja traducida.

    La letra es como la de la burbuja original. La traducción suele ser más larga que el original (el español
    más que el inglés): nunca se corta. Primero la burbuja se ensancha (hasta 1,8 veces), después la letra se achica
    un poco y, si todavía no entra, se suman líneas. Nunca queda más chica que la original (la tapa entera).
    Antes probaba hasta 3 líneas: una burbuja de 4 renglones perdía el final de la traducción.
    """
    words = text.split() or [text]
    line_height = max(10.0, (height - 12) / max(1, rows))  # alto de cada línea de texto de la original
    base = int(max(10, min(30, line_height * 0.72)))  # letra parecida a la original
    widest = max(width, min(int(width * 1.8), 460))
    most = max(rows + 3, 5)
    for size in sorted({base, max(10, int(base * 0.9)), max(10, int(base * 0.8))}, reverse=True):
        font = _font(size, text)
        step = line_height * size / base  # con letra más chica, renglones más juntos
        for count in range(max(1, rows), most + 1):
            target = max(width - 24, font.getlength(text) / count + size)  # líneas parejas
            if target > widest - 24:
                continue
            lines = _wrap(words, font, target)
            longest = max(font.getlength(line) for line in lines)
            if len(lines) <= count and longest <= widest - 24:
                return size, lines, max(width, int(longest) + 28), max(height, int(len(lines) * step + 12))
    # Muchísimo texto: todo el ancho posible y las líneas que hagan falta (se lee entero, nunca con "…").
    size = max(10, int(base * 0.8))
    font = _font(size, text)
    lines = _wrap(words, font, widest - 24)
    return size, lines, widest, max(height, int(len(lines) * line_height * size / base + 12))


def render_bubble(size_px: tuple[int, int], text_lines: list[str], font_size: int,
                  background: tuple[int, int, int], foreground: tuple[int, int, int],
                  radius: int = 10) -> Image.Image:
    """Burbuja de diálogo (fondo claro, letra oscura) con la traducción."""
    width, height = size_px
    base = render_pill(width, height, "", font_size, fill=(*background[:3], 255), text_color=foreground,
                       radius=radius, shadow=False)
    draw = ImageDraw.Draw(base)
    font = _font(font_size, " ".join(text_lines))
    line_height = height / max(1, len(text_lines))
    for index, line in enumerate(text_lines):
        draw.text((width / 2, line_height * (index + 0.5)), visual(line), font=font, fill=(*foreground[:3], 255),
                  anchor="mm")
    return base


# ---------------------------------------------------------------- geometría del chat
def base_font_size(rows) -> int:
    """Tamaño de letra parecido al del chat, estimado con la altura de las palabras que leyó el OCR."""
    heights = sorted(w.height for row in rows for w in row.words) or sorted(r.height for r in rows)
    median = heights[len(heights) // 2] if heights else 16
    return int(max(10, min(40, median * 1.15)))


@dataclass(frozen=True)
class PillSpot:
    """Dónde va la píldora de una línea del mensaje (coordenadas de la captura del chat)."""

    left: int
    top: int
    bottom: int
    cover_right: int  # tiene que llegar al menos hasta acá para tapar el original
    max_right: int  # y como mucho hasta acá (el borde del chat)

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def text_slot(self) -> Slot:
        return Slot(self.left, self.top, self.max_right - TEXT_INSET - RIGHT_PAD, self.bottom)


TEXT_EDGE_FLOOR = 0.6  # sin haber visto mensajes largos, las traducciones llegan al menos a esta parte del ancho
TEXT_EDGE_MARGIN = 45  # px más allá del texto más largo: Roblox corta los renglones bastante antes del borde del panel


def chat_spots(item, frame_width: int, row_height: float | None = None, text_right: float = 0) -> list[PillSpot]:
    """Una píldora por línea del mensaje: la primera desde donde termina el nombre, el resto enteras.

    Con `row_height` (la altura típica de las líneas del chat) todas las píldoras tienen el mismo alto, centradas en
    su línea: el OCR mide cada línea un poco distinto (una banderita la agranda) y se veían desparejas. Pueden pasar
    unos píxeles el borde calibrado, para tapar el final del texto original.

    Con `text_right` (hasta dónde llega el texto del chat) las píldoras no pasan de ahí: la zona calibrada suele ser
    más ancha que el chat, y una traducción larga se salía del chat.
    """
    edge = frame_width
    if text_right:
        edge = min(frame_width, max(text_right, TEXT_EDGE_FLOOR * frame_width) + TEXT_EDGE_MARGIN)
    spots = []
    for index, row in enumerate(item.rows):
        start = item.text_left if index == 0 else row.left
        if row_height:
            center = row.top + row.height / 2
            top, bottom = int(center - row_height / 2) - 3, int(center + row_height / 2) + 3
        else:
            top, bottom = int(row.top) - 3, int(row.bottom) + 3
        spots.append(PillSpot(
            left=int(start) - PILL_BEFORE_TEXT, top=top, bottom=bottom,
            cover_right=int(row.right) + 4, max_right=int(max(edge, row.right)) + 6,
        ))
    return spots


def chat_metrics(items) -> tuple[int, float] | None:
    """(tamaño de letra, alto de línea) típicos de las líneas visibles del chat."""
    heights = sorted(w.height for item in items for row in item.rows for w in row.words)
    rows = sorted(row.height for item in items for row in item.rows)
    if not rows:
        return None
    median = heights[len(heights) // 2] if heights else rows[len(rows) // 2]
    return int(max(10, min(40, median * 1.15))), float(rows[len(rows) // 2])


def chat_slots(item, frame_width: int) -> list[Slot]:
    """Espacio para texto de cada línea del mensaje (compatibilidad y pruebas)."""
    return [spot.text_slot() for spot in chat_spots(item, frame_width)]


@dataclass
class Entry:
    """Estado de la traducción de un mensaje (del chat o de una burbuja)."""

    key: int
    speaker: str
    original: str
    status: str = "pending"  # pending | done | hidden | linked
    text: str = ""


# ---------------------------------------------------------------- ventanas
class Patch:
    """Una traducción en pantalla. Si la imagen no cambió, solo se mueve la ventana (rápido)."""

    def __init__(self) -> None:
        from ..layered import LayeredWindow

        self.window = LayeredWindow()
        self._image: Image.Image | None = None
        self._at: tuple[int, int] | None = None
        self._shown = False

    def show(self, image: Image.Image, x: int, y: int) -> None:
        if image is not self._image:
            self.window.update(image, x, y)
            self._image = image
        elif (x, y) != self._at or not self._shown:
            self.window.move(x, y)  # mismo dibujo en otro lugar: solo se mueve
        self._at, self._shown = (x, y), True  # quieta y sin cambios: no se toca (menos CPU)

    def hide(self) -> None:
        self.window.hide()
        self._shown = False


class PatchLayer:
    """Conjunto de traducciones en pantalla identificadas por clave; reutiliza ventanas."""

    def __init__(self, root=None) -> None:
        self._patches: dict[object, Patch] = {}
        self._spare: list[Patch] = []

    def show(self, key: object, image: Image.Image, x: int, y: int) -> None:
        patch = self._patches.get(key)
        if patch is None:
            patch = self._spare.pop() if self._spare else Patch()
            self._patches[key] = patch
        patch.show(image, x, y)

    def keep_only(self, keys: set) -> None:
        for key in [k for k in self._patches if k not in keys]:
            patch = self._patches.pop(key)
            patch.hide()
            self._spare.append(patch)

    def hide_all(self) -> None:
        self.keep_only(set())


class ImageCache:
    """Guarda la última imagen de cada traducción: si no cambió nada, se reutiliza el mismo objeto."""

    def __init__(self, limit: int = 400) -> None:
        self.limit = limit
        self._items: dict[object, tuple[tuple, Image.Image]] = {}

    def get(self, key: object, signature: tuple, build: Callable[[], Image.Image]) -> Image.Image:
        cached = self._items.get(key)
        if cached and cached[0] == signature:
            return cached[1]
        image = build()
        self._items[key] = (signature, image)
        if len(self._items) > self.limit:
            self._items.pop(next(iter(self._items)))
        return image


# ---------------------------------------------------------------- chat
class InlineChatView:
    """Dibuja cada traducción encima de su mensaje en el chat, en el lugar exacto.

    Cada mensaje en otro idioma queda "seleccionado" apenas se pide su traducción (píldora con el original
    en gris) y la traducción se escribe encima a medida que llega. Si el chat se cierra o salís de Roblox,
    se ocultan.
    """

    SEARCH_LAST = 60
    # Una línea que el OCR no leyó en una captura (el fondo del chat se desvaneció y la cámara se mueve detrás)
    # mantiene su traducción este tiempo, mientras ahí siga habiendo texto.
    HOLD_S = 3.0
    JITTER_PX = 2  # diferencias de posición menores no mueven la traducción (el OCR varía 1-2 px)

    def __init__(self, root, same_message: Callable[[ChatLine, ChatLine], bool]) -> None:
        self.layer = PatchLayer(root)
        self.same_message = same_message
        self.entries: deque[Entry] = deque(maxlen=500)
        self.by_id: dict[int, Entry] = {}
        # Búsqueda exacta (lo normal): sin comparar textos. Varias entradas si el mismo mensaje se repitió.
        self.by_line: dict[tuple[str, str], list[Entry]] = {}
        self.images = ImageCache()
        self._positions: dict[int, float] = {}
        self._moving_frames = 0
        self._last_bottom: ChatLine | None = None
        self._placed: dict[object, tuple[Image.Image, int, int, float]] = {}  # lo que se mostró: imagen, x, y, cuándo
        self._metrics: tuple[int, float] | None = None  # letra y alto de línea del chat
        self._layouts: dict[tuple, tuple[int, list[str]]] = {}
        self.last_frame = None
        self.last_patches: list[tuple[Image.Image, int, int]] = []

    def reset(self, same_message: Callable[[ChatLine, ChatLine], bool] | None = None) -> None:
        """Empezar de cero (se cerró Roblox o se refrescó): se ocultan las traducciones y se olvida la partida."""
        self.layer.hide_all()
        if same_message is not None:
            self.same_message = same_message
        self.entries.clear()
        self.by_id.clear()
        self.by_line.clear()
        self._positions.clear()
        self._placed.clear()
        self._moving_frames = 0
        self._last_bottom = None
        self.last_frame = None
        self.last_patches = []

    # --- estado de las traducciones
    def _add(self, entry: Entry) -> None:
        if len(self.entries) == self.entries.maxlen:
            old = self.entries[0]
            self.by_id.pop(old.key, None)
            same = self.by_line.get((old.speaker, old.original), [])
            if old in same:
                same.remove(old)
        self.entries.append(entry)
        self.by_id[entry.key] = entry
        self.by_line.setdefault((entry.speaker, entry.original), []).append(entry)

    def pending(self, msg_id: int, line: ChatLine) -> None:
        self._add(Entry(msg_id, line.speaker, line.text))

    def delta(self, msg_id: int, chunk: str) -> None:
        entry = self.by_id.get(msg_id)
        if entry and entry.status == "pending":
            entry.text += chunk

    def final(self, msg_id: int, line: ChatLine, text: str | None) -> None:
        """`text` = traducción a mostrar, o None si no hace falta mostrar nada (ya estaba en tu idioma)."""
        entry = self.by_id.get(msg_id)
        if entry is None:
            entry = Entry(msg_id, line.speaker, line.text)
            self._add(entry)
        entry.status = "done" if text else "hidden"
        entry.text = text or ""

    def find(self, line: ChatLine) -> Entry | None:
        exact = self.by_line.get((line.speaker, line.text))
        if exact:
            # Mismo mensaje repetido: mientras se traduce el nuevo, sirve la traducción del anterior.
            return next((e for e in reversed(exact) if e.status == "done"), exact[-1])
        for entry in reversed(list(self.entries)[-self.SEARCH_LAST:]):
            if self.same_message(ChatLine(entry.speaker, entry.original), line):
                return entry
        return None

    def find_text(self, text: str) -> Entry | None:
        """Busca por texto sin importar quién lo dijo (las burbujas no muestran el nombre)."""
        for entry in reversed(list(self.entries)[-self.SEARCH_LAST:]):
            if self.same_message(ChatLine("?", entry.original), ChatLine("?", text)):
                return entry
        return None

    # --- dibujo
    def _movement(self, placed: dict[int, float], bottom: ChatLine | None) -> tuple[bool, bool, float]:
        """(se movió, lo estás desplazando, cuánto). Cuando llega un mensaje nuevo todo sube: eso no es desplazar (en una
        ráfaga pasa varias capturas seguidas y antes ocultaba todas las traducciones: parpadeaban). Si el chat se
        mueve dos capturas seguidas sin mensajes nuevos abajo, lo estás desplazando: se ocultan las traducciones
        hasta que se quede quieto, así no quedan desfasadas."""
        # Se movió si se movió la mayoría (la mediana): una sola línea mal leída en otra altura no cuenta.
        shifts = sorted(top - self._positions[key] for key, top in placed.items() if key in self._positions)
        dy = shifts[len(shifts) // 2] if shifts else 0.0
        moved = bool(shifts) and abs(dy) > 4
        new_below = bottom is not None and (
            self._last_bottom is None or not self.same_message(bottom, self._last_bottom))
        self._positions, self._last_bottom = placed, bottom
        self._moving_frames = self._moving_frames + 1 if moved and not new_below else 0
        return moved, self._moving_frames >= 2, dy

    def render(self, frame, visible: bool) -> None:
        self.last_frame = frame
        self.last_patches = []
        if frame is None or not visible:
            self.layer.hide_all()
            self._placed.clear()
            return
        found = [(item, self.find(item.origin or item.line)) for item in frame.items]
        # Solo se muestra la traducción terminada: una píldora "traduciendo" con el original repetido (y los errores
        # del OCR a la vista) que después cambiaba y crecía palabra por palabra eran parpadeos y rectángulos de más.
        # El mismo mensaje repetido (spam, "gg" dos veces) usa la misma traducción, pero cada aparición tiene su propia
        # píldora, con la identidad estable que le da el seguidor del chat: si compartían una (o se numeraban por
        # orden), una pisaba a la otra o saltaban de llave al subir el chat, y parpadeaban.
        matches, seen = [], {}
        for item, entry in found:
            if entry is not None and entry.status == "done":
                seen[entry.key] = seen.get(entry.key, -1) + 1
                identity = getattr(item, "uid", 0) or -1 - seen[entry.key]
                matches.append((item, entry, (entry.key, identity)))
        bottom = frame.items[-1].line if frame.items else None
        moved, scrolling, dy = self._movement({pill: item.top for item, _entry, pill in matches}, bottom)
        if scrolling:
            self.layer.hide_all()
            self._placed.clear()
            return
        now = time.monotonic()
        shown: set = set()
        # Un solo tamaño de letra y alto de píldora para todo el chat (antes cada línea tenía el suyo según lo que
        # midiera el OCR y se veían desparejas). Solo cambia si el chat cambia de verdad (más de 1 px).
        metrics = chat_metrics(frame.items)
        if metrics and (self._metrics is None or abs(metrics[0] - self._metrics[0]) > 1
                        or abs(metrics[1] - self._metrics[1]) > 2):
            self._metrics = metrics
        base, row_height = self._metrics or (16, None)
        for item, entry, pill in matches:
            spots = chat_spots(item, frame.image.width, row_height, getattr(frame, "text_right", 0))
            text = entry.text
            slots = [spot.text_slot() for spot in spots]
            # Acomodar el texto (probar tamaños, medir palabras) es lo más caro del dibujo: se recuerda.
            size_wanted = int(round(base * STYLE.scale))
            layout_key = (text, size_wanted, tuple(slot.width for slot in slots))
            if layout_key not in self._layouts:
                if len(self._layouts) > 600:
                    self._layouts.clear()
                self._layouts[layout_key] = layout_text(text, slots, size_wanted, balance=True)
            size, lines = self._layouts[layout_key]
            for index, (spot, line) in enumerate(zip(spots, lines)):
                image = self.images.get(
                    (entry.key, index),
                    (line, size, spot.height, spot.cover_right - spot.left, spot.max_right - spot.left,
                     STYLE.version),
                    lambda spot=spot, line=line, size=size: self._pill(spot, line, size),
                )
                key = (*pill, index)
                shown.add(key)
                x, y = frame.region.left + spot.left, frame.region.top + spot.top
                previous = self._placed.get(key)
                if previous and abs(previous[1] - x) <= self.JITTER_PX and abs(previous[2] - y) <= self.JITTER_PX:
                    x, y = previous[1], previous[2]  # el OCR varió 1-2 px: la traducción no tiembla
                self._placed[key] = (image, x, y, now)
                self.last_patches.append((image, x - frame.region.left, y - frame.region.top))
                self.layer.show(key, image, x, y)
        # Una línea que el OCR no leyó en esta captura mantiene su traducción un momento: si no, se apaga y se prende
        # (pasa sobre todo justo cuando llega un mensaje y todo sube). Si el chat se movió, se mueve lo mismo que el
        # resto; en los dos casos, solo si en ese lugar sigue habiendo letras.
        whites = None
        for key, (image, x, y, when) in list(self._placed.items()):
            if key in shown:
                continue
            if whites is None:
                whites = np.asarray(frame.image.convert("RGB")).min(axis=2) >= 230  # letras blancas en la captura
            if moved:
                y = y + int(round(dy))
            left, top = x - frame.region.left, y - frame.region.top
            inside = 0 <= top and top + image.height <= frame.image.height + 6
            still_there = inside and whites[max(0, top):top + image.height, max(0, left):left + image.width].sum() >= 4
            # No se sostiene encima de otra traducción que ya ocupa ese renglón.
            taken = any(abs(other[2] - y) < image.height * 0.6 and other[1] < x + image.width and x < other[1] +
                        other[0].width for other_key, other in self._placed.items() if other_key in shown)
            if now - when <= self.HOLD_S and still_there and not taken:
                shown.add(key)
                self._placed[key] = (image, x, y, when)
                self.last_patches.append((image, left, top))
                if moved:
                    self.layer.show(key, image, x, y)
            else:
                del self._placed[key]
        self.layer.keep_only(shown)

    def shift(self, dy: int) -> None:
        """El chat se desplazó `dy` px (llegó un mensaje y todo subió): las traducciones se mueven ya, sin esperar
        la próxima lectura con OCR. Antes quedaban corridas una línea (encima del mensaje de al lado) hasta ~200 ms."""
        frame = self.last_frame
        if frame is None or not self._placed:
            return
        region = frame.region
        for key, (image, x, y, when) in list(self._placed.items()):
            y += dy
            if y < region.top - 2 or y + image.height > region.bottom + 6:
                del self._placed[key]  # salió del chat
                continue
            self._placed[key] = (image, x, y, when)
            self.layer.show(key, image, x, y)
        self.layer.keep_only(set(self._placed))
        self._positions = {key: top + dy for key, top in self._positions.items()}
        self.last_patches = [(image, x - region.left, y - region.top) for image, x, y, _ in self._placed.values()]

    @staticmethod
    def _pill(spot: PillSpot, line: str, size: int) -> Image.Image:
        text_width = _font(size, line).getlength(line) if line else 0
        width = max(spot.cover_right - spot.left, int(text_width) + TEXT_INSET + RIGHT_PAD)
        width = min(width, spot.max_right - spot.left)
        return render_pill(width, spot.height, line, size, fill=STYLE.fill, text_color=CHAT_TEXT, accent=STYLE.accent)

    def preview(self) -> Image.Image | None:
        """Cómo se ve el chat con las traducciones encima. Las traducciones no salen en capturas de pantalla
        (a propósito, para que el OCR no se lea a sí mismo), así que esta es la forma de verlas."""
        if self.last_frame is None:
            return None
        image = self.last_frame.image.convert("RGBA")
        for patch, left, top in self.last_patches:
            image.alpha_composite(patch, (max(0, left), max(0, top)))
        return image.convert("RGB")


# ---------------------------------------------------------------- burbujas
class BubbleView:
    """Traducción encima de las burbujas de chat de los jugadores (fondo claro, letra oscura)."""

    def __init__(self, root, same_message: Callable[[ChatLine, ChatLine], bool]) -> None:
        self.layer = PatchLayer(root)
        self.same_message = same_message
        self.entries: deque[Entry] = deque(maxlen=200)
        self._keys = itertools.count(1_000_000)
        self.images = ImageCache()
        self._pairs: list = []
        self._texts: dict[int, str] = {}
        self._area = None
        self._visible = False
        self._resolve: Callable[[Entry], str] = lambda entry: ""
        self.last_patches: list[tuple[Image.Image, int, int]] = []
        # Lo último mostrado de cada burbuja (imagen, x, y, cuándo, velocidad): si en una detección no aparece (el OCR
        # la leyó distinta o no la vio), se sostiene un instante en vez de apagarse y prenderse.
        self._recent: dict[int, tuple[Image.Image, int, int, float, float, float]] = {}

    def reset(self, same_message: Callable[[ChatLine, ChatLine], bool] | None = None) -> None:
        self.layer.hide_all()
        if same_message is not None:
            self.same_message = same_message
        self.entries.clear()
        self._pairs, self._texts, self._area = [], {}, None
        self._recent.clear()
        self.last_patches = []

    def entry_for(self, text: str) -> tuple[Entry, bool]:
        """Entrada de esa burbuja; True si es nueva (hay que traducirla)."""
        for entry in reversed(self.entries):
            if self.same_message(ChatLine("?", entry.original), ChatLine("?", text)):
                return entry, False
        entry = Entry(next(self._keys), "", text)
        self.entries.append(entry)
        return entry, True

    def render(self, area, items, visible: bool, resolve: Callable[[Entry], str]) -> None:
        """Nueva detección de burbujas. `resolve(entry)` devuelve la traducción a mostrar ("" si todavía no
        hay o no hace falta). Se resuelve acá, una vez por detección, no en cada cuadro de la animación."""
        self._area, self._visible, self._resolve = area, visible, resolve
        self._pairs = [(item, self.entry_for(item.text)[0]) for item in items]
        self._texts = {entry.key: resolve(entry) for _item, entry in self._pairs}
        self._draw()

    def animate(self) -> None:
        """Entre detecciones mueve las traducciones según la velocidad de cada burbuja, así la siguen de
        forma continua con la cámara. Solo mueve ventanas: no redibuja."""
        if self._pairs and self._visible:
            self._draw()

    def _draw(self) -> None:
        area, visible = self._area, self._visible
        self.last_patches = []
        if area is None or not visible:
            self.layer.hide_all()
            self._recent.clear()
            return
        shown: set = set()
        for item, entry in self._pairs:
            text = self._texts.get(entry.key, "")
            if not text:
                continue
            speed = (item.vx ** 2 + item.vy ** 2) ** 0.5
            if speed > MAX_BUBBLE_SPEED:
                self._recent.pop(entry.key, None)  # tampoco se sostiene: se movería mal
                continue  # giro brusco de cámara: mejor ocultar un instante que dejarla fuera de lugar
            # Se predice dónde está la burbuja AHORA (se capturó hace unos milisegundos y se viene moviendo). Si está
            # tapada en parte (detrás del chat) no se predice: el borde del chat no se mueve.
            ahead = 0.0
            if item.captured_at and not item.clip:
                ahead = min(0.25, max(0.0, time.monotonic() - item.captured_at) + RENDER_DELAY_S)
            left = item.left + 1 + int(item.vx * ahead)
            top = item.top + 1 + int(item.vy * ahead)
            width, height = item.right - item.left - 2, item.bottom - item.top - 2
            if width < 10 or height < 8:
                continue
            image = self.images.get(
                entry.key, (text, width // 3, height // 3),  # cambios de 1-2 px no redibujan
                lambda item=item, text=text, width=width, height=height: self._bubble_image(item, text, width, height),
            )
            # Si la traducción no entraba, la burbuja es más grande: centrada y apoyada abajo (sobre la colita).
            left += (width - image.width) // 2
            top += height - image.height
            if item.clip:
                # Solo la parte que se ve de la burbuja: la traducción queda tapada igual que el original.
                x0, y0 = max(0, item.clip[0] - left), max(0, item.clip[1] - top)
                x1, y1 = min(image.width, item.clip[2] - left), min(image.height, item.clip[3] - top)
                if x1 - x0 < 12 or y1 - y0 < 8:
                    continue
                image = image.crop((x0, y0, x1, y1))
                left, top = left + x0, top + y0
            shown.add(entry.key)
            self.last_patches.append((image, area.left + left, area.top + top))
            self.layer.show(entry.key, image, area.left + left, area.top + top)
            self._recent[entry.key] = (image, area.left + left, area.top + top, time.monotonic(), item.vx, item.vy)
        self._hold_missing(shown)
        self.layer.keep_only(shown)

    HOLD_S = 0.4

    def _hold_missing(self, shown: set) -> None:
        now = time.monotonic()
        boxes = [(x, y, x + image.width, y + image.height) for key, (image, x, y, *_rest) in self._recent.items()
                 if key in shown]
        for key, (image, x, y, when, vx, vy) in list(self._recent.items()):
            if key in shown:
                continue
            age = now - when
            if age > self.HOLD_S:
                del self._recent[key]
                continue
            nx, ny = int(x + vx * age), int(y + vy * age)
            # No encima de otra traducción (la burbuja pudo haber cambiado de mensaje).
            if any(nx < b[2] and b[0] < nx + image.width and ny < b[3] and b[1] < ny + image.height for b in boxes):
                continue
            shown.add(key)
            self.layer.show(key, image, nx, ny)

    @staticmethod
    def _bubble_image(item, text: str, width: int, height: int) -> Image.Image:
        size, lines, out_width, out_height = fit_bubble_text(text, width, height, max(1, item.rows))
        return render_bubble((out_width, out_height), lines, size, item.background, item.foreground,
                             radius=min(height // 2, 12))
