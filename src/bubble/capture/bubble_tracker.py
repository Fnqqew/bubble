"""Seguimiento rápido de las burbujas de chat de los jugadores, también con la cámara en movimiento.

Leer texto (OCR) tarda ~150-400 ms, demasiado para seguir una burbuja que se mueve con la cámara. Por eso:
1. Unas 8 veces por segundo se buscan en la pantalla rectángulos claros, sin color y con texto oscuro
   adentro (la forma de una burbuja). Esto no lee texto: toma ~20 ms.
2. Cada burbuja encontrada se asocia con la de la captura anterior (misma forma, cerca): así la traducción
   la sigue aunque la cámara se mueva.
3. Solo cuando aparece una burbuja nueva (o cambia de tamaño porque cambió el texto) se lee su texto con OCR,
   sobre el recorte agrandado, que se lee mucho mejor que la pantalla completa.
"""

from __future__ import annotations

import asyncio
from collections import deque
import itertools
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from PIL import Image, ImageChops

from ..geometry import Rect
from ..performance import Pacer, Stopwatch
from .bubbles import BubbleItem
from .ocr import WindowsOcr
from .screen import grab, reading_mark, still_readable

log = logging.getLogger(__name__)

SCALE = 0.5  # la búsqueda de formas se hace a media resolución (4 veces menos píxeles)
SIGNATURE_SIZE = (24, 6)
# Diferencia de huella a partir de la cual el texto de la burbuja cambió (el jugador dijo otra cosa).
SIGNATURE_CHANGED = 0.08  # misma burbuja movida: 0; agrandada/achicada (zoom): ~0.04-0.065; otro texto: >= 0.17


def _ndimage():
    """scipy tarda ~1 s en cargarse: se carga recién cuando hace falta (así Bubble abre rápido)."""
    from scipy import ndimage

    return ndimage


@dataclass
class BubbleBox:
    left: int
    top: int
    right: int
    bottom: int
    background: tuple[int, int, int]
    foreground: tuple[int, int, int]
    # "Huella" del texto: la burbuja achicada a 24x6 en escala de grises. Sirve para saber si es el mismo
    # mensaje (aunque la cámara se mueva) sin leerlo con OCR.
    signature: np.ndarray | None = field(default=None, repr=False, compare=False)
    # Bordes cortados (izquierda, arriba, derecha, abajo): la burbuja sigue detrás del chat o fuera de la pantalla,
    # así que lo que se ve no es la burbuja entera (ni su huella, ni su texto completo).
    clipped: tuple[bool, bool, bool, bool] = (False, False, False, False)

    @property
    def is_clipped(self) -> bool:
        return any(self.clipped)

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.top + self.bottom) / 2


def _signature(gray: Image.Image) -> np.ndarray:
    """Huella del texto de una burbuja: escala de grises reducida a 24x6 (promediando por zonas, a resolución
    completa: no depende de cómo cae la burbuja en la grilla de media resolución) y con el contraste normalizado,
    así el mismo mensaje da casi la misma huella aunque la burbuja se vea más grande o más chica."""
    thumb = gray.resize(SIGNATURE_SIZE, Image.Resampling.BOX)
    values = np.asarray(thumb, dtype=np.float32)
    low, high = float(values.min()), float(values.max())
    return (values - low) / (high - low) if high - low > 1 else np.zeros_like(values)


