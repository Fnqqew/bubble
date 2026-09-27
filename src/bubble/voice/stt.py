"""Filtros para lo que transcribe Whisper (ver asr.py, que es el que transcribe)."""

from __future__ import annotations

import re

# Frases que Whisper "inventa" con ruido, música o silencio (aprendidas de videos subtitulados).
_HALLUCINATIONS = re.compile(
    r"^(thank you( so much)?( for watching)?|thanks for watching|subscribe|please subscribe|"
    r"gracias por ver( el video)?|subt[ií]tulos (realizados )?por la comunidad de amara\.org|"
    r"obrigad[oa] por assistir|legendas pela comunidade amara\.org|sous-titres r[ée]alis[ée]s par.*|"
    r"you|bye|\.+|♪+|music)\W*$",
    re.IGNORECASE,
)


def is_hallucination(text: str) -> bool:
    return bool(_HALLUCINATIONS.match(text.strip()))
