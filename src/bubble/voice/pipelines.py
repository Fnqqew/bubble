"""Tu voz traducida, para que te escuchen los demás (lo que te dicen a vos está en live.py).

Tres formas de usarla:
- VoiceSpeaker: tocás un botón y hablás (termina solo cuando dejás de hablar), o lo mantenés apretado mientras
  hablás → Whisper → Claude → voz sintética.
- DirectVoice: traducción directa, sin tecla: cada frase que decís se traduce y se dice sola.
- Escribiendo (la barra, Ctrl+Enter): el texto traducido se dice con la voz sintética.

Las tres terminan en VoiceOut: la voz sintética sale por el micrófono virtual (lo que Roblox escucha como tu
micrófono) y, si querés, también por tus auriculares, para que sepas qué dijo. En Roblox tenés que tener el micrófono
activado: si no, no te escucha nadie (la página Voz te avisa).
"""

from __future__ import annotations

import ctypes
import itertools
import logging
import queue
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import audio as audio_io
from .asr import FastWhisper, Heard
from .speech import melody, sounds_unfinished
from .tts import Voices

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
BLOCK = 480  # 30 ms a 16 kHz

# (texto en tu idioma, entonación: "question" | "exclaim" | "") -> (traducción, idioma) o None
Translate = Callable[[str, str], tuple[str, str] | None]


@dataclass
class Turn:
    """Una frase tuya de punta a punta: qué se entendió, cómo se tradujo y cuánto tardó cada paso."""

    heard: str = ""
    translation: str = ""
    language: str = ""
    intonation: str = ""
    sure: bool = False  # Whisper la entendió con seguridad
    times: dict[str, float] = field(default_factory=dict)  # "entender", "traducir", "voz" (segundos)
    error: str = ""


