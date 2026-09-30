"""Hebreo, árabe, persa y urdu en el juego: se escriben de derecha a izquierda.

Las traducciones y los subtítulos del juego se dibujan con Pillow, que sin su motor de texto completo (libraqm, que no
viene en Windows) pone las letras en el orden en que se guardan: el hebreo salía al revés y el árabe, además, con cada
letra suelta. Acá se da vuelta el texto como se lee y se unen las letras árabes, sin paquetes aparte: las formas de
cada letra (sola, al principio, en el medio, al final) salen de la tabla de Unicode.
"""

from __future__ import annotations

import unicodedata
from functools import lru_cache

_RTL_RANGES = ((0x0590, 0x05FF), (0x0600, 0x06FF), (0x0750, 0x077F), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))
_ARABIC = (0x0600, 0x06FF)
_TASHKEEL = range(0x064B, 0x0660)  # marcas de vocal: van sobre la letra, no cortan la unión


def is_rtl(char: str) -> bool:
    code = ord(char)
    return any(low <= code <= high for low, high in _RTL_RANGES)


def has_rtl(text: str) -> bool:
    return any(is_rtl(char) for char in text)


@lru_cache(maxsize=1)
def _forms() -> dict[str, dict[str, str]]:
    """letra → {"isolated", "final", "initial", "medial"} → la forma que se dibuja (de la tabla de Unicode)."""
    table: dict[str, dict[str, str]] = {}
    for code in list(range(0xFB50, 0xFDFF)) + list(range(0xFE70, 0xFEFF)):
        parts = unicodedata.decomposition(chr(code)).split()
        if len(parts) == 2 and parts[0] in ("<isolated>", "<final>", "<initial>", "<medial>"):
            table.setdefault(chr(int(parts[1], 16)), {})[parts[0][1:-1]] = chr(code)
    return table


def _joins_next(char: str) -> bool:
    """¿Esta letra se une con la que sigue? (las que solo tienen forma sola y final, como ا د ر و, no)."""
    forms = _forms().get(char)
    return bool(forms) and "initial" in forms


def shape(text: str) -> str:
    """Las letras árabes con la forma que les toca según sus vecinas (unidas, como se escriben)."""
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
    """El texto en el orden en que se ve (para dibujarlo con Pillow). Sin letras de derecha a izquierda, igual."""
    if not has_rtl(text):
        return text
    text = shape(text) if any(_ARABIC[0] <= ord(c) <= _ARABIC[1] for c in text) else text
    # Tramos: de derecha a izquierda (letras hebreas/árabes y lo que queda entre ellas) o de izquierda a derecha
    # (números, palabras en letras latinas). La línea se lee de derecha a izquierda: los tramos van al revés y las
    # letras de cada tramo de derecha a izquierda también; los números y el latín quedan como se leen.
    runs: list[tuple[bool, str]] = []
    for char in text:
        rtl = is_rtl(char) if (char.isalnum() or is_rtl(char)) else None
        if rtl is None:  # espacios y signos: con el tramo en el que están
            rtl = runs[-1][0] if runs else True
        if runs and runs[-1][0] == rtl:
            runs[-1] = (rtl, runs[-1][1] + char)
        else:
            runs.append((rtl, char))
    return "".join(chunk[::-1] if rtl else chunk for rtl, chunk in reversed(runs))