def _text_signature(gray: Image.Image) -> np.ndarray:
    """Huella anclada al texto: el recuadro exacto de las letras (a resolución completa), no el de la burbuja, que
    se mide de a 2 px y hacía variar la huella de la misma burbuja al moverse la cámara."""
    letters = np.asarray(gray) < 140
    # Solo adentro: en los bordes y las esquinas redondeadas se ve el fondo (árboles oscuros) y no son letras.
    margin_x, margin_y = min(8, gray.width // 6), min(4, gray.height // 5)
    letters[:margin_y], letters[len(letters) - margin_y:] = False, False
    letters[:, :margin_x], letters[:, letters.shape[1] - margin_x:] = False, False
    rows, cols = np.flatnonzero(letters.any(axis=1)), np.flatnonzero(letters.any(axis=0))
    if len(rows) and len(cols):
        gray = gray.crop((int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1))
    return _signature(gray)


def signature_distance(a: np.ndarray | None, b: np.ndarray | None) -> float:
    if a is None or b is None:
        return 0.0
    return float(np.abs(a - b).mean())


def _same_fill(pixels: np.ndarray, luma: np.ndarray, mask: np.ndarray, tolerance: int = 24) -> np.ndarray:
    """Píxeles del mismo color que lo más claro de la mancha (el relleno de la burbuja)."""
    brightest = luma[mask] >= np.percentile(luma[mask], 75)
    reference = np.median(pixels[mask][brightest], axis=0).astype(np.int16)
    return np.abs(pixels.astype(np.int16) - reference).max(axis=2) <= tolerance


def _spread(mask: np.ndarray, op, pad: bool) -> np.ndarray:
    """3x3 de una: cada píxel con sus vecinos de fila y después de columna (dos pasadas en vez de nueve)."""
    p = np.pad(mask, 1, constant_values=pad)
    rows = op(op(p[1:-1, :-2], p[1:-1, 1:-1]), p[1:-1, 2:])
    p = np.pad(rows, ((1, 1), (0, 0)), constant_values=pad)
    return op(op(p[:-2], p[1:-1]), p[2:])


def close_3x3(mask: np.ndarray) -> np.ndarray:
    """Lo mismo que scipy `binary_closing` con un cuadrado de 3x3 (igual hasta en los bordes), ~7 veces más rápido:
    era la parte más cara de buscar burbujas y la traducción quedaba atrás de la burbuja al girar la cámara."""
    return _spread(_spread(mask, np.logical_or, False), np.logical_and, False)


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """Rellena lo encerrado por la mancha (el texto dentro de la burbuja). Una sola pasada de etiquetado: mucho
    más rápido que `binary_fill_holes`, que dilata una y otra vez."""
    outside, _count = _ndimage().label(~np.pad(mask, 1))
    border = np.unique(np.concatenate((outside[0], outside[-1], outside[:, 0], outside[:, -1])))
    return ~np.isin(outside, border)[1:-1, 1:-1] | mask


def find_bubble_boxes(image: Image.Image, exclude: Rect | None = None) -> list[BubbleBox]:
    """Rectángulos con forma de burbuja: claros, sin color, bien rectangulares y con letra oscura adentro."""
    small = (image if image.mode == "RGB" else image.convert("RGB")).reduce(int(round(1 / SCALE)))
    pixels = np.asarray(small)
    # Brillo y saturación calculados por PIL en C, en 8 bits (varias veces más rápido que con numpy en int32).
    red, green, blue = small.split()
    luma = np.asarray(small.convert("L"))
    saturation = np.asarray(ImageChops.subtract(
        ImageChops.lighter(ImageChops.lighter(red, green), blue),
        ImageChops.darker(ImageChops.darker(red, green), blue),
    ))
    bright = (luma > 190) & (saturation < 45)
    dark = luma < 140
    # Cerrar los cortes que deja el texto oscuro dentro de la burbuja (el relleno fino se hace por zona).
    closed = close_3x3(bright)
    labels, _count = _ndimage().label(closed)
    min_width = int(28 * SCALE)
    boxes = []
    for index, region in enumerate(_ndimage().find_objects(labels), start=1):
        if region is None:
            continue
        rows, cols = region
        if (rows.stop - rows.start) / SCALE < 14 or (cols.stop - cols.start) / SCALE < 28:
            continue
        mask = labels[region] == index
        # El relleno de una burbuja es de un color parejo (blanco). Una pared o casa clara pegada tiene otro tono:
        # se deja solo lo del color de lo más claro de la mancha, así la burbuja no gana ni pierde filas según
        # cómo quede la cámara (eso cambiaba su huella y la traducción parpadeaba).
        mask = _fill_holes(mask & _same_fill(pixels[region], luma[region], mask))  # el texto queda "adentro"
        # La mancha clara puede juntar varias burbujas apiladas (el mismo jugador escribió varias veces, unidas
        # por la colita) o una burbuja con algo claro detrás (una pared blanca): se separa cada rectángulo.
        for top, bottom, left, right in _rectangles(mask, min_width):
            box = _bubble_in(mask[top:bottom, left:right], rows.start + top, cols.start + left, pixels, luma, bright, dark)
            if box is None:
                continue
            if exclude and exclude.left <= box.center[0] <= exclude.right and exclude.top <= box.center[1] <= exclude.bottom:
                continue
            box.clipped = _clipping(box, image.width, image.height, exclude)
            box.signature = _text_signature(image.crop((box.left, box.top, box.right, box.bottom)).convert("L"))
            boxes.append(box)
    return boxes


def _clipping(box: BubbleBox, width: int, height: int, chat: Rect | None) -> tuple[bool, bool, bool, bool]:
    """Qué bordes de la burbuja están cortados: contra el borde de la pantalla o contra el panel del chat (que se
    dibuja encima de las burbujas; el panel es un poco más grande que la zona de mensajes calibrada)."""
    left, top = box.left <= 3, box.top <= 3
    right, bottom = box.right >= width - 3, box.bottom >= height - 3
    if chat is not None:
        panel_top, panel_bottom = chat.top - 40, chat.bottom + 50
        rows = box.top < panel_bottom and box.bottom > panel_top
        cols = box.left < chat.right + 8 and box.right > chat.left - 8
        left = left or (rows and chat.right - 4 <= box.left <= chat.right + 16)
        right = right or (rows and chat.left - 16 <= box.right <= chat.left + 4)
        top = top or (cols and chat.bottom - 4 <= box.top <= panel_bottom + 8)
        bottom = bottom or (cols and panel_top - 8 <= box.bottom <= chat.top + 4)
    return left, top, right, bottom


def _longest_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """(inicio, fin) del tramo seguido más largo de cada fila ((0, 0) si la fila está vacía), todo de una vez."""
    steps = np.diff(np.pad(mask, ((0, 0), (1, 1))).astype(np.int8), axis=1)
    rows, starts = np.nonzero(steps == 1)
    _rows, ends = np.nonzero(steps == -1)  # mismo orden: cada tramo empieza y termina en su fila
    order = np.lexsort((starts - ends, rows))  # por fila y, dentro de la fila, el más largo primero
    first = order[np.r_[True, rows[order][1:] != rows[order][:-1]]] if len(order) else order
    result = np.zeros((mask.shape[0], 2), dtype=np.int64)
    result[rows[first], 0], result[rows[first], 1] = starts[first], ends[first]
    return [(int(a), int(b)) for a, b in result]


def _rectangles(
    mask: np.ndarray, min_width: int, tolerance: int = 3, max_wider_rows: int = 3, side_share: float = 0.35
) -> list[tuple[int, int, int, int]]:
    """Rectángulos (arriba, abajo, izquierda, derecha) formados por filas seguidas que cubren las mismas columnas.

    De cada fila se toma su tramo claro más largo. Las esquinas redondeadas van ensanchando el rectángulo de a
    poco. Una fila que coincide en un borde y sobresale un poco del otro es la burbuja con algo claro pegado a un
    costado (una pared detrás): sigue siendo la burbuja, sin agrandarla. Unas pocas filas más anchas por los dos
    lados tampoco la cortan; si siguen más filas así, es otro rectángulo (otra burbuja, más ancha, justo debajo).
    La colita y el espacio entre burbujas apiladas (filas angostas) también cortan.
    """
    found: list[list[int]] = []
    current: list[int] | None = None  # [arriba, abajo, izquierda, derecha]
    wider: list[int] | None = None  # filas seguidas más anchas que `current` (candidato a otro rectángulo)
    for row, (start, end) in enumerate(_longest_runs(mask)):
        if end - start < min_width:
            current = wider = None
            continue
        near = 2 * tolerance  # esquinas redondeadas (no siempre simétricas)
        if current and abs(start - current[2]) <= near and abs(end - current[3]) <= near:
            current[1] = row + 1
            if abs(start - current[2]) <= tolerance and abs(end - current[3]) <= tolerance:
                current[2], current[3] = min(current[2], start), max(current[3], end)  # esquina: se ensancha de a poco
            wider = None
            continue
        if current:
            width = current[3] - current[2]
            same_right, same_left = abs(end - current[3]) <= 1, abs(start - current[2]) <= 1
            left_extra = same_right and near < current[2] - start <= side_share * width
            right_extra = same_left and near < end - current[3] <= side_share * width
            if left_extra or right_extra:
                current[1] = row + 1
                wider = None
                continue
            # Al revés: coincide en un borde y del otro termina claramente antes. Lo que venía siendo más ancho era
            # algo claro pegado a un costado de la burbuja que ya terminó: la burbuja es esta, desde las filas de arriba.
            left_less = same_right and near < start - current[2] <= side_share * width
            right_less = same_left and near < current[3] - end <= side_share * width
            if left_less or right_less:
                current[1], current[2], current[3] = row + 1, start, end
                wider = None
                continue
        if current and start <= current[2] + tolerance and end >= current[3] - tolerance:
            if wider and abs(start - wider[2]) <= tolerance and abs(end - wider[3]) <= tolerance:
                wider[1] = row + 1
            else:
                wider = [row, row + 1, start, end]
            if wider[1] - wider[0] <= max_wider_rows:
                current[1] = row + 1
                continue
            current[1] = wider[0]  # lo más ancho sigue: es otro rectángulo, desde donde empezó
            current, wider = wider, None
            found.append(current)
            continue
        if (current and current[1] - current[0] <= max_wider_rows
                and start >= current[2] - tolerance and end <= current[3] + tolerance):
            current[1], current[2], current[3] = row + 1, start, end  # las primeras filas tenían algo claro detrás
            wider = None
            continue
        if current and wider and wider[1] == row and start >= wider[2] - near and end <= wider[3] + near:
            # Las pocas filas "más anchas" que se sumaron al rectángulo de arriba eran el principio de este (una
            # burbuja que empieza pegada a algo claro): vuelven a este.
            current[1] = wider[0]
            current = [wider[0], row + 1, start, end]
        else:
            current = [row, row + 1, start, end]
        wider = None
        found.append(current)  # se sigue completando mientras las filas de abajo lo continúen
    return [tuple(r) for r in found if r[1] - r[0] >= 3]


def _bubble_in(
    body_mask: np.ndarray, top: int, left: int, pixels: np.ndarray, luma: np.ndarray, bright: np.ndarray, dark: np.ndarray
) -> BubbleBox | None:
    """La burbuja de un rectángulo de la mancha clara, si tiene forma de burbuja (a media resolución)."""
    bottom, right = top + body_mask.shape[0], left + body_mask.shape[1]
    full_h, full_w = (bottom - top) / SCALE, (right - left) / SCALE
    if not (14 <= full_h <= 320 and 28 <= full_w <= 1000 and full_w >= 1.1 * full_h):
        return None
    if body_mask.mean() < 0.8:  # no es un rectángulo (manchas, cielo, paredes con formas)
        return None
    body_region = (slice(top, bottom), slice(left, right))
    dark_share = (dark[body_region] & body_mask).mean()
    if not 0.015 <= dark_share <= 0.4:  # sin texto, o demasiado oscuro para ser una burbuja
        return None
    return BubbleBox(
        int(left / SCALE), int(top / SCALE), int(right / SCALE), int(bottom / SCALE),
        tuple(int(v) for v in pixels[body_region][body_mask & bright[body_region]].mean(axis=0)),
        tuple(int(v) for v in pixels[body_region][dark[body_region] & body_mask].min(axis=0)),
    )


_WORDS = re.compile(r"[^\W\d_]+")
_ICON_LETTERS = set("ceilrjt")  # el ícono de parlante de quien habla por voz se lee "clil", "elil", "c(ll", "rill"


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORDS.findall(text)]