class VoiceOut:
    """Dice un texto con voz sintética: al micrófono virtual y (si querés) a tus auriculares."""

    def __init__(self, voices: Voices, hear_myself: bool = True, output: audio_io.Output | None = None,
                 bridge=None) -> None:
        self.voices = voices
        self.output = output or audio_io.voice_output()
        self.bridge = bridge  # tu micrófono pasando al virtual (ver bridge.py): baja mientras suena la traducida
        # Con el micrófono virtual, tu voz traducida va a Roblox y vos no la escucharías: también suena, más bajo,
        # en tus auriculares (sin micrófono virtual ya sale por ahí).
        self.hear_myself = hear_myself
        self.monitor_volume = 0.7
        self.listeners: list[Callable[[float], None]] = []  # avisos "va a sonar tanto tiempo" (para no escucharla)
        self._lock = threading.Lock()  # una frase a la vez
        self._warm_lock = threading.Lock()
        self._warm_next: str | None = None
        self._warming = False

    def warm_up(self, language: str) -> None:
        """Carga ya la voz de ese idioma (la primera vez tarda ~3 s): así tu primera frase no espera. De a una: si
        piden varias seguidas, se carga la que está en curso y después solo la última pedida (las del medio no)."""
        with self._warm_lock:
            self._warm_next = language
            if self._warming:
                return
            self._warming = True
        threading.Thread(target=self._warm_worker, name="bubble-voz-precarga", daemon=True).start()

    def _warm_worker(self) -> None:
        while True:
            with self._warm_lock:
                language, self._warm_next = self._warm_next, None
                if language is None:
                    self._warming = False
                    return
            try:
                is_loaded = getattr(self.voices, "is_loaded", None)
                if is_loaded is None or not is_loaded(language):
                    self.voices.synthesize("ok", language)
            except Exception:  # noqa: BLE001 - es solo para adelantar
                log.debug("No se pudo preparar la voz de %s", language, exc_info=True)

    def say(self, text: str, language: str, on_ready: Callable[[float], None] | None = None, style: str = "") -> bool:
        """Bloquea hasta que termina de sonar. False si no hay voz para ese idioma. `on_ready(segundos)`: la voz ya
        está lista y empieza a sonar (para medir cuánto tardó). `style`: cómo lo dijiste (gritando, bajito…)."""
        started = time.perf_counter()
        style = style or written_style(text)
        streaming = getattr(self.voices, "stream", None)
        if streaming is not None:
            opened = streaming(text, language, style=style)
            if opened is not None:
                return self._say_streaming(text, opened, started, on_ready)
        speech = self.voices.synthesize(text, language, style=style)
        if speech is None:
            return False
        if on_ready:
            on_ready(time.perf_counter() - started)
        with self._lock:
            seconds = len(speech.audio) / speech.sample_rate
            for listener in self.listeners:
                listener(seconds)
            if self.bridge is not None and self.bridge.running:
                self.bridge.duck(seconds + 0.25)
            if self.hear_myself and self.output.is_cable:
                threading.Thread(target=self._monitor, args=(speech,), name="bubble-tu-voz-escucha",
                                 daemon=True).start()
            audio_io.play(self.output, speech.audio, speech.sample_rate)
        return True

    def _say_streaming(self, text: str, opened, started: float, on_ready) -> bool:
        """Las voces de la nube: suenan apenas llega el primer pedazo. Los avisos ("va a sonar tanto") se van
        renovando con cada pedazo, porque el largo total no se sabe hasta el final."""
        rate, pieces = opened
        first = next(pieces, None)
        if first is None:
            return False
        if on_ready:
            on_ready(time.perf_counter() - started)
        monitor: queue.Queue | None = queue.Queue() if self.hear_myself and self.output.is_cable else None
        if monitor is not None:
            threading.Thread(target=self._monitor_stream, args=(monitor, rate), name="bubble-tu-voz-escucha",
                             daemon=True).start()

        def tee():
            for piece in itertools.chain([first], pieces):
                if monitor is not None:
                    monitor.put(piece)
                yield piece
            if monitor is not None:
                monitor.put(None)

        def ahead(seconds: float) -> None:
            for listener in self.listeners:
                listener(seconds + 0.3)
            if self.bridge is not None and self.bridge.running:
                self.bridge.duck(seconds + 0.25)

        with self._lock:
            ahead(max(len(first) / rate, len(text) / 16))
            try:
                audio_io.play_stream(self.output, tee(), rate, on_piece=ahead)
            finally:
                if monitor is not None:
                    monitor.put(None)
        return True

    def _monitor_stream(self, pieces: queue.Queue, rate: int) -> None:
        def take():
            while (piece := pieces.get()) is not None:
                yield piece * self.monitor_volume

        try:
            audio_io.play_stream(audio_io.monitor_output(), take(), rate)
        except Exception:  # noqa: BLE001 - es solo para que la escuches
            log.debug("No se pudo reproducir tu voz traducida en tus parlantes", exc_info=True)

    def _monitor(self, speech) -> None:
        try:
            audio_io.play(audio_io.monitor_output(), speech.audio * self.monitor_volume, speech.sample_rate)
        except Exception:  # noqa: BLE001 - es solo para que la escuches
            log.debug("No se pudo reproducir tu voz traducida en tus parlantes", exc_info=True)


_SENTENCE_BOUNDARY = re.compile(r"[.!?…]+['\"»”)]*\s")


def written_style(text: str) -> str:
    """Cómo suena lo escrito, por el signo del final: "question" ("¿venís?"), "exclaim" ("¡vamos!") o ""."""
    end = text.rstrip(" \"'»”)")
    if end.endswith(("?", "？")):
        return "question"
    return "exclaim" if end.endswith(("!", "！")) else ""


