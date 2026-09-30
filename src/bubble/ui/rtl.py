"""Soporte de hebreo, árabe, persa y urdu, que se escriben de derecha a izquierda.

Las traducciones y los subtítulos del juego se dibujan con Pillow. Sin su motor de texto completo (libraqm, que no viene
en Windows), Pillow coloca las letras en el orden en que están almacenadas: el hebreo aparecía invertido y el árabe,
además, con las letras sin unir. Este módulo invierte el texto al orden de lectura y une las letras árabes sin
dependencias adicionales: la forma de cada letra (aislada, inicial, media o final) se obtiene de la tabla de Unicode.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

_RTL_RANGES = ((0x0590, 0x05FF), (0x0600, 0x06FF), (0x0750, 0x077F), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))
_ARABIC = (0x0600, 0x06FF)
_TASHKEEL = range(0x064B, 0x0660)  # marcas de vocal: van sobre la letra y no interrumpen la unión


def is_rtl(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _RTL_RANGES)


def has_rtl(text: str) -> bool:
    return any(is_rtl(char) for char in text)


@lru_cache(maxsize=1)
def _forms() -> dict[str, dict[str, str]]:
    """Mapea cada letra a sus formas {"isolated", "final", "initial", "medial"} según la tabla de Unicode."""
    table: dict[str, dict[str, str]] = {}
    for code in list(range(0xFB50, 0xFDFF)) + list(range(0xFE70, 0xFEFF)):
        parts = unicodedata.decomposition(chr(code)).split()
        if len(parts) == 2 and parts[0] in ("<isolated>", "<final>", "<initial>", "<medial>"):
            table.setdefault(chr(int(parts[1], 16)), {})[parts[0][1:-1]] = chr(code)
    return table


def _joins_next(char: str) -> bool:
    """Indica si la letra se une con la siguiente. Las que solo tienen forma aislada y final (como ا د ر و) no se unen.
    """
    forms = _forms().get(char)
    return bool(forms) and "initial" in forms


def shape(text: str) -> str:
    """Devuelve las letras árabes con la forma que corresponde según sus vecinas, es decir, unidas."""
    table = _forms()
    letters = [char for char in text]
    out = []
    for index, char in enumerate(letters):
        forms = table.get(char)
        if not forms:
            out.append(char)
            continue
        before = next((c for c in reversed(letters[:index]) if ord(c) not in _TASHKEEL), "")
        after = next((c for c in letters[index + 1:] if ord(c) not in _TASHKEEL), "")
        joined_before = bool(before) and before in table and _joins_next(before)
        joined_after = bool(after) and after in table and "initial" in forms
        if joined_before and joined_after and "medial" in forms:
            out.append(forms["medial"])
        elif joined_before and "final" in forms:
            out.append(forms["final"])
        elif joined_after and "initial" in forms:
            out.append(forms["initial"])
        else:
            out.append(forms.get("isolated", char))
    return "".join(out)


def visual(text: str) -> str:
    """Devuelve el texto en orden visual, para dibujarlo con Pillow. Si no hay letras de derecha a izquierda, lo
    devuelve igual.
    """
    if not has_rtl(text):
        return text
    text = shape(text) if any(_ARABIC[0] <= ord(c) <= _ARABIC[1] for c in text) else text
    # Tramos de derecha a izquierda (letras hebreas o árabes y lo que queda entre ellas) o de izquierda a derecha
    # (números y palabras en letras latinas). La línea se lee de derecha a izquierda: se invierte el orden de los tramos
    # y también el de las letras dentro de cada tramo de derecha a izquierda; los números y el texto latino conservan su
    # orden.
    runs: list[tuple[bool, str]] = []
    for char in text:
        rtl = is_rtl(char) if (char.isalnum() or is_rtl(char)) else None
        if rtl is None:  # espacios y signos: se asignan al tramo en el que están
            rtl = runs[-1][0] if runs else True
        if runs and runs[-1][0] == rtl:
            runs[-1] = (rtl, runs[-1][1] + char)
        else:
            runs.append((rtl, char))
    return "".join(chunk[::-1] if rtl else chunk for rtl, chunk in reversed(runs))