def usable_bubble_text(text: str) -> bool:
    """¿Puede ser un mensaje? No lo son el ícono de parlante ("clil"), un contador del juego ("$333 (06:54)") ni un
    nombre con sus puntos leído sobre algo claro ("GRO (+3,815)", "I GRO", "GR04")."""
    from ..translate.langdetect import known_anywhere

    words = [w for w in _WORDS.findall(text) if len(w) >= 2]
    if not words:
        return False
    letters = "".join(words).lower()
    if len(letters) <= 5 and set(letters) <= _ICON_LETTERS:
        return False
    if sum(c.isdigit() for c in text) > len(letters):
        return False
    shortish = all(len(w) <= 4 and not known_anywhere(w) for w in words)
    looks_like_a_name = "(" in text or any(c.isdigit() for c in text) or all(w.isupper() for w in words)
    return not (shortish and looks_like_a_name)


def fragment_of(part: str, whole: str) -> bool:
    """`part` es un pedazo (o una lectura rota) de `whole`: la burbuja quedó tapada en parte (otra burbuja, una
    cabeza, una puerta) y el OCR leyó solo lo que se veía. Antes eso reemplazaba al texto entero y se traducía
    cortado. Con las mismas palabras y alguna rota ("She ved in"), se queda la lectura de antes, la de la burbuja
    entera."""
    part_words, whole_words = _words(part), _words(whole)
    if not part_words or len(part_words) > len(whole_words):
        return False  # más largo: la burbuja creció o es otro mensaje
    known = set(whole_words)
    return sum(w in known for w in part_words) / len(part_words) >= 0.7