class Sentences:
    """Junta la traducción a medida que llega y avisa la primera oración completa, para empezar a decirla ya: en una
    frase larga, la primera oración suena mientras Claude sigue traduciendo el resto (antes se esperaba la traducción
    entera). El resto se dice junto, al final: cada pedido a la voz de la nube sale con un tono un poco distinto, y
    oración por oración parecía que cambiaba de persona. Las oraciones muy cortas se juntan con la siguiente (una voz
    que dice "Ok." y frena suena rara)."""

    MIN_CHARS = 14

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.text = ""
        self.done = 0  # hasta dónde ya se mandó a decir

    def add(self, chunk: str) -> None:
        self.text += chunk
        if self.done:
            return  # la primera ya se está diciendo: lo demás, junto al final
        rest = self.text
        cut = next((m.end() for m in _SENTENCE_BOUNDARY.finditer(rest) if len(rest[:m.end()].strip()) >= self.MIN_CHARS),
                   None)
        if cut is not None:
            self.emit(rest[:cut].strip())
            self.done += cut

    def finish(self, final: str) -> None:
        """La traducción terminó (`final`, ya revisada): se dice lo que falta."""
        said = self.text[:self.done].strip()
        if not said:
            rest = final.strip()
        elif final.startswith(said):
            rest = final[len(said):].strip()
        else:  # la versión final cambió lo ya dicho (una corrección): se sigue con lo que faltaba de lo que llegó
            rest = self.text[self.done:].strip()
        if rest:
            self.emit(rest)


class StreamedTurn:
    """Tu frase entendida en vivo mientras hablás (Bubble Pro, con el botón): el audio va a la nube a medida que lo
    decís y, cuando terminás, el texto ya está (~0,3 s). Antes se mandaba todo lo dicho en cada pausa para saber si
    habías terminado (~1 s cada vez, pagando lo mismo varias veces) y otra vez al final."""

    def __init__(self, listener, language: str) -> None:
        self.listener = listener
        self.language = language.split("-")[0].lower()
        self._changed = threading.Condition()
        self._finals: dict[int, object] = {}
        self._final_at = 0.0
        listener.on_caption = self._caption

    def start(self) -> None:
        self.listener.start(capture=False)

    def stop(self) -> None:
        self.listener.stop()

    def begin(self) -> None:
        with self._changed:
            self._finals.clear()
            self._final_at = 0.0

    def feed(self, block: np.ndarray) -> None:
        self.listener.feed(block)

    def _caption(self, caption) -> None:
        if not caption.final:
            return
        with self._changed:
            if caption.text:
                self._finals[caption.id] = caption
            else:
                self._finals.pop(caption.id, None)
            self._final_at = time.monotonic()
            self._changed.notify_all()

    def _text(self) -> str:
        return " ".join(c.text for c in sorted(self._finals.values(), key=lambda c: c.start)).strip()

    def _closed(self) -> bool:
        """¿La nube ya cerró todo lo que dijiste? (texto sin terminar pendiente no hay, y el último cierre es posterior
        a tu última voz)"""
        return not self.listener._pieces and self._final_at >= self.listener.voice_at

    def said_since(self, moment: float) -> str | None:
        """Lo que dijiste, si la nube ya cerró la frase después de `moment` (si no, None: todavía no se sabe)."""
        with self._changed:
            return self._text() if self._final_at >= moment and not self.listener._pieces else None

    def finish(self, seconds: float, timeout: float = 1.5) -> Heard | None:
        """Terminaste: se espera que la nube cierre lo que falte (casi siempre ya está) y se devuelve la frase."""
        started = time.monotonic()
        with self._changed:
            closed = self._closed()
        self.listener.finalize()
        if not closed:
            with self._changed:
                self._changed.wait_for(lambda: self._closed() or self._final_at >= started, timeout)
        with self._changed:
            text = self._text()
        if not text:
            return None
        return Heard(text, self.language, 1.0, 0.0, -0.1, seconds, time.monotonic() - started)


