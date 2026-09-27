"""Lee el chat de Roblox periódicamente: captura → OCR → mensajes visibles (con ubicación) y nuevos."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, replace
from typing import Callable

import numpy as np
from PIL import Image

from ..geometry import Rect
from ..performance import Pacer, Stopwatch
from ..translate.base import ChatLine
from .chat_parser import ChatItem, ChatTracker, parse_chat_items
from .ocr import (
    WHITE_TEXT, WindowsOcr, binarize_local_background, looks_faded, prepare_chat_image, white_text_bands,
)
from .screen import grab, reading_mark, still_readable

log = logging.getLogger(__name__)


def fingerprint_changed(a: bytes, b: bytes) -> int:
    """Cantidad de píxeles distintos entre dos huellas (muchísimos si no se pueden comparar).

    Se cuentan píxeles y no un porcentaje: un mensaje corto que aparece en un lugar vacío del chat cambia
    muy poco de la imagen, pero tiene que notarse igual.
    """
    if not a or len(a) != len(b):
        return 1 << 30
    return int(np.count_nonzero(np.frombuffer(a, np.uint8) != np.frombuffer(b, np.uint8)))


def text_fingerprint(image: Image.Image) -> bytes:
    """Huella barata del texto del chat, sobre la captura sin preparar: dónde hay letras blancas puras (el texto de
    los mensajes), en bloques de 3x3. El fondo (cielo, pasto, una pared clara que pasa detrás cuando el fondo del
    chat se desvanece) casi nunca es blanco puro: moverse con la cámara no la cambia. Alcanza para saber si el chat
    cambió: leer con OCR cuesta ~50 ms; esto ~1 ms."""
    white = np.asarray(image.convert("RGB")).min(axis=2) >= WHITE_TEXT
    height, width = white.shape[0] // 3 * 3, white.shape[1] // 3 * 3
    blocks = white[:height, :width].reshape(height // 3, 3, width // 3, 3).any(axis=(1, 3))
    return np.packbits(blocks).tobytes()


def _uncovered_bands(image: Image.Image, items: list[ChatItem]) -> list[tuple[int, int]]:
    """Franjas con letras blancas que no salieron como mensaje (ni aviso del sistema)."""
    bands = []
    for top, bottom in white_text_bands(image):
        center = (top + bottom) / 2
        # Una lectura dudosa no cuenta como cubierta: se vuelve a leer para confirmarla (o descartarla).
        if not any(row.top - 4 <= center <= row.bottom + 4 for item in items if not item.uncertain for row in item.rows):
            bands.append((top, bottom))
    return bands


def _uncovered(image: Image.Image, items: list[ChatItem]) -> bool:
    """Hay una línea con letras blancas que no salió como mensaje: la lectura quedó incompleta."""
    return bool(_uncovered_bands(image, items))


def _moved_rows(rows, dy: int):
    """Filas leídas en un recorte, llevadas a la posición en la captura entera."""
    return [replace(row, top=row.top + dy, words=tuple(replace(word, top=word.top + dy) for word in row.words))
            for row in rows]


def _grab_fingerprint(region: Rect) -> tuple[Image.Image, bytes]:
    image = grab(region)
    return image, text_fingerprint(image)


def _ink_blocks(prepared: Image.Image, block: int = 8) -> np.ndarray:
    """Tinta (oscuridad) promedio de cada fila en bloques de `block` px de ancho."""
    ink = 255.0 - np.asarray(prepared, dtype=np.float32)
    width = ink.shape[1] // block * block
    return ink[:, :width].reshape(ink.shape[0], -1, block).mean(axis=2)


def estimate_shift(before: Image.Image, after: Image.Image, max_shift: int = 90) -> int:
    """Cuántos píxeles se movió verticalmente el texto del chat (negativo: subió, llegó un mensaje nuevo).

    Compara las dos imágenes preparadas (letras oscuras sobre blanco) reducidas a bloques de 8 px de ancho: cada
    línea del chat tiene otro texto, así que solo coinciden en el desplazamiento correcto (comparar solo cuánta
    tinta hay por fila no alcanza: líneas de largo parecido se confunden). Toma unos milisegundos: así las
    traducciones se mueven con el chat al instante, sin esperar el OCR. Devuelve 0 si no hay un desplazamiento claro.
    """
    a, b = _ink_blocks(before), _ink_blocks(after)
    height = len(a)
    if b.shape != a.shape or height < 30:
        return 0

    def error(x: np.ndarray, y: np.ndarray) -> float:
        # Solo las filas que mejor coinciden: el mensaje nuevo que apareció (o el que se fue) no tiene con qué
        # coincidir y no debe pesar.
        rows = np.sort(np.abs(x - y).mean(axis=1))
        return float(rows[: max(1, int(len(rows) * 0.8))].mean())

    still = error(a, b)
    best, best_error = 0, still
    for dy in range(-max_shift, max_shift + 1):
        if dy == 0:
            continue
        x, y = (b[dy:], a[:height - dy]) if dy > 0 else (b[:height + dy], a[-dy:])
        if len(x) < height // 3:
            continue
        current = error(x, y)
        if current < best_error:
            best, best_error = dy, current
    return best if abs(best) >= 3 and best_error < 0.5 * still else 0


@dataclass
class ChatFrame:
    """Lo que se ve del chat en una captura: la imagen, dónde está en pantalla y sus mensajes."""

    image: Image.Image
    region: Rect
    items: list[ChatItem]


class ChatWatcher:
    def __init__(
        self,
        ocr: WindowsOcr,
        tracker: ChatTracker,
        region_provider: Callable[[], Rect | None],
        on_message: Callable[[ChatLine], None],
        on_error: Callable[[str], None] = lambda _msg: None,
        interval_s: float = 0.25,
        on_frame: Callable[[ChatFrame | None], None] = lambda _frame: None,
        pacer: Pacer | None = None,
        on_shift: Callable[[int], None] = lambda _dy: None,
    ) -> None:
        self.ocr = ocr
        self.tracker = tracker
        self.region_provider = region_provider
        self.on_message = on_message
        self.on_error = on_error
        self.on_frame = on_frame
        # Apenas el chat se desplaza (llegó un mensaje y todo subió), antes de leerlo con OCR: las traducciones
        # se mueven con él al instante en vez de quedar corridas una línea hasta la próxima lectura.
        self.on_shift = on_shift
        self.interval_s = interval_s
        # Con `pacer`, la espera entre lecturas se adapta a lo que cuesta cada lectura en esta PC.
        self.pacer = pacer
        self._task: asyncio.Task | None = None
        self._wrap_region: Rect | None = None
        self._wrap_right = 0.0
        self._last_parsed: list[ChatItem] | None = None
        self._last_region: Rect | None = None
        self._last_fingerprint: bytes = b""
        self._last_prepared: Image.Image | None = None
        self._last_read_at = 0.0
        self._incomplete_since = 0.0

    RETRY_S = 0.4  # relectura de una lectura incompleta
    RETRY_SLOW_S = 2.0  # si sigue incompleta hace rato (texto que no es un mensaje), de a poco

    def _should_retry(self, now: float) -> bool:
        if not self._incomplete_since:
            return False
        wait = self.RETRY_S if now - self._incomplete_since < 6 else self.RETRY_SLOW_S
        return now - self._last_read_at >= wait

    async def _read(self, image: Image.Image, prepared: Image.Image, region: Rect) -> list[ChatItem]:
        """Lee el chat. Si su fondo oscuro se desvaneció (texto directo sobre el juego), se lee también con otra
        preparación y se suman los mensajes que solo salieron en esa."""
        rows = await self.ocr.recognize(prepared)
        faded = looks_faded(image)
        # Borde donde Roblox corta las líneas largas: el más a la derecha visto en esta región (solo con el fondo
        # oscuro: sobre el juego, un borde de un árbol leído como texto lo correría).
        if region != self._wrap_region:
            self._wrap_region, self._wrap_right = region, 0.0
        if not faded:
            self._wrap_right = max([self._wrap_right, *(r.right for r in rows if r.words)])
        wrap = self._wrap_right if self._wrap_right >= 0.5 * image.width else 0
        parsed = parse_chat_items(rows, self.tracker.is_known_name, image.width, wrap)
        bands = await asyncio.to_thread(_uncovered_bands, image, parsed)
        if bands:
            # Renglones con texto que el OCR no devolvió: pasa sobre todo con dos mensajes idénticos seguidos (el
            # OCR de Windows devuelve uno solo; la otra traducción se apagaba o quedaba corrida un renglón). Se lee
            # cada franja sola: así sale.
            found = []
            heights = sorted(row.height for item in parsed for row in item.rows) or [18.0]
            reach = heights[len(heights) // 2] * 0.8  # un renglón entero alrededor del centro (no media letra)
            for top, bottom in bands[:4]:
                center = (top + bottom) / 2
                y0, y1 = max(0, int(center - reach)), min(prepared.height, int(center + reach))
                band_rows = await self.ocr.recognize(prepared.crop((0, y0, prepared.width, y1)))
                found += parse_chat_items(_moved_rows(band_rows, y0), self.tracker.is_known_name, image.width, wrap)
            extra = [item for item in found if not any(abs(item.top - known.top) < 9 for known in parsed)]
            for item in extra:
                item.uncertain = True  # leído aparte: un mensaje nuevo así se confirma en otra captura
            if extra:
                parsed = sorted(parsed + extra, key=lambda item: item.top)
        if faded and await asyncio.to_thread(_uncovered, image, parsed):
            # Solo si a la primera lectura le faltó algo: la segunda cuesta otro OCR entero.
            other = await asyncio.to_thread(binarize_local_background, image)
            extra = parse_chat_items(await self.ocr.recognize(other), self.tracker.is_known_name, image.width, wrap)
            missing = [item for item in extra if not any(abs(item.top - known.top) < 9 for known in parsed)]
            for item in missing:
                item.uncertain = True  # puede ser texto mal leído: un mensaje nuevo así se confirma con otra lectura
            parsed = sorted(parsed + missing, key=lambda item: item.top)
        return parsed

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
        self.on_frame(None)

    async def _run(self) -> None:
        last_error = ""
        while True:
            try:
                region = await asyncio.to_thread(self.region_provider)
                if region is None or region.width < 20 or region.height < 20:
                    self.on_frame(None)  # saliste de Roblox: se ocultan las traducciones
                    await asyncio.sleep(0.5)
                    continue
                mark = reading_mark()
                if mark is None:
                    await asyncio.sleep(0.05)  # estás sacando una captura: las traducciones se ven en ella
                    continue
                watch = Stopwatch()
                image, fingerprint = await asyncio.to_thread(watch.cpu, _grab_fingerprint, region)
                if not still_readable(mark):
                    continue
                now = time.monotonic()
                # Si el texto del chat no cambió (mismas letras), no hace falta volver a leerlo. Pero una lectura
                # incompleta (hay letras que no salieron como mensaje: fondo complicado detrás del chat) se repite cada
                # tanto: con la cámara moviéndose, la próxima suele salir bien.
                same = (self._last_parsed is not None and region == self._last_region
                        and fingerprint_changed(self._last_fingerprint, fingerprint) < 6)
                if same and not self._should_retry(now):
                    parsed = self._last_parsed
                else:
                    prepared = await asyncio.to_thread(watch.cpu, prepare_chat_image, image)
                    if not same and self._last_prepared is not None and region == self._last_region:
                        shift = estimate_shift(self._last_prepared, prepared)
                        if shift:
                            self.on_shift(shift)
                    started = time.perf_counter()
                    parsed = await self._read(image, prepared, region)
                    watch.seconds += time.perf_counter() - started  # el OCR corre en hilos de Windows
                    incomplete = await asyncio.to_thread(watch.cpu, _uncovered, image, parsed)
                    if incomplete and not self._incomplete_since:
                        self._incomplete_since = now
                    elif not incomplete:
                        self._incomplete_since = 0.0
                    self._last_parsed, self._last_region, self._last_fingerprint = parsed, region, fingerprint
                    self._last_prepared, self._last_read_at = prepared, now
                parse_started = time.thread_time()
                # Los avisos del sistema del juego no se traducen ni se tapan.
                items = [item for item in parsed if item.kind == "player"]
                new = self.tracker.update([item.line for item in items], [item.uncertain for item in items])
                # Nombres unificados (misma persona aunque el OCR lea su nombre distinto).
                for item, canonical, origin, uid in zip(items, self.tracker.visible, self.tracker.visible_origins,
                                                        self.tracker.visible_ids):
                    item.speaker, item.origin, item.uid = canonical.speaker, origin, uid
                self.on_frame(ChatFrame(image, region, items))
                for line in new:
                    self.on_message(line)
                watch.seconds += time.thread_time() - parse_started
                if self.pacer:
                    self.pacer.record(watch.seconds)
                last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - se informa y se reintenta
                message = f"Error leyendo el chat: {exc}"
                if message != last_error:
                    log.exception(message)
                    self.on_error(message)
                    last_error = message
                await asyncio.sleep(2.0)
                continue
            await asyncio.sleep(self.pacer.sleep if self.pacer else self.interval_s)