class BubbleTexts:
    """Qué texto le queda a cada burbuja cada vez que el OCR la relee."""

    RECENT_S = 20.0

    def __init__(self) -> None:
        self._recent: deque[tuple[float, str, int]] = deque(maxlen=40)

    def settle(self, current: str, current_rows: int, read: str, rows: int, now: float) -> tuple[str, int]:
        """(texto, líneas) con los que queda la burbuja después de leer `read`."""
        if not usable_bubble_text(read):
            return current, current_rows
        if current and fragment_of(read, current):
            return current, current_rows  # se leyó la mitad: queda lo que ya se había leído entero
        for when, text, text_rows in reversed(self._recent):
            if now - when <= self.RECENT_S and fragment_of(read, text):
                return text, text_rows  # parte de una burbuja que se leyó entera hace poco (otra pista)
        self._recent.append((now, read, rows))
        return read, rows


@dataclass
class Track:
    id: int
    # La burbuja entera. Si está tapada en parte (detrás del chat, contra el borde), se estima con el último tamaño
    # que se le vio completo; lo que se ve de verdad queda en `visible`.
    box: BubbleBox
    last_seen: float
    text: str = ""
    rows: int = 1
    ocr_signature: np.ndarray | None = field(default=None, repr=False)  # huella cuando se leyó el texto
    reading: bool = False
    # Velocidad en pantalla (px/s), para ubicar la traducción donde va a estar la burbuja.
    vx: float = 0.0
    vy: float = 0.0
    visible: BubbleBox | None = None
    captured_at: float = 0.0  # time.monotonic() de la captura en que se vio por última vez
    read_at: float = -1e9  # último intento de lectura

    @property
    def clipped(self) -> bool:
        return self.visible is not None and self.visible.is_clipped

    def _distance(self) -> float:
        return signature_distance(self.box.signature, self.ocr_signature)

    @property
    def text_changed(self) -> bool:
        """La burbuja parece mostrar otro mensaje que el que se leyó (hay que volver a leerla). Tapada en parte no
        se puede saber: se mantiene lo leído."""
        return not self.clipped and self._distance() > SIGNATURE_CHANGED

    @property
    def current_text(self) -> str:
        """Texto leído mientras siga siendo el de la burbuja. Una diferencia chica de huella (otra luz, otro
        tamaño) no lo oculta mientras se relee; una grande sí (es otro mensaje: no se muestra una traducción vieja)."""
        if not self.clipped and self._distance() > SIGNATURE_OTHER:
            return ""
        return self.text

    def needs_ocr(self, now: float | None = None) -> bool:
        if self.reading or self.clipped:
            return False  # tapada en parte: se leería la mitad del texto; se espera a que se vea entera
        if self.text:
            return self.text_changed
        if self.ocr_signature is None:
            return True  # nunca se leyó
        # Se leyó y no tenía texto (algo claro que parece burbuja): solo se reintenta si cambió o de a ratos.
        return self._distance() > SIGNATURE_CHANGED or (now is not None and now - self.read_at > EMPTY_RETRY_S)


