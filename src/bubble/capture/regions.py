"""Manchas de una máscara y filtro de mediana, con numpy y Pillow.

Reemplazan a `scipy.ndimage` (`label`, `find_objects` y `median_filter`), que eran lo único que Bubble usaba de scipy:
el paquete ocupaba 108 MB en cada PC. Dan exactamente el mismo resultado (ver tests/test_regiones.py).
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def _runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Tramos horizontales seguidos de la máscara, en orden de lectura: (fila, inicio, fin sin incluir)."""
    height, width = mask.shape
    padded = np.zeros((height, width + 2), dtype=bool)
    padded[:, 1:-1] = mask
    # Cada fila alterna inicio y fin de tramo: con una sola búsqueda salen los dos.
    edges = np.flatnonzero(padded[:, 1:] != padded[:, :-1])
    rows, columns = np.divmod(edges, width + 1)
    return rows[0::2], columns[0::2], columns[1::2]


def _touching(rows: np.ndarray, starts: np.ndarray, ends: np.ndarray, width: int,
              diagonal: bool) -> tuple[np.ndarray, np.ndarray]:
    """Pares de tramos de filas vecinas que se tocan (de costado, o también en diagonal)."""
    reach = 1 if diagonal else 0
    stride = width + 4  # cada fila en su propio rango de claves, sin pisarse con la de al lado
    start_keys = rows * stride + starts
    end_keys = rows * stride + ends
    above = (rows - 1) * stride
    # Un tramo de arriba [a, b) toca a [c, d) si b + reach > c y a < d + reach.
    low = np.searchsorted(end_keys, above + starts - reach, side="right")
    high = np.searchsorted(start_keys, above + ends + reach, side="left")
    counts = np.maximum(high - low, 0)
    lower = np.repeat(np.arange(len(rows)), counts)
    offsets = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    upper = np.repeat(low, counts) + offsets
    return upper, lower


def _groups(count: int, first: np.ndarray, second: np.ndarray) -> tuple[np.ndarray, int]:
    """Número de mancha de cada tramo (1, 2, 3… según su primer tramo en orden de lectura) y cantidad de manchas. Une
    los pares hasta que no cambia nada; la raíz de cada mancha queda en su tramo de menor número.
    """
    parent = np.arange(count)
    while len(first):
        a, b = parent[first], parent[second]
        if np.array_equal(a, b):
            break
        low = np.minimum(a, b)
        np.minimum.at(parent, a, low)
        np.minimum.at(parent, b, low)
        while True:  # cada tramo apunta directo a la raíz de su mancha
            jumped = parent[parent]
            if np.array_equal(jumped, parent):
                break
            parent = jumped
    numbers = np.cumsum(parent == np.arange(count), dtype=np.int32)
    return numbers[parent], int(numbers[-1])


def _label(mask: np.ndarray, diagonal: bool):
    mask = np.asarray(mask, dtype=bool)
    labels = np.zeros(mask.shape, dtype=np.int32)
    rows, starts, ends = _runs(mask)
    if not len(rows):
        return labels, rows, starts, ends, rows, 0
    upper, lower = _touching(rows, starts, ends, mask.shape[1], diagonal)
    number, count = _groups(len(rows), upper, lower)
    lengths = ends - starts
    within = np.arange(lengths.sum()) - np.repeat(np.cumsum(lengths) - lengths, lengths)
    labels.flat[np.repeat(rows * mask.shape[1] + starts, lengths) + within] = np.repeat(number, lengths)
    return labels, rows, starts, ends, number, count


def label(mask: np.ndarray, diagonal: bool = False) -> tuple[np.ndarray, int]:
    """Como `scipy.ndimage.label`: numera las manchas 1, 2, 3… en orden de lectura. `diagonal`: los píxeles que se
    tocan en diagonal son de la misma mancha (como pasarle `structure=np.ones((3, 3))`).
    """
    labels, _rows, _starts, _ends, _number, count = _label(mask, diagonal)
    return labels, count


def regions(mask: np.ndarray, diagonal: bool = False) -> tuple[np.ndarray, list[tuple[slice, slice]]]:
    """Las manchas numeradas (como `label`) y el rectángulo de cada una (como `scipy.ndimage.find_objects`)."""
    labels, rows, starts, ends, number, count = _label(mask, diagonal)
    if not count:
        return labels, []
    index = number - 1
    top, left = np.full(count, labels.shape[0]), np.full(count, labels.shape[1])
    bottom, right = np.zeros(count, dtype=np.intp), np.zeros(count, dtype=np.intp)
    np.minimum.at(top, index, rows)
    np.minimum.at(left, index, starts)
    np.maximum.at(bottom, index, rows + 1)
    np.maximum.at(right, index, ends)
    return labels, [(slice(t, b), slice(l, r))
                    for t, b, l, r in zip(top.tolist(), bottom.tolist(), left.tolist(), right.tolist())]


def median_filter(channel: np.ndarray, size: int) -> np.ndarray:
    """Como `scipy.ndimage.median_filter` sobre un canal de 8 bits (bordes reflejados), hecho por Pillow en C."""
    pad = size // 2
    padded = np.pad(np.asarray(channel, dtype=np.uint8), pad, mode="symmetric")
    filtered = np.asarray(Image.fromarray(padded).filter(ImageFilter.MedianFilter(size)))
    return filtered[pad:-pad, pad:-pad] if pad else filtered
