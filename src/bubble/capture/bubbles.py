"""Burbujas de chat sobre la cabeza de los jugadores: datos comunes y diagnóstico por renglón de OCR.

El seguimiento en vivo (rápido, también con la cámara en movimiento) está en bubble_tracker.py. Acá quedan
las comprobaciones por renglón de texto, que sirven para el diagnóstico de "Probar captura".
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageStat

from ..geometry import Rect
from .ocr import OcrRow


@dataclass
class BubbleItem:
    text: str
    left: int
    top: int
    right: int
    bottom: int
    background: tuple[int, int, int]
    foreground: tuple[int, int, int]
    rows: int = 1
    vx: float = 0.0  # velocidad en pantalla (px/s)
    vy: float = 0.0
    captured_at: float = 0.0  # time.monotonic() de la captura en que se vio
    # Parte visible (izquierda, arriba, derecha, abajo) cuando la burbuja está tapada en parte (detrás del chat o
    # contra el borde de la pantalla); left/top/right/bottom es la burbuja entera. None: se ve entera.
    clip: tuple[int, int, int, int] | None = None


def _pixels(image: Image.Image) -> list:
    getter = getattr(image, "get_flattened_data", None) or image.getdata  # getdata está deprecado
    return list(getter())


def _luma(color) -> float:
    r, g, b = color[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def check_bubble_row(image: Image.Image, row: OcrRow) -> tuple[tuple[tuple, tuple] | None, str]:
    """((fondo, letra), "") si el renglón está dentro de una burbuja; (None, motivo) si no.

    Se mira solo a los COSTADOS del texto (no arriba/abajo, donde en burbujas de varias líneas está el
    renglón vecino): ahí tiene que haber fondo claro, parejo y sin color.
    """
    if row.height < 6:
        return None, "letra demasiado chica"
    margin = int(max(3, min(8, row.height * 0.45)))
    top, bottom = int(row.top), int(row.bottom)
    left, right = int(row.left) - margin, int(row.right) + margin
    if left < 0 or right + 2 >= image.width or top < 0 or bottom >= image.height:
        return None, "pegado al borde de la pantalla"
    sides = [image.crop((left, top, left + 2, bottom)), image.crop((right, top, right + 2, bottom))]
    pixels = [p for part in sides for p in _pixels(part.convert("RGB"))]
    if not pixels:
        return None, "sin fondo para comparar"
    lumas = [_luma(p) for p in pixels]
    mean = sum(lumas) / len(lumas)
    spread = (sum((v - mean) ** 2 for v in lumas) / len(lumas)) ** 0.5
    saturation = sum(max(p[:3]) - min(p[:3]) for p in pixels) / len(pixels)
    if mean < 180:
        return None, f"fondo oscuro (brillo {mean:.0f})"
    if spread > 32:
        return None, f"fondo desparejo (variación {spread:.0f})"
    if saturation > 45:
        return None, f"fondo con color (saturación {saturation:.0f})"
    inside = image.crop((int(row.left), top, int(row.right), bottom)).convert("RGB")
    if ImageStat.Stat(inside.convert("L")).extrema[0][0] > 130:
        return None, "sin letra oscura"
    darkest = min(_pixels(inside), key=_luma)
    background = tuple(int(sum(p[i] for p in pixels) / len(pixels)) for i in range(3))
    return (background, tuple(darkest)), ""


def bubble_colors(image: Image.Image, row: OcrRow) -> tuple[tuple, tuple] | None:
    return check_bubble_row(image, row)[0]


def _same_bubble(upper: OcrRow, lower: OcrRow) -> bool:
    gap = lower.top - upper.bottom
    overlap = min(upper.right, lower.right) - max(upper.left, lower.left)
    return -2 <= gap <= 1.2 * max(upper.height, lower.height) and overlap >= 0.3 * min(upper.width, lower.width)


def group_bubbles(rows: list[tuple[OcrRow, tuple, tuple]]) -> list[BubbleItem]:
    """Une renglones que forman la misma burbuja (uno debajo del otro, superpuestos en horizontal)."""
    groups: list[list[tuple[OcrRow, tuple, tuple]]] = []
    for entry in sorted(rows, key=lambda e: e[0].top):
        for group in groups:
            if _same_bubble(group[-1][0], entry[0]):
                group.append(entry)
                break
        else:
            groups.append([entry])
    items = []
    for group in groups:
        group_rows = [g[0] for g in group]
        items.append(BubbleItem(
            text=" ".join(r.text for r in group_rows).strip(),
            left=int(min(r.left for r in group_rows)), top=int(min(r.top for r in group_rows)),
            right=int(max(r.right for r in group_rows)), bottom=int(max(r.bottom for r in group_rows)),
            background=group[0][1], foreground=group[0][2], rows=len(group_rows),
        ))
    return [item for item in items if any(c.isalpha() for c in item.text)]


def find_bubbles(image: Image.Image, rows: list[OcrRow], exclude: Rect | None = None) -> list[BubbleItem]:
    """Burbujas en la captura, sin contar lo que cae dentro de `exclude` (el chat)."""
    return group_bubbles([(row, *colors) for row, colors, _ in _classify(image, rows, exclude) if colors])


def explain_bubbles(image: Image.Image, rows: list[OcrRow], exclude: Rect | None = None) -> list[str]:
    """Diagnóstico: por qué cada texto de la pantalla se tomó (o no) como burbuja."""
    lines = []
    for row, colors, reason in _classify(image, rows, exclude):
        status = "BURBUJA" if colors else f"descartado: {reason}"
        lines.append(f"({int(row.left)},{int(row.top)}) {row.text!r} -> {status}")
    return lines


def _classify(image: Image.Image, rows: list[OcrRow], exclude: Rect | None):
    for row in rows:
        if exclude and exclude.left <= row.left <= exclude.right and exclude.top <= row.top <= exclude.bottom:
            yield row, None, "está dentro del chat"
            continue
        colors, reason = check_bubble_row(image, row)
        yield row, colors, reason