SIGNATURE_OTHER = 0.12  # desde acá es otro texto seguro: la traducción vieja se oculta enseguida
EMPTY_RETRY_S = 1.5
GRACE_S = 0.35  # una burbuja que no se detectó en una captura suelta sigue mostrando su traducción este tiempo


def _edge_offset(old: BubbleBox, box: BubbleBox) -> tuple[float, float]:
    """Cuánto se movió la burbuja, mirando los bordes que se ven (si está tapada de un lado, el centro engaña)."""
    left, top, right, bottom = box.clipped
    if left and not right:
        dx = box.right - old.right
    elif right and not left:
        dx = box.left - old.left
    elif left and right:
        dx = 0.0
    else:
        dx = box.center[0] - old.center[0]
    if top and not bottom:
        dy = box.bottom - old.bottom
    elif bottom and not top:
        dy = box.top - old.top
    elif top and bottom:
        dy = 0.0
    else:
        dy = box.center[1] - old.center[1]
    return dx, dy


def _full_box(old: BubbleBox, box: BubbleBox) -> BubbleBox:
    """La burbuja entera a partir de lo que se ve: el tamaño completo que tenía y el borde que no está tapado."""
    left, top, right, bottom = box.clipped
    width = old.width if (left or right) else box.width
    height = old.height if (top or bottom) else box.height
    x = box.right - width if left and not right else box.left
    y = box.bottom - height if top and not bottom else box.top
    return BubbleBox(int(x), int(y), int(x + width), int(y + height), box.background, box.foreground,
                     signature=old.signature)


