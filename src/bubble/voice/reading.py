"""Cómo lee una voz prestada el texto de otro idioma: con qué fonética y, si hace falta, pasado a otras letras.

Se eligió midiendo cuánto entiende Whisper (large-v3-turbo) de tres frases dichas por cada combinación: el porcentaje
de letras mal (CER). De referencia, el hindi con su propia voz: 2-5 %.
"""

from __future__ import annotations

# Fonética de espeak con la que lee cada idioma cuando la voz es de otro (sin entrada: la de la voz). Leer con la
# propia ayuda cuando la voz conoce sus sonidos y empeora cuando no: el croata se entiende mejor con la eslovena
# (6 % contra 34 %) y el bielorruso con la rusa (8 % contra 31 %), pero el macedonio con la suya (18 % contra 47 %:
# con la búlgara, «ќ» se leía por su nombre), el lituano (22 % contra 32 % con la letona) y el azerí (14 % contra
# 18 %).
READS_AS = {"ta": "ta", "gu": "gu", "pa": "pa", "mk": "mk", "az": "az", "lt": "lt"}

_SERBIAN = dict(zip("АБВГДЂЕЖЗИЈКЛМНОПРСТЋУФХЦЧШабвгдђежзијклмнопрстћуфхцчш",
                    ["A", "B", "V", "G", "D", "Đ", "E", "Ž", "Z", "I", "J", "K", "L", "M", "N", "O", "P", "R", "S",
                     "T", "Ć", "U", "F", "H", "C", "Č", "Š", "a", "b", "v", "g", "d", "đ", "e", "ž", "z", "i", "j",
                     "k", "l", "m", "n", "o", "p", "r", "s", "t", "ć", "u", "f", "h", "c", "č", "š"]))
_SERBIAN.update({"Љ": "Lj", "Њ": "Nj", "Џ": "Dž", "љ": "lj", "њ": "nj", "џ": "dž"})


def serbian_latin(text: str) -> str:
    """El serbio en cirílico, en letras latinas (una por una, sin ambigüedad). La voz eslovena que lo lee no conoce
    el cirílico: lo deletreaba («Z, D, R, A…»).
    """
    return "".join(_SERBIAN.get(char, char) for char in text)


def prepare(text: str, language: str) -> str:
    """El texto como conviene dárselo a la voz de ese idioma."""
    family = language.split("-")[0].lower()
    if family == "sr":
        return serbian_latin(text)
    return text
