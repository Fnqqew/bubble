"""Subtítulos de voz: cada frase con quién la dice, el original mientras habla y la traducción apenas llega.

Recibe las frases de `LiveListener` (que se van completando) y pide la traducción cuando la frase es definitiva. La
traducción llega de a pedazos y se muestra así, palabra por palabra. Lo que ya está en tu idioma no se subtitula.
"""

from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .live import Caption

KEEP = 3  # frases a la vista
MINE = -1  # "voz" de las frases que dijiste vos
NOTICE = -2  # avisos de Bubble en el juego (ej. "Roblox está usando otro micrófono")
SHOW_S = 7.0  # cuánto queda cada frase después de la última novedad


@dataclass
class Line:
    id: int
    speaker: int
    language: str
    original: str
    translation: str = ""
    final: bool = False  # el original ya es el definitivo
    done: bool = False  # la traducción terminó
    updated: float = 0.0
    heard_at: float = 0.0
    asked: str = ""  # el texto que se mandó a traducir
    generation: int = 0  # cada pedido de traducción nuevo invalida los pedazos de los anteriores


# translate(texto, idioma, voz, al_llegar_un_pedazo, al_terminar(traducción o None si falló, native=ya estaba en
# tu idioma), cómo lo dijo: "question", "shout"…)
Translate = Callable[[str, str, int, Callable[[str], None], Callable[[str | None], None], str], None]


class CaptionBoard:
    def __init__(self, my_language: str, translate: Translate, on_change: Callable[[], None] = lambda: None,
                 keep: int = KEEP, show_s: float = SHOW_S,
                 on_translated: Callable[[Line], None] = lambda _line: None) -> None:
        self.my_language = my_language.split("-")[0].lower()
        self.translate = translate
        self.on_change = on_change
        self.on_translated = on_translated  # una frase quedó traducida (para el registro de la ventana)
        self._own_ids = -1
        self.keep = keep
        self.show_s = show_s
        self.lines: list[Line] = []
        self._lock = threading.Lock()

    def caption(self, caption: Caption) -> None:
        """Novedad de una frase (se puede llamar desde cualquier hilo)."""
        now = time.monotonic()
        language = caption.language.split("-")[0].lower()
        with self._lock:
            line = next((item for item in self.lines if item.id == caption.id), None)
            if not caption.text or language == self.my_language:
                # Nada que mostrar (no era voz) o ya está en tu idioma.
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
                # Texto definitivo: si ya se está traduciendo casi lo mismo (se pidió antes, en la pausa), sirve.
                ask = not (line.asked and same_words(line.asked, caption.text))
            elif caption.stable and not line.asked:
                ask = True  # se adelanta: la frase suena terminada, no hace falta esperar el texto definitivo
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
                # Claude confirmó que ya estaba en tu idioma (Whisper lo había detectado como otro): no se subtitula.
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
        """Tu voz traducida (lo que escuchan los demás), para que veas qué salió."""
        with self._lock:
            self._own_ids -= 1
            now = time.monotonic()
            self.lines.append(Line(self._own_ids, MINE, language, original, translation, True, True, now, now))
            self.lines = self.lines[-self.keep:]
        self._changed()

    def notice(self, text: str, seconds: float = 14.0) -> None:
        """Un aviso de Bubble en el juego, como un subtítulo."""
        with self._lock:
            self._own_ids -= 1
            now = time.monotonic()
            later = now + seconds - self.show_s  # se ve más que un subtítulo común
            self.lines.append(Line(self._own_ids, NOTICE, "", "", text, True, True, later, now))
            self.lines = self.lines[-self.keep:]
        self._changed()

    def _changed(self) -> None:
        self.on_change()

    def visible(self, now: float | None = None) -> list[Line]:
        now = time.monotonic() if now is None else now
        with self._lock:
            self.lines = [line for line in self.lines if now - line.updated < self.show_s]
            return [Line(**vars(line)) for line in self.lines]


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def same_words(a: str, b: str) -> bool:
    """¿Dicen lo mismo? (sin fijarse en mayúsculas ni puntuación, y tolerando alguna palabra distinta)."""
    a, b = _normal(a), _normal(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.88

