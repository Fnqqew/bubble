"""Encuentra el chat en la ventana de Roblox, sin que el jugador lo calibre.

Cada juego puede poner el chat en otro lugar (arriba a la izquierda es lo normal, pero hay juegos que lo mueven
abajo o lo agrandan). Se lee la ventana entera una vez con OCR y se buscan líneas con forma de mensaje
("Nombre: texto", "[Team] Nombre: texto", avisos "[SYSTEM]: …") alineadas en una misma columna: eso es el chat.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..geometry import Rect
from .chat_parser import _LETTER, _STRONG
from .ocr import OcrRow

MIN_LINES = 2
VISIBLE_LINES = 10  # el chat de Roblox muestra ~8-10 líneas: la zona se estira hacia arriba hasta eso
MIN_WIDTH_SHARE = 0.4  # ancho mínimo (parte de la ventana): un mensaje largo llega hasta el borde del panel


@dataclass
class ChatGuess:
    region: Rect  # relativa a la imagen de la ventana
    lines: int  # cuántas líneas con forma de mensaje la sostienen


def _looks_like_message(row: OcrRow) -> bool:
    text = row.text.strip()
    match = _STRONG.match(text)
    return bool(match) and len(_LETTER.findall(match.group("text"))) >= 2 and row.height >= 6


def find_chat_region(rows: list[OcrRow], width: int, height: int) -> ChatGuess | None:
    """Zona del chat a partir de las líneas que leyó el OCR en toda la ventana (`width` x `height`)."""
    messages = sorted((row for row in rows if _looks_like_message(row)), key=lambda row: row.left)
    # Columnas: líneas que empiezan casi en el mismo lugar (la banderita corre el texto unos 30 px).
    columns: list[list[OcrRow]] = []
    for row in messages:
        if columns and row.left - columns[-1][0].left <= 45:
            columns[-1].append(row)
        else:
            columns.append([row])
    best: list[OcrRow] | None = None
    for column in columns:
        column.sort(key=lambda row: row.top)
        # Las líneas del chat van una debajo de la otra: se toma el tramo seguido más largo (sin huecos enormes).
        heights = sorted(row.height for row in column)
        line = heights[len(heights) // 2]
        run: list[OcrRow] = []
        for row in column:
            if run and row.top - run[-1].bottom > 4 * line:
                if best is None or len(run) > len(best):
                    best = run
                run = []
            run.append(row)
        if best is None or len(run) > len(best):
            best = run
    if not best or len(best) < MIN_LINES:
        return None
    line = sorted(row.height for row in best)[len(best) // 2]
    pitch = max(line * 1.2, (best[-1].top - best[0].top) / max(1, len(best) - 1)) if len(best) > 1 else line * 1.4
    # A la izquierda, lugar para la banderita/etiqueta (el OCR a veces no la lee); abajo, una línea más: ahí aparecen
    # los mensajes nuevos (y con el fondo desvanecido la última línea puede no haberse leído).
    left = max(0, int(min(row.left for row in best)) - 34)
    right = min(width, int(max(max(row.right for row in best) + 12, left + MIN_WIDTH_SHARE * width)))
    bottom = min(height, int(max(row.bottom for row in best) + pitch * 1.4))
    top = max(0, int(min(min(row.top for row in best) - line * 0.4, bottom - pitch * VISIBLE_LINES)))
    return ChatGuess(Rect(left, top, right - left, bottom - top), len(best))