class VoiceSpeaker:
    """Con un botón, de dos formas:
    - lo tocás (y lo soltás enseguida): te escucha y termina solo cuando dejás de hablar (o cuando lo volvés a tocar);
    - lo mantenés apretado mientras hablás: termina al soltarlo.
    Después se traduce y se dice.

    Para que salga rápido y sin cortarte: apenas hacés una pausa se lee lo que dijiste (en segundo plano). Si suena
    terminado, se traduce ya, sin esperar más silencio ni volver a leer el audio; si quedó a medias ("y…", "porque…"),
    se espera un poco más a que sigas."""

    MAX_SECONDS = 20.0
    TAP_S = 0.35  # soltarlo antes de esto es "tocarlo"
    PEEK_AFTER_S = 0.2  # a los 200 ms de silencio se lee lo dicho hasta ahí
    END_SILENCE_S = 0.8  # silencio que termina la frase si no se pudo leer
    UNFINISHED_S = 1.5  # si lo último suena a frase sin terminar, se espera hasta acá
    NO_SPEECH_S = 6.0  # tocándolo: si no hablás en este tiempo, se cancela
    MIN_SPEECH_S = 0.2  # menos voz que esto (una tecla, un golpe) no es una frase

    def __init__(
        self,
        transcriber: FastWhisper,
        out: VoiceOut,
        translate: Translate,
        push_to_talk_vk: int,
        my_language: str,
        on_event: Callable[[str, str], None] = lambda _kind, _text: None,
        mic_factory: Callable = audio_io.microphone,
        vad_factory: Callable | None = None,
        held: Callable[[], bool] | None = None,
        profile=None,
        stream: StreamedTurn | None = None,
        by_sentence: bool = False,
    ) -> None:
        """`stream`: con Bubble Pro, tu voz se entiende en vivo mientras hablás (ver StreamedTurn). `by_sentence`:
        `translate` acepta `on_sentence` y la voz empieza con la primera oración traducida (ver _translate_and_say)."""
        self.transcriber = transcriber
        self.stream = stream
        self.by_sentence = by_sentence
        self.out = out
        self.translate = translate
        self.vk = push_to_talk_vk
        self.my_language = my_language
        self.on_event = on_event  # ("grabando" | "escuchando" | "entendi" | "traduccion" | "error", texto)
        self.on_turn: Callable[[Turn], None] | None = None  # cada frase terminada, con sus tiempos
        self.mic_factory = mic_factory
        self.vad_factory = vad_factory
        self.profile = profile  # lo que se aprendió de cómo hablás (voice/profile.py)
        if held is not None:
            self._held = held
        self._running = threading.Event()
        self._thread: threading.Thread | None = None
        self._peeker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bubble-tu-voz-lee")

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        if self.stream is not None:
            self.stream.start()  # la conexión queda abierta: cuando tocás el botón no hay que esperarla
        self._thread = threading.Thread(target=self._loop, name="bubble-tu-voz", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self.stream is not None:
            self.stream.stop()

    def _held(self) -> bool:
        return bool(ctypes.windll.user32.GetAsyncKeyState(self.vk) & 0x8000)

    def _loop(self) -> None:
        was_down = True  # si ya estaba apretada al empezar, se espera a que la suelte
        while self.running:
            down = self._held()
            if down and not was_down:
                recorded, early = self._record()
                if recorded is not None and len(recorded) > SAMPLE_RATE * 0.4:
                    threading.Thread(target=self.speak, args=(recorded, early), name="bubble-tu-voz-trad",
                                     daemon=True).start()
                down = self._held()  # un segundo toque para terminar no vuelve a empezar: se espera a que lo sueltes
            was_down = down
            time.sleep(0.015)

    def _new_vad(self):
        if self.vad_factory is not None:
            return self.vad_factory()
        from .vad import StreamingVad

        return StreamingVad()

    def _hint(self) -> str:
        return self.profile.hint(self.my_language) if self.profile is not None else ""

    def _transcribe(self, audio: np.ndarray) -> Heard | None:
        return self.transcriber.transcribe(audio, language=self.my_language, hint=self._hint(), retry_beam=3,
                                           clean=True)

    def _ended(self, quiet: float, peek: Future | None, quiet_since: float | None = None) -> bool:
        """¿Ya terminaste de hablar? Depende de cómo sonó lo último que dijiste."""
        if self.stream is not None and quiet_since is not None:
            said = self.stream.said_since(quiet_since)
            if said is None:
                return quiet >= self.UNFINISHED_S  # la nube todavía no cerró la frase (tarda ~0,3 s)
            if not said or sounds_unfinished(said, self.my_language):
                return quiet >= self.UNFINISHED_S
            return True  # suena terminado: ya
        if peek is None:
            return quiet >= self.END_SILENCE_S
        if not peek.done():
            return quiet >= self.UNFINISHED_S  # se espera la lectura (pero no para siempre)
        try:
            heard = peek.result()
        except Exception:  # noqa: BLE001
            heard = None
        if heard is None:
            return quiet >= self.END_SILENCE_S
        if sounds_unfinished(heard.text, self.my_language):
            return quiet >= self.UNFINISHED_S
        return True  # suena terminado: ya

    def _record(self) -> tuple[np.ndarray | None, Future | None]:
        """Graba mientras lo mantenés apretado, o, si lo tocaste, hasta que terminás de hablar. Devuelve el audio y, si
        ya se leyó en la última pausa (y no volviste a hablar), esa lectura."""
        self.on_event("grabando", "")
        blocks: list[np.ndarray] = []
        total = 0
        peek: Future | None = None
        if self.stream is not None:
            self.stream.begin()
        try:
            audio_io.com_ready()
            vad = self._new_vad()
            with self.mic_factory().recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK * 4) as recorder:
                started = time.monotonic()
                released_at: float | None = None  # cuándo lo soltaste, si fue un toque
                spoke, quiet_since, speech_end, voiced = False, None, 0, 0
                while self.running and time.monotonic() - started < self.MAX_SECONDS:
                    block = audio_io.to_mono(recorder.record(numframes=BLOCK))
                    blocks.append(block)
                    total += len(block)
                    if self.stream is not None:
                        self.stream.feed(block)  # a la nube, mientras hablás
                    now, held = time.monotonic(), self._held()
                    probs = vad.feed(block)
                    voiced += int(np.sum(np.asarray(probs) >= 0.5)) if len(probs) else 0
                    if len(probs) and float(np.max(probs)) >= 0.5:
                        spoke, quiet_since, peek = True, None, None  # volvió a hablar: lo leído ya no es el final
                    elif len(probs) and spoke and quiet_since is None:
                        quiet_since, speech_end = now, total
                    quiet = now - quiet_since if quiet_since is not None else 0.0
                    if (self.stream is None and quiet_since is not None and peek is None
                            and quiet >= self.PEEK_AFTER_S):
                        said = np.concatenate(blocks)[:speech_end + int(0.1 * SAMPLE_RATE)]
                        peek = self._peeker.submit(self._transcribe, said)
                    if released_at is None:
                        if held:
                            continue
                        if now - started >= self.TAP_S:
                            break  # lo mantuviste apretado mientras hablabas: al soltarlo, listo
                        released_at = now  # fue un toque: se escucha hasta que termines de hablar
                        self.on_event("escuchando", "")
                    if held and now - released_at > 0.3:
                        break  # otro toque: terminaste
                    if spoke and quiet_since is not None and self._ended(quiet, peek, quiet_since):
                        break
                    if not spoke and now - released_at >= self.NO_SPEECH_S:
                        self.on_event("error", "No te escuché: tocá el botón y hablá")
                        return None, None
        except Exception as exc:  # noqa: BLE001
            self.on_event("error", f"No se pudo usar el micrófono: {exc}")
            return None, None
        if not blocks:
            return None, None
        if voiced * 512 / SAMPLE_RATE < self.MIN_SPEECH_S:
            if self.stream is not None:
                self.stream.listener.finalize()
            return None, None  # fue un ruido (una tecla, un golpe), no una frase: no se traduce nada
        if self.stream is not None:
            # Lo entendido en vivo (casi siempre ya está); si la nube no responde, se lee de nuevo al traducir.
            return np.concatenate(blocks), self._peeker.submit(self.stream.finish, total / SAMPLE_RATE)
        return np.concatenate(blocks), (peek if quiet_since is not None else None)

    def speak(self, recorded: np.ndarray, early: Future | None = None) -> Turn:
        """Tu voz grabada → texto → traducción → voz sintética → salida. Separado para poder probarlo sin micrófono.
        `early`: la lectura ya hecha en la pausa (si no, se lee ahora)."""
        turn = Turn()
        try:
            started = time.perf_counter()
            heard = None
            if early is not None:
                try:
                    heard = early.result(timeout=10)
                except Exception:  # noqa: BLE001 - se lee de nuevo
                    heard = None
            if heard is None:
                heard = self._transcribe(recorded)
            turn.times["entender"] = time.perf_counter() - started
            if heard is None:
                turn.error = "No se entendió lo que dijiste"
                self.on_event("error", turn.error)
                return turn
            tune = melody(recorded)
            turn.heard, turn.sure = heard.text, heard.logprob > -0.35 and heard.no_speech < 0.3
            turn.intonation = self.profile.intonation(tune) if self.profile is not None else (
                tune.kind() if tune else "")
            if self.profile is not None:
                self.profile.learn_melody(tune)
            self.on_event("entendi", heard.text)
            started = time.perf_counter()

            def ready(seconds: float) -> None:
                turn.times["voz"] = seconds
                if self.on_turn:
                    self.on_turn(turn)

            if self.by_sentence:
                self._translate_and_say(heard.text, turn, ready)
                turn.times["traducir"] = time.perf_counter() - started
                return turn
            translated = self.translate(heard.text, turn.intonation)
            turn.times["traducir"] = time.perf_counter() - started
            if translated is None:
                turn.error = "No se pudo traducir"
                self.on_event("error", turn.error)
                return turn
            turn.translation, turn.language = translated
            self.on_event("traduccion", turn.translation)
            if not self.out.say(turn.translation, turn.language, on_ready=ready, style=turn.intonation):
                turn.error = f"No hay voz para el idioma «{turn.language}»"
                self.on_event("error", turn.error)
        except Exception as exc:  # noqa: BLE001
            log.exception("Falló la traducción de tu voz")
            turn.error = str(exc)
            self.on_event("error", turn.error)
        return turn

    def _translate_and_say(self, text: str, turn: Turn, ready: Callable[[float], None]) -> None:
        """Traduce y dice de a oraciones: la primera suena mientras se traduce el resto (`translate` avisa cada
        oración con `on_sentence(texto, idioma)`)."""
        pending: queue.Queue = queue.Queue()
        missing: list[str] = []

        def talk() -> None:
            first = True
            while (item := pending.get()) is not None:
                sentence, language = item
                if not self.out.say(sentence, language, on_ready=ready if first else None, style=turn.intonation):
                    missing.append(language)
                first = False

        voice = threading.Thread(target=talk, name="bubble-tu-voz-dice", daemon=True)
        voice.start()
        try:
            translated = self.translate(text, turn.intonation, on_sentence=lambda s, lang: pending.put((s, lang)))
        finally:
            pending.put(None)
        if translated is None:
            turn.error = "No se pudo traducir"
            self.on_event("error", turn.error)
        else:
            turn.translation, turn.language = translated
            self.on_event("traduccion", turn.translation)
        voice.join()
        if missing and not turn.error:
            turn.error = f"No hay voz para el idioma «{missing[0]}»"
            self.on_event("error", turn.error)