class BubbleTracker:
    """Asocia las burbujas de cada captura con las de la anterior: cerca, de tamaño parecido y, sobre todo,
    con la misma huella de texto. Así, cuando un jugador escribe otra vez (la burbuja vieja sube y aparece una
    nueva abajo), la vieja conserva su traducción y la nueva se lee y traduce aparte. Una burbuja que pasa por
    detrás del chat (o sale por el borde de la pantalla) sigue siendo la misma, con su texto."""

    def __init__(self, max_jump: float = 260, forget_s: float = 0.6, clock: Callable[[], float] = time.monotonic):
        self.max_jump = max_jump
        self.forget_s = forget_s
        self.clock = clock
        self.tracks: list[Track] = []
        self._ids = itertools.count(1)

    def _cost(self, track: Track, box: BubbleBox) -> float | None:
        old = track.box
        left, top, right, bottom = box.clipped
        if (left or right) and box.width > 1.25 * old.width:
            return None  # lo que se ve no puede ser más grande que la burbuja entera
        dx, dy = _edge_offset(old, box)
        jump = (dx * dx + dy * dy) ** 0.5
        size_change = (0 if left or right else abs(old.width - box.width) / max(old.width, 1)) + (
            0 if top or bottom else abs(old.height - box.height) / max(old.height, 1))
        if jump > self.max_jump or size_change > 0.9:
            return None
        # La huella pesa mucho: una burbuja con el mismo texto que subió 100 px es "la misma", no la que quedó
        # en su lugar con otro texto. Tapada en parte, la huella no sirve para comparar.
        signature = 0.0 if box.is_clipped else signature_distance(old.signature, box.signature)
        return jump + 200 * size_change + 2500 * signature

    def update(self, boxes: list[BubbleBox], captured_at: float | None = None) -> list[Track]:
        now = self.clock()
        captured_at = now if captured_at is None else captured_at
        # Emparejamiento global: primero los pares más parecidos (no el primero que aparece).
        pairs = sorted(
            (cost, t_index, b_index)
            for t_index, track in enumerate(self.tracks)
            for b_index, box in enumerate(boxes)
            if (cost := self._cost(track, box)) is not None
        )
        used_tracks: set[int] = set()
        used_boxes: set[int] = set()
        for _cost, t_index, b_index in pairs:
            if t_index in used_tracks or b_index in used_boxes:
                continue
            used_tracks.add(t_index)
            used_boxes.add(b_index)
            track, box = self.tracks[t_index], boxes[b_index]
            full = _full_box(track.box, box) if box.is_clipped else box
            dt = captured_at - track.captured_at
            if dt > 0:
                (ox, oy), (nx, ny) = track.box.center, full.center
                # Promedio suave: una sola captura rara no cambia mucho la velocidad estimada.
                track.vx = 0.4 * track.vx + 0.6 * (nx - ox) / dt
                track.vy = 0.4 * track.vy + 0.6 * (ny - oy) / dt
            track.box, track.visible, track.last_seen, track.captured_at = full, box, now, captured_at
        for b_index, box in enumerate(boxes):
            if b_index not in used_boxes:
                self.tracks.append(Track(next(self._ids), box, now, visible=box, captured_at=captured_at))
        # Una burbuja que no se ve por un momento (otro objeto la tapó) no se olvida enseguida.
        self.tracks = [t for t in self.tracks if now - t.last_seen <= self.forget_s]
        return [t for t in self.tracks if t.last_seen == now]

    def recent(self, max_age: float) -> list[Track]:
        """Las que se vieron hace muy poco (incluye las que faltaron en una sola captura)."""
        now = self.clock()
        return [t for t in self.tracks if now - t.last_seen <= max_age]


