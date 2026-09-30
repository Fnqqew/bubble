"""Subtítulos de voz: cada frase con su hablante, el original mientras se dice y la traducción en cuanto llega.

Recibe las frases de `LiveListener` a medida que se completan y pide la traducción cuando la frase es definitiva. La
traducción llega en fragmentos y se muestra palabra por palabra. Lo que ya está en el idioma del jugador no se
subtitula.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .live import Caption

KEEP = 3  # frases visibles
MINE = -1  # "voz" de las frases dichas por el jugador
NOTICE = -2  # avisos de Bubble en el juego (ej. "Roblox está usando otro micrófono")
SHOW_S = 7.0  # tiempo visible tras la última novedad (más si es larga: ver CaptionBoard.show_for)
READ_CHARS_PER_S = 14  # caracteres legibles por segundo


@dataclass
class Line:
    id: int
    speaker: int
    language: str
    original: str
    translation: str = ""
    final: bool = False  # el original es el definitivo
    done: bool = False  # la traducción terminó
    updated: float = 0.0
    heard_at: float = 0.0
    asked: str = ""  # texto enviado a traducir
    generation: int = 0  # cada pedido nuevo invalida los fragmentos de los anteriores


# translate(texto, idioma, voz, al_llegar_un_fragmento, al_terminar(traducción o None si falló, native=ya estaba en el
# idioma del jugador), entonación: "question", "shout"…)
Translate = Callable[[str, str, int, Callable[[str], None], Callable[[str | None], None], str], None]


class CaptionBoard:
    def __init__(self, my_language: str, translate: Translate, on_change: Callable[[], None] = lambda: None,
                 keep: int = KEEP, show_s: float = SHOW_S,
                 on_translated: Callable[[Line], None] = lambda _line: None,
                 is_native: Callable[[str, str], bool] | None = None) -> None:
        self.my_language = my_language.split("-")[0].lower()
        # ¿Ya está en el idioma del jugador? Se evalúa por las palabras, no solo por el idioma que detectó el
        # reconocimiento. Se revisa antes de mostrar la frase para que no aparezcan frases en español que luego habría
        # que retirar.
        self.is_native = is_native
        self.translate = translate
        self.on_change = on_change
        self.on_translated = on_translated  # frase traducida (para el registro de la ventana)
        self._own_ids = -1
        self.keep = keep
        self.show_s = show_s
        self.lines: list[Line] = []
        self._lock = threading.Lock()

    def caption(self, caption: Caption) -> None:
        """Novedad de una frase (puede llamarse desde cualquier hilo)."""
        now = time.monotonic()
        language = caption.language.split("-")[0].lower()
        with self._lock:
            line = next((item for item in self.lines if item.id == caption.id), None)
            native = language == self.my_language or (
                self.is_native is not None and bool(caption.text) and self.is_native(caption.text, language))
            if not caption.text or native:
                # Nada que mostrar (no era voz) o ya está en el idioma del jugador.
                if line:
                    self.lines.remove(line)
                    self._changed()
                return
            if line is None:
                line = Line(caption.id, caption.speaker, language, caption.text, heard_at=now)
                self.lines.append(line)
                self.lines = self.lines[-self.keep:]
            line.speaker = caption.speaker or line.speaker
            line.language = language
            line.original = caption.text
            line.updated = now
            ask = False
            if caption.final and not line.final:
                # Texto definitivo: si ya se está traduciendo casi lo mismo (pedido antes, durante la pausa), se
                # reutiliza.
                ask = not (line.asked and same_words(line.asked, caption.text))
            elif caption.stable and not line.asked:
                ask = True  # se adelanta: la frase suena terminada, no hace falta esperar al texto definitivo
            line.final = caption.final
            if ask:
                line.asked, line.generation = caption.text, line.generation + 1
                line.translation, line.done = "", False
            generation = line.generation
        self._changed()
        if ask:
            self.translate(caption.text, language, line.speaker,
                           lambda piece: self._piece(line, piece, generation),
                           lambda text, native=False: self._done(line, text, generation, native),
                           caption.intonation)

    def _piece(self, line: Line, piece: str, generation: int) -> None:
        with self._lock:
            if generation != line.generation:
                return
            line.translation += piece
            line.updated = time.monotonic()
        self._changed()

    def _done(self, line: Line, text: str | None, generation: int, native: bool = False) -> None:
        with self._lock:
            if generation != line.generation:
                return
            if native:
                # Claude confirmó que ya estaba en el idioma del jugador (Whisper lo había detectado como otro): no se
                # subtitula.
                if line in self.lines:
                    self.lines.remove(line)
                changed = True
            else:
                changed = False
        if changed:
            self._changed()
            return
        with self._lock:
            if generation != line.generation:
                return
            if text:
                line.translation = text
            line.done = True
            line.updated = time.monotonic()
            finished = Line(**vars(line)) if text else None
        self._changed()
        if finished:
            self.on_translated(finished)

    def mine(self, original: str, translation: str, language: str) -> None:
        """Voz del jugador traducida (lo que escuchan los demás), para que pueda verificar el resultado."""
        with self._lock:
            self._own_ids -= 1
            now = time.monotonic()
            self.lines.append(Line(self._own_ids, MINE, language, original, translation, True, True, now, now))
            self.lines = self.lines[-self.keep:]
        self._changed()

    def notice(self, text: str, seconds: float = 14.0) -> None:
        from ..i18n import t

        text = t(text)
        """Un aviso de Bubble en el juego, como un subtítulo."""
        with self._lock:
            self._own_ids -= 1
            now = time.monotonic()
            later = now + seconds - self.show_s  # se muestra más tiempo que un subtítulo común
            self.lines.append(Line(self._own_ids, NOTICE, "", "", text, True, True, later, now))
            self.lines = self.lines[-self.keep:]
        self._changed()

    def _changed(self) -> None:
        self.on_change()

    def show_for(self, line: Line) -> float:
        """Tiempo que permanece visible: el valor habitual, o el que requiere leerla si es larga."""
        return max(self.show_s, len(line.translation or line.original) / READ_CHARS_PER_S + 2.0)

    def visible(self, now: float | None = None) -> list[Line]:
        now = time.monotonic() if now is None else now
        with self._lock:
            self.lines = [line for line in self.lines if now - line.updated < self.show_for(line)]
            return [Line(**vars(line)) for line in self.lines]


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def same_words(a: str, b: str) -> bool:
    """Indica si dicen lo mismo, sin distinguir mayúsculas ni puntuación y tolerando alguna palabra distinta."""
    a, b = _normal(a), _normal(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.88

