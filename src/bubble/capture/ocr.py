"""OCR nativo de Windows (Windows.Media.Ocr): rápido (~40 ms), local y sin costo."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import numpy as np
from PIL import Image


def _ndimage():
    """scipy tarda ~1 s en cargarse, por lo que se importa de forma diferida para que Bubble abra rápido."""
    from scipy import ndimage

    return ndimage


@dataclass(frozen=True)
class OcrWord:
    text: str
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width


@dataclass(frozen=True)
class OcrRow:
    text: str
    top: float
    height: float
    left: float
    width: float = 0.0
    words: tuple[OcrWord, ...] = ()

    @property
    def bottom(self) -> float:
        return self.top + self.height

    @property
    def right(self) -> float:
        return self.left + self.width


CHAT_TEXT_WHITE = 235  # brillo mínimo para considerar un píxel como letra plena


def prepare_chat_image(image: Image.Image) -> Image.Image:
    """Deja el chat como letras negras sobre fondo blanco, sin importar el fondo del juego.

    El texto del chat es claro (blanco o de color, en los nombres) con borde oscuro, sobre un panel semitransparente
    que deja ver la escena. Se toma el canal más brillante de cada píxel (un nombre rojo o azul queda tan claro como
    el texto blanco), se estira el contraste desde el brillo típico del fondo y se invierte.
    """
    value = np.asarray(image.convert("RGB")).max(axis=2).astype(np.float32)
    background = float(np.percentile(value, 50))
    low = min(background + 15, CHAT_TEXT_WHITE - 40)
    value = np.clip((value - low) * (255.0 / (CHAT_TEXT_WHITE - low)), 0, 255)
    return Image.fromarray((255 - value).astype(np.uint8))


def binarize_local_background(image: Image.Image, min_distance: float = 60, ratio: float = 0.75) -> Image.Image:
    """Variante de preparación para cuando el fondo oscuro del chat se desvaneció y el texto queda directamente sobre
    el juego (cielo, pasto, paredes que se mueven con la cámara).

    Estima el color del fondo alrededor de cada punto (mediana de la zona: las letras son finas y no la alteran) y
    marca como letra lo que difiere mucho de ese fondo sin ser mucho más oscuro que él (el borde semitransparente de
    las letras sí es más oscuro, por lo que queda excluido). El resultado es blanco y negro puro.
    """
    rgb = image.convert("RGB")
    small = rgb.reduce(3)
    background_small = np.stack(
        [_ndimage().median_filter(np.asarray(small)[..., channel], size=7) for channel in range(3)], axis=2)
    background = np.asarray(Image.fromarray(background_small.astype(np.uint8)).resize(rgb.size, Image.Resampling.BILINEAR),
                            dtype=np.float32)
    pixels = np.asarray(rgb, dtype=np.float32)
    distance = np.sqrt(((pixels - background) ** 2).sum(axis=2))
    ink = (distance > min_distance) & (pixels.sum(axis=2) >= ratio * background.sum(axis=2))
    return Image.fromarray(np.where(ink, 0, 255).astype(np.uint8))


def looks_faded(image: Image.Image) -> bool:
    """Indica si el fondo oscuro del chat se desvaneció (Roblox lo oculta tras unos segundos sin actividad)."""
    return float(np.median(np.asarray(image.convert("RGB")).max(axis=2))) > 120


WHITE_TEXT = 230  # el texto de los mensajes de Roblox es blanco: todos los canales altos


def white_text_bands(image: Image.Image, min_pixels: int = 2) -> list[tuple[int, int]]:
    """Franjas verticales (arriba, abajo) de la imagen con letras blancas, es decir, donde hay mensajes; no usa OCR (~1
    ms).
    """
    white = np.asarray(image.convert("RGB")).min(axis=2) >= WHITE_TEXT
    busy = np.concatenate(([False], white.sum(axis=1) >= min_pixels, [False]))
    edges = np.flatnonzero(busy[1:] != busy[:-1])
    bands: list[list[int]] = []
    for start, end in zip(edges[::2].tolist(), edges[1::2].tolist()):
        if bands and start - bands[-1][1] <= 2:
            bands[-1][1] = end  # huecos de 1-2 filas dentro de una misma línea (entre letras)
        else:
            bands.append([start, end])
    return [(start, end) for start, end in bands if end - start >= 5]


def _same_line(a: OcrRow, b: OcrRow) -> bool:
    """Indica si dos filas pertenecen a la misma línea visual: deben superponerse verticalmente al menos la mitad de la
    más baja.

    (Comparar centros con la altura máxima unía líneas vecinas cuando un ícono, como las banderas del chat,
    agrandaba una de ellas.)
    """
    overlap = min(a.bottom, b.bottom) - max(a.top, b.top)
    return overlap >= 0.5 * min(a.height, b.height)


GAP_LETTERS = 3.0  # hueco mínimo (en alturas de letra) para separar dos textos a la misma altura


def _gap_limit(rows: list[OcrRow]) -> float:
    heights = sorted(w.height for r in rows for w in r.words) or sorted(r.height for r in rows)
    return max(24.0, GAP_LETTERS * heights[len(heights) // 2])


def _from_words(words: list[OcrWord]) -> OcrRow:
    top, left = min(w.top for w in words), min(w.left for w in words)
    return OcrRow(" ".join(w.text for w in words), top, max(w.top + w.height for w in words) - top, left,
                  max(w.right for w in words) - left, tuple(words))


def split_far_words(row: OcrRow) -> list[OcrRow]:
    """Separa una línea con un hueco muy grande en el medio: el OCR une el mensaje del chat con cualquier texto a la
    misma altura más a la derecha (burbuja de otro jugador, nombre sobre una cabeza, cartel del juego).
    """
    words = sorted(row.words, key=lambda w: w.left)
    if len(words) < 2:
        return [row]
    limit = _gap_limit([row])
    parts = [[words[0]]]
    for word in words[1:]:
        if word.left - max(w.right for w in parts[-1]) > limit:
            parts.append([word])
        else:
            parts[-1].append(word)
    return [row] if len(parts) == 1 else [_from_words(part) for part in parts]


def merge_rows(rows: list[OcrRow]) -> list[OcrRow]:
    """Une fragmentos de la misma línea (Windows separa el nombre coloreado del mensaje), pero solo si están cerca: el
    texto del juego a la misma altura y lejos a la derecha no es parte del mensaje.
    """
    merged: list[list[OcrRow]] = []
    rows = [part for row in rows for part in split_far_words(row)]
    for row in sorted(rows, key=lambda r: r.top + r.height / 2):
        if merged and any(_same_line(row, other) for other in merged[-1]):
            merged[-1].append(row)
        else:
            merged.append([row])
    result = []
    for line in merged:
        line.sort(key=lambda r: r.left)
        limit = _gap_limit(line)
        groups = [[line[0]]]
        for row in line[1:]:
            # sin ancho no se puede calcular el alcance
            reach = max(r.right if r.width else float("inf") for r in groups[-1])
            if row.left - reach > limit:
                groups.append([row])
            else:
                groups[-1].append(row)
        for group in groups:
            top = min(r.top for r in group)
            left = group[0].left
            result.append(OcrRow(
                text=" ".join(r.text for r in group),
                top=top,
                height=max(r.bottom for r in group) - top,
                left=left,
                width=max(r.right for r in group) - left,
                words=tuple(w for r in group for w in r.words),
            ))
    return sorted(result, key=lambda r: (r.top, r.left))


def _use_system_cpp_runtime() -> None:
    """Carga la librería de C++ de Windows (msvcp140.dll) antes que winrt.

    El paquete winrt incluye su propia copia, antigua (14.29). Si se carga primero, todo el proceso la usa y otras
    librerías compiladas con una versión más nueva fallan sin aviso (el reconocimiento de voz con Whisper cerraba
    Bubble al abrirse). La del sistema es más nueva y sirve para ambas.
    """
    import ctypes
    import os

    try:
        ctypes.WinDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "msvcp140.dll"))
    except OSError:
        pass  # sin la del sistema se usa la de winrt (el OCR funciona igual)


class WindowsOcr:
    def __init__(self, language_tag: str = "") -> None:
        _use_system_cpp_runtime()
        from winrt.windows.globalization import Language
        from winrt.windows.media.ocr import OcrEngine

        if language_tag:
            self._engine = OcrEngine.try_create_from_language(Language(language_tag))
        else:
            self._engine = OcrEngine.try_create_from_user_profile_languages()
        if self._engine is None:
            # Si el idioma de Windows no tiene lector de texto, se usa otro instalado: el chat de Roblox en letras
            # latinas se lee igual con cualquiera.
            available = [language.language_tag for language in OcrEngine.available_recognizer_languages]
            preferred = sorted(available, key=lambda tag: (not tag.startswith("en"), not tag.startswith("es"), tag))
            for tag in preferred:
                self._engine = OcrEngine.try_create_from_language(Language(tag))
                if self._engine is not None:
                    break
        if self._engine is None:
            raise RuntimeError("Windows no tiene ningún idioma para leer texto: agregá uno en Configuración › Hora e "
                               "idioma › Idioma (con «Reconocimiento óptico de caracteres»).")
        self._max_dim = OcrEngine.max_image_dimension
        # Un motor de OCR no admite dos lecturas simultáneas ("Another RecognizeAsync operation is already running"),
        # por lo que se hacen de a una. Para leer en paralelo, usar otra instancia.
        self._lock = asyncio.Lock()

    @property
    def language(self) -> str:
        return self._engine.recognizer_language.language_tag

    async def recognize(self, image: Image.Image, upscale: bool = True) -> list[OcrRow]:
        from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.storage.streams import DataWriter

        # El chat de Roblox tiene letra chica y agrandarla mejora mucho la lectura. En pantalla completa (burbujas) no
        # hace falta y sería más lento.
        scale = 2 if upscale and max(image.size) * 2 <= self._max_dim else 1
        if scale > 1:
            image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        r, g, b = image.convert("RGB").split()
        bgra = Image.merge("RGBA", (b, g, r, Image.new("L", image.size, 255))).tobytes()
        writer = DataWriter()
        writer.write_bytes(bgra)
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, image.width, image.height
        )
        async with self._lock:
            result = await self._engine.recognize_async(bitmap)
        rows = []
        for line in result.lines:
            words = tuple(
                OcrWord(w.text, w.bounding_rect.x / scale, w.bounding_rect.y / scale,
                        w.bounding_rect.width / scale, w.bounding_rect.height / scale)
                for w in line.words
            )
            if not words:
                continue
            top = min(w.top for w in words)
            bottom = max(w.top + w.height for w in words)
            left = min(w.left for w in words)
            right = max(w.right for w in words)
            rows.append(OcrRow(line.text, top, bottom - top, left, right - left, words))
        return merge_rows(rows)
