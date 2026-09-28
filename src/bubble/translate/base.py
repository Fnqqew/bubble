"""Tipos compartidos del motor de traducción."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Callable, Literal, Protocol

Direction = Literal["incoming", "outgoing"]
# translate = otro idioma; adapt = mismo idioma pero con jerga de otro país.
Mode = Literal["translate", "adapt"]
DeltaCallback = Callable[[str], None]

TONE_MIN, TONE_MAX = 1, 5
TONE_NAMES = {
    1: "Neutro / formal",
    2: "Amable",
    3: "Casual",
    4: "Gamer",
    5: "Jerga nativa",
}


def clamp_tone(value: int) -> int:
    return max(TONE_MIN, min(TONE_MAX, int(value)))


@dataclass(frozen=True)
class ChatLine:
    speaker: str
    text: str


@dataclass(frozen=True)
class TranslationRequest:
    text: str
    target_lang: str
    direction: Direction
    speaker: str = ""
    context: tuple[ChatLine, ...] = ()
    target_region: str = ""
    mode: Mode = "translate"
    # Jerga detectada localmente, como pista para Claude: ("kkkk", "pt-BR", "laughter").
    slang_hints: tuple[tuple[str, str, str], ...] = ()
    # Nivel de informalidad de lo que enviás: 1 = neutro/formal ... 5 = jerga nativa.
    tone: int = 3
    # Se va a decir en voz (voz sintética): palabras completas, sin abreviaturas de chat, en la escritura del idioma.
    spoken: bool = False
    # Viene de reconocer una voz (Whisper): puede tener palabras mal entendidas y le falta puntuación (¿? ¡!).
    from_speech: bool = False
    # Cómo lo dijo (la entonación): "question" (subió al final) o "exclaim" (con énfasis). Whisper no lo marca.
    intonation: str = ""
    # Cómo querés sonar: pares (lo que dijiste, cómo quedó bien) que aprobaste en la página Pruebas.
    examples: tuple[tuple[str, str], ...] = ()
    # Tus palabras y nombres (de tu perfil de voz): Whisper puede haberlos escuchado mal.
    vocabulary: tuple[str, ...] = ()


@dataclass(frozen=True)
class TranslationResult:
    original: str
    translation: str
    source_lang: str | None
    target_lang: str
    provider: str
    # "translated" | "adapted" | "same_language" | "universal" | "filtered" | "local" | "cache" | "error"
    status: str
    ttft_s: float | None = None
    total_s: float = 0.0
    error: str | None = None


class Provider(Protocol):
    name: str

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    def stream(self, request: TranslationRequest) -> AsyncIterator[str]:
        """Devuelve la traducción en fragmentos a medida que se genera."""
        ...