class DirectVoice:
    """Traducción directa: escucha tu micrófono todo el tiempo y cada frase que decís sale traducida en voz.

    Usa la misma escucha en vivo que los subtítulos (live.py): la traducción se pide apenas hacés una pausa y la
    frase suena terminada, y las frases se dicen en orden, sin pisarse.
    """

    def __init__(
        self,
        final_asr: FastWhisper,
        out: VoiceOut,
        translate: Translate,
        my_language: str,
        partial_asr: FastWhisper | None = None,
        on_event: Callable[[str, str], None] = lambda _kind, _text: None,
        mic_factory: Callable = audio_io.microphone,
        target: Callable[[], str] = lambda: "en",
        profile=None,
        listener=None,
    ) -> None:
        """`listener`: otra escucha en vivo con la misma cara (Bubble Pro: la de la nube, cloud/deepgram.py)."""
        from .live import LiveListener, Settings

        self.out = out
        self.translate = translate
        self.on_event = on_event
        self.target = target  # idioma al que se traduce (para precargar su voz)
        self.profile = profile
        mine = my_language.split("-")[0].lower()
        # Tu idioma ya se sabe: no se detecta (antes, a veces tu español salía como otro idioma). Tu texto no hace falta
        # verlo mientras hablás: se lee solo en las pausas, con el modelo preciso, y esa lectura ya es la final. Así la
        # traducción se pide en la pausa y nunca hay que repetirla. Las pausas son tuyas: una pausa corta no corta la
        # frase si lo último que dijiste queda a medias ("y…", "porque…"); si suena terminada, sale enseguida.
        settings = Settings(fast_final_languages=(mine,), first_partial_s=60.0, partial_every_s=60.0,
                            end_silence_s=0.7, quick_end_s=0.3, wait_for_tail=True, unfinished_end_s=1.5)
        if listener is not None:
            listener.on_caption = self._caption
            self.listener = listener
        else:
            self.listener = LiveListener(final_asr, self._caption, partial_asr=final_asr, source_factory=mic_factory,
                                         on_error=lambda msg: on_event("error", msg), settings=settings,
                                         language=mine, hint=profile.hint if profile is not None else "",
                                         clean=True, judge=profile.intonation if profile is not None else None)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bubble-directo-trad")
        self._asked: dict[int, tuple[str, Future]] = {}
        self._queue: queue.Queue = queue.Queue()
        self._running = threading.Event()
        self.listening = True  # False: fuera del juego no se escucha (ver set_listening)
        # Mientras suena tu voz traducida por tus auriculares, el micrófono no la tiene que escuchar como tuya.
        out.listeners.append(self._mute)

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self.out.warm_up(self.target())
        self.listener.start()
        threading.Thread(target=self._speak_in_order, name="bubble-directo-voz", daemon=True).start()

    def stop(self) -> None:
        self._running.clear()
        self.listener.stop()
        self._queue.put(None)
        if self._mute in self.out.listeners:
            self.out.listeners.remove(self._mute)

    def set_listening(self, on: bool) -> None:
        """Solo se escucha mientras jugás (Roblox al frente, o la barra para escribir abierta). Fuera del juego el
        micrófono no se traduce (ni se manda a la nube, con Bubble Pro)."""
        if on == self.listening:
            return
        self.listening = on
        self.listener.muted_until = 0.0 if on else float("inf")

    def _mute(self, seconds: float) -> None:
        if self.listening and (not self.out.output.is_cable or self.out.hear_myself):
            self.listener.muted_until = time.monotonic() + seconds + 0.3

    def _caption(self, caption) -> None:
        from .captions import same_words

        if caption.final:
            if not caption.text:
                return
            asked = self._asked.pop(caption.id, None)
            if asked is None or not same_words(asked[0], caption.text):
                asked = (caption.text, self._pool.submit(self.translate, caption.text, caption.intonation))
            self.on_event("entendi", caption.text)
            self._queue.put((*asked, caption.intonation))
        elif caption.stable and caption.id not in self._asked:
            self._asked[caption.id] = (caption.text,
                                       self._pool.submit(self.translate, caption.text, caption.intonation))

    def _speak_in_order(self) -> None:
        while self.running:
            item = self._queue.get()
            if item is None:
                break
            _text, future, intonation = item
            try:
                translated = future.result(timeout=25)
                if translated is None:
                    self.on_event("error", "No se pudo traducir")
                    continue
                text, language = translated
                self.on_event("traduccion", text)
                if not self.out.say(text, language, style=intonation):
                    self.on_event("error", f"No hay voz para el idioma «{language}»")
            except Exception as exc:  # noqa: BLE001
                log.exception("Falló la traducción directa")
                self.on_event("error", str(exc))
