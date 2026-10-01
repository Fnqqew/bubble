"""Subtítulos de voz: cada frase con su hablante, el original mientras se dice y la traducción en cuanto llega.

Recibe las frases de `LiveListener` a medida que se completan. Mientras la persona habla, lo dicho hasta el momento se
traduce en vivo cada pocas palabras (una traducción provisoria, que la siguiente reemplaza). Cuando la frase es
definitiva se pide la traducción final; si la última traducción en vivo ya abarcaba la frase completa, queda como final
sin esperar. Lo que ya está en el idioma del jugador no se subtitula.
"""

from __future__ import annotations

import difflib
import itertools
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
# Traducción en vivo: desde cuántas palabras, cada cuántas palabras nuevas y con qué separación mínima.
LIVE_MIN_WORDS = 2
LIVE_NEW_WORDS = 2
LIVE_EVERY_S = 0.35
# Pedidos en vivo a la vez para todo el tablero. Con el carril propio de la traducción en vivo son dos (el subtítulo
# se actualiza el doble de seguido); sin él, uno, para no demorar la voz del jugador, que usa el mismo carril.
LIVE_PARALLEL = 2
LIVE_GIVE_UP_S = 8.0  # un pedido en vivo sin respuesta en este tiempo se da por perdido


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
    asked: str = ""  # texto enviado a traducir (traducción final)
    generation: int = 0  # cada pedido nuevo invalida los fragmentos de los anteriores
    live: bool = False  # la traducción mostrada es provisoria: la persona seguía hablando
    live_shown: str = ""  # texto original al que corresponde la traducción en vivo que se muestra
    live_asked: str = ""  # último texto enviado a traducir en vivo
    live_at: float = 0.0  # cuándo se pidió la última traducción en vivo
    live_shown_seq: int = 0  # número del pedido en vivo que se muestra (uno anterior que llegue tarde no lo pisa)
    wait_live: bool = False  # el texto definitivo es el de una traducción en vivo en curso: se espera esa
    wait_seq: int = 0  # número de ese pedido
    wait_intonation: str = ""  # entonación del texto definitivo, mientras se espera


# translate(texto, idioma, voz, al_llegar_un_fragmento, al_terminar(traducción o None si falló, native=ya estaba en el
# idioma del jugador), entonación: "question", "shout"…, live=True: traducción en vivo de una frase incompleta)
Translate = Callable[..., None]