class BubbleWatcher:
    """Busca y sigue burbujas varias veces por segundo mientras Roblox está en primer plano."""

    def __init__(
        self,
        ocr: WindowsOcr,
        game_area: Callable[[], Rect | None],
        chat_area: Callable[[], Rect | None],
        on_bubbles: Callable[[Rect | None, list[BubbleItem]], None],
        interval_s: float = 0.12,
        pacer: Pacer | None = None,
    ) -> None:
        self.ocr = ocr
        self.game_area = game_area
        self.chat_area = chat_area
        self.on_bubbles = on_bubbles
        self.interval_s = interval_s
        # Con `pacer`, cada cuánto se buscan burbujas se adapta a lo que cuesta en esta PC.
        self.pacer = pacer
        self.tracker = BubbleTracker()
        self.texts = BubbleTexts()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None
        self.on_bubbles(None, [])

    async def _read_text(self, track: Track, image: Image.Image) -> None:
        box = track.box  # la burbuja tal como estaba en esta captura (huella incluida)
        track.reading = True
        try:
            crop = image.crop((box.left, box.top, box.right, box.bottom))
            rows = await self.ocr.recognize(crop)  # recorte chico: se agranda x2 y se lee mucho mejor
            text = " ".join(r.text for r in rows).strip()
            track.text, track.rows = self.texts.settle(track.text, track.rows, text, max(1, len(rows)),
                                                       time.monotonic())
            track.ocr_signature = box.signature  # también si no tenía texto: no se relee en cada captura
        except Exception:  # noqa: BLE001 - se reintenta en la próxima captura
            log.debug("No se pudo leer una burbuja", exc_info=True)
        finally:
            track.reading = False

    @staticmethod
    def _item(track: Track) -> BubbleItem:
        box, seen = track.box, track.visible
        clip = (seen.left, seen.top, seen.right, seen.bottom) if seen is not None and seen.is_clipped else None
        return BubbleItem(track.current_text, box.left, box.top, box.right, box.bottom, box.background,
                          box.foreground, track.rows, track.vx, track.vy, track.captured_at, clip)

    async def _run(self) -> None:
        while True:
            try:
                area = await asyncio.to_thread(self.game_area)
                if area is None:
                    self.on_bubbles(None, [])
                    await asyncio.sleep(0.4)
                    continue
                mark = reading_mark()
                if mark is None:
                    await asyncio.sleep(0.05)  # estás sacando una captura: las traducciones se ven en ella
                    continue
                captured_at = time.monotonic()
                watch = Stopwatch()
                image = await asyncio.to_thread(watch.cpu, grab, area)
                if not still_readable(mark):
                    continue
                chat = await asyncio.to_thread(self.chat_area)
                exclude = chat.offset(-area.left, -area.top) if chat else None
                boxes = await asyncio.to_thread(watch.cpu, find_bubble_boxes, image, exclude)
                if self.pacer:
                    self.pacer.record(watch.seconds)
                now = self.tracker.clock()
                for track in self.tracker.update(boxes, captured_at):
                    if track.needs_ocr(now):
                        track.read_at = now
                        asyncio.get_running_loop().create_task(self._read_text(track, image))
                # Si cambió el mensaje no se muestra la traducción vieja; si la burbuja faltó en una sola captura,
                # la traducción sigue (se mueve con la velocidad que traía) en vez de parpadear.
                items = [self._item(t) for t in self.tracker.recent(GRACE_S) if t.current_text]
                self.on_bubbles(area, items)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - las burbujas son un extra: se sigue intentando
                log.exception("Error siguiendo burbujas")
                await asyncio.sleep(1.0)
                continue
            # Con burbujas a la vista se sigue rápido; sin ninguna, se busca con menos frecuencia (menos CPU).
            wait = self.pacer.sleep if self.pacer else self.interval_s
            await asyncio.sleep(wait if self.tracker.tracks else max(wait, 0.3))