class CaptionBoard:
    def __init__(self, my_language: str, translate: Translate, on_change: Callable[[], None] = lambda: None,
                 keep: int = KEEP, show_s: float = SHOW_S,
                 on_translated: Callable[[Line], None] = lambda _line: None,
                 is_native: Callable[[str, str], bool] | None = None, live: bool = True,
                 parallel: int = LIVE_PARALLEL) -> None:
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
        self.live = live  # traducir mientras la persona habla (más pedidos a Claude, sin esperar la pausa)
        self.parallel = parallel
        self._flights: dict[int, tuple[Line, str, float]] = {}  # pedidos en vivo en curso: frase, texto, cuándo
        self._seq = itertools.count(1)

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
            ask = reused = False
            # Texto definitivo, o una pausa en la que la frase suena terminada (se adelanta, sin esperar al definitivo).
            definitive = (caption.final and not line.final) or (caption.stable and not line.asked)
            if definitive and line.asked and same_words(line.asked, caption.text):
                definitive = False  # ya se pidió (o se resolvió) casi lo mismo durante la pausa
            if definitive:
                if self._covers(line, caption.text, caption.intonation):
                    # La última traducción en vivo ya abarca la frase: queda como definitiva, sin otro pedido.
                    reused = True
                    line.asked, line.live, line.done, line.wait_live = caption.text, False, True, False
                    line.generation += 1  # (una traducción en vivo todavía en curso ya no corresponde)
                elif (flight := self._flight_for(line, caption.text)) is not None:
                    # Se está traduciendo en vivo este mismo texto: se espera esa traducción en vez de pedir otra.
                    line.asked, line.wait_live, line.wait_seq = caption.text, True, flight
                    line.wait_intonation = caption.intonation
                else:
                    ask = True
            elif self.live and line.asked and not caption.final and \
                    len(caption.text.split()) - len(line.asked.split()) >= LIVE_NEW_WORDS:
                # Parecía terminada, pero la persona siguió hablando (en Pro, una oración con punto en medio de lo
                # que dice): se descarta ese pedido y se vuelve a traducir en vivo.
                line.asked, line.wait_live, line.generation = "", False, line.generation + 1
            line.final = caption.final
            finished = Line(**vars(line)) if reused else None
            stream = True
            if ask:
                line.asked, line.generation, line.wait_live = caption.text, line.generation + 1, False
                # Con una traducción en vivo a la vista, se mantiene hasta que llega la final completa (sin vaciar el
                # subtítulo ni reescribirlo palabra por palabra).
                stream = not line.translation
                if stream:
                    line.translation = ""
                line.done = False
            generation = line.generation
            live = None if ask or finished else self._live_job(line, now)
        self._changed()
        if finished:
            self.on_translated(finished)
        if ask:
            self.translate(caption.text, language, line.speaker,
                           lambda piece: self._piece(line, piece, generation, stream),
                           lambda text, native=False: self._done(line, text, generation, native),
                           caption.intonation)
        if live:
            self._ask_live(*live)

    # ------------------------------------------------------------ traducción en vivo
    @staticmethod
    def _covers(line: Line, text: str, intonation: str) -> bool:
        """Indica si la traducción en vivo a la vista corresponde a este texto y respeta su entonación."""
        return bool(line.live and line.translation and line.live_shown and same_words(line.live_shown, text)
                    and fits_intonation(line.translation, intonation))

    def _flight_for(self, line: Line, text: str) -> int | None:
        """Número del último pedido en vivo en curso de esta frase con este mismo texto."""
        matching = [seq for seq, (other, asked, _since) in self._flights.items()
                    if other is line and same_words(asked, text)]
        return max(matching) if matching else None

    def _live_job(self, line: Line, now: float) -> tuple | None:
        """Con el lock tomado: si corresponde traducir en vivo esta frase, reserva el pedido y lo devuelve."""
        for seq, (_line, _text, since) in list(self._flights.items()):
            if now - since > LIVE_GIVE_UP_S:
                del self._flights[seq]  # un pedido que nunca respondió no bloquea los siguientes
        if not self.live or len(self._flights) >= max(1, self.parallel) or line.asked or line.final or \
                line.speaker in (MINE, NOTICE):
            return None
        words = len(line.original.split())
        new = words - len(line.live_asked.split())
        needed = LIVE_NEW_WORDS if line.live_asked else LIVE_MIN_WORDS
        if words < LIVE_MIN_WORDS or new < needed or now - line.live_at < LIVE_EVERY_S:
            return None
        seq = next(self._seq)
        self._flights[seq] = (line, line.original, now)
        line.live_asked, line.live_at = line.original, now
        return line, line.original, line.generation, seq

    def _ask_live(self, line: Line, text: str, generation: int, seq: int) -> None:
        try:
            self.translate(text, line.language, line.speaker, lambda _piece: None,
                           lambda result, native=False: self._live_done(line, text, generation, seq, result, native),
                           "", live=True)
        except Exception:  # noqa: BLE001 - sin traducción en vivo queda el original; la final llega igual
            with self._lock:
                self._flights.pop(seq, None)

    def _live_done(self, line: Line, text: str, generation: int, seq: int, result: str | None,
                   native: bool = False) -> None:
        now = time.monotonic()
        changed, finished, follow = False, None, None
        with self._lock:
            self._flights.pop(seq, None)
            current = generation == line.generation and line in self.lines
            usable = bool(result) and not native
            if current and line.wait_live and seq == line.wait_seq:
                # El texto definitivo era el de esta traducción en vivo.
                line.wait_live = False
                if usable and same_words(text, line.asked) and fits_intonation(result, line.wait_intonation):
                    line.translation, line.live_shown, line.live, line.done = result, text, False, True
                    line.live_shown_seq, line.updated = seq, now
                    changed, finished = True, Line(**vars(line))
                else:
                    # No sirve como definitiva (falló o no respeta la entonación): se pide la final.
                    line.generation += 1
                    keep = bool(line.translation)
                    if not keep:
                        line.translation = ""
                    line.done = False
                    follow = (line.asked, line.language, line.speaker, line.generation, not keep,
                              line.wait_intonation)
            elif current and usable and (not line.asked or line.wait_live) and seq > line.live_shown_seq:
                line.translation, line.live, line.live_shown, line.done = result, True, text, False
                line.live_shown_seq, line.updated = seq, now
                changed = True
            # Mientras tanto la persona siguió hablando (en esta frase o en otra): se traduce lo nuevo.
            jobs = []
            for other in sorted(self.lines, key=lambda item: item.updated, reverse=True):
                job = self._live_job(other, now)
                if job:
                    jobs.append(job)
        if changed:
            self._changed()
        if finished:
            self.on_translated(finished)
        if follow:
            asked, language, speaker, final_generation, stream, intonation = follow
            self.translate(asked, language, speaker,
                           lambda piece: self._piece(line, piece, final_generation, stream),
                           lambda result, native=False: self._done(line, result, final_generation, native),
                           intonation)
        for job in jobs:
            self._ask_live(*job)

    def _piece(self, line: Line, piece: str, generation: int, stream: bool = True) -> None:
        with self._lock:
            if generation != line.generation or not stream:
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
            line.live = False
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


def fits_intonation(translation: str, intonation: str) -> bool:
    """Una traducción hecha sin conocer la entonación sirve si ya la refleja: pregunta con «?», grito con «!»."""
    marks = set(intonation.split("+")) if intonation else set()
    if "question" in marks and "?" not in translation:
        return False
    if marks & {"shout", "exclaim"} and "!" not in translation:
        return False
    return True


def _normal(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", text.lower()).split())


def same_words(a: str, b: str) -> bool:
    """Indica si dicen lo mismo, sin distinguir mayúsculas ni puntuación y tolerando alguna palabra distinta."""
    a, b = _normal(a), _normal(b)
    return a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.88

