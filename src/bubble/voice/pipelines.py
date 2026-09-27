"""Tu voz traducida, para que te escuchen los demás (lo que te dicen a vos está en live.py).

Tres formas de usarla:
- VoiceSpeaker: tocás un botón y hablás (termina solo cuando dejás de hablar), o lo mantenés apretado mientras
  hablás → Whisper → Claude → voz sintética.
- DirectVoice: traducción directa, sin tecla: cada frase que decís se traduce y se dice sola.
- Escribiendo (la barra, Ctrl+Enter): el texto traducido se dice con la voz sintética.

Las tres terminan en VoiceOut: la voz sintética sale por el micrófono virtual (lo que Roblox escucha como tu
micrófono) y, si querés, también por tus auriculares, para que sepas qué dijo. Si estás muteado en Roblox, te desmutea
mientras suena y te vuelve a mutear (ver roblox_mic.py).
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

import numpy as np

from . import audio as audio_io
from .asr import FastWhisper
from .tts import Voices

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
BLOCK = 480  # 30 ms a 16 kHz

Translate = Callable[[str], tuple[str, str] | None]  # texto en tu idioma -> (traducción, idioma) o None


class VoiceOut:
    """Dice un texto con voz sintética: al micrófono virtual y (si querés) a tus auriculares."""

    def __init__(self, voices: Voices, hear_myself: bool = True, output: audio_io.Output | None = None,
                 bridge=None, mic_switch=None) -> None:
        self.voices = voices
        # Tu micrófono en Roblox (roblox_mic.RobloxMic): si estás muteado, se desmutea solo mientras suena la frase.
        self.mic_switch = mic_switch
        self.output = output or audio_io.voice_output()
        self.bridge = bridge  # tu micrófono pasando al virtual (ver bridge.py): baja mientras suena la traducida
        # Con el micrófono virtual, tu voz traducida va a Roblox y vos no la escucharías: también suena, más bajo,
        # en tus auriculares (sin micrófono virtual ya sale por ahí).
        self.hear_myself = hear_myself
        self.monitor_volume = 0.7
        self.listeners: list[Callable[[float], None]] = []  # avisos "va a sonar tanto tiempo" (para no escucharla)
        self._lock = threading.Lock()  # una frase a la vez

    def warm_up(self, language: str) -> None:
        """Carga ya la voz de ese idioma (la primera vez tarda ~3 s): así tu primera frase no espera."""
        threading.Thread(target=lambda: self.voices.synthesize("ok", language), name="bubble-voz-precarga",
                         daemon=True).start()

    def say(self, text: str, language: str) -> bool:
        """Bloquea hasta que termina de sonar. False si no hay voz para ese idioma."""
        speech = self.voices.synthesize(text, language)
        if speech is None:
            return False
        with self._lock:
            unmuted = self._unmute()
            try:
                seconds = len(speech.audio) / speech.sample_rate
                for listener in self.listeners:
                    listener(seconds)
                if self.bridge is not None and self.bridge.running:
                    self.bridge.duck(seconds + 0.25)
                if self.hear_myself and self.output.is_cable:
                    threading.Thread(target=self._monitor, args=(speech,), name="bubble-tu-voz-escucha",
                                     daemon=True).start()
                audio_io.play(self.output, speech.audio, speech.sample_rate)
            finally:
                if unmuted:
                    try:
                        self.mic_switch.mute_again()
                    except Exception:  # noqa: BLE001
                        log.debug("No se pudo volver a mutear en Roblox", exc_info=True)
        return True

    def _unmute(self) -> bool:
        """Solo con el micrófono virtual: sin él, Roblox escucharía tu micrófono real, no la voz traducida."""
        if self.mic_switch is None or not self.output.is_cable:
            return False
        try:
            return bool(self.mic_switch.unmute())
        except Exception:  # noqa: BLE001 - si no se puede, suena igual (quizás no estabas muteado)
            log.debug("No se pudo desmutear en Roblox", exc_info=True)
            return False

    def _monitor(self, speech) -> None:
        try:
            audio_io.play(audio_io.monitor_output(), speech.audio * self.monitor_volume, speech.sample_rate)
        except Exception:  # noqa: BLE001 - es solo para que la escuches
            log.debug("No se pudo reproducir tu voz traducida en tus parlantes", exc_info=True)


class VoiceSpeaker:
    """Con un botón, de dos formas:
    - lo tocás (y lo soltás enseguida): te escucha y termina solo cuando dejás de hablar (o cuando lo volvés a tocar);
    - lo mantenés apretado mientras hablás: termina al soltarlo.
    Después se traduce y se dice (y si estabas muteado en Roblox, te desmutea solo mientras suena)."""

    MAX_SECONDS = 20.0
    TAP_S = 0.35  # soltarlo antes de esto es "tocarlo"
    END_SILENCE_S = 0.8  # tocándolo: este silencio después de hablar es que terminaste
    NO_SPEECH_S = 6.0  # tocándolo: si no hablás en este tiempo, se cancela

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
    ) -> None:
        self.transcriber = transcriber
        self.out = out
        self.translate = translate
        self.vk = push_to_talk_vk
        self.my_language = my_language
        self.on_event = on_event  # ("grabando" | "escuchando" | "entendi" | "traduccion" | "error", texto)
        self.mic_factory = mic_factory
        self.vad_factory = vad_factory
        if held is not None:
            self._held = held
        self._running = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self._thread = threading.Thread(target=self._loop, name="bubble-tu-voz", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()

    def _held(self) -> bool:
        return bool(ctypes.windll.user32.GetAsyncKeyState(self.vk) & 0x8000)

    def _loop(self) -> None:
        was_down = True  # si ya estaba apretada al empezar, se espera a que la suelte
        while self.running:
            down = self._held()
            if down and not was_down:
                recorded = self._record()
                if recorded is not None and len(recorded) > SAMPLE_RATE * 0.4:
                    threading.Thread(target=self.speak, args=(recorded,), name="bubble-tu-voz-trad", daemon=True).start()
                down = self._held()  # un segundo toque para terminar no vuelve a empezar: se espera a que lo sueltes
            was_down = down
            time.sleep(0.015)

    def _new_vad(self):
        if self.vad_factory is not None:
            return self.vad_factory()
        from .vad import StreamingVad

        return StreamingVad()

    def _record(self) -> np.ndarray | None:
        """Graba mientras lo mantenés apretado, o, si lo tocaste, hasta que dejás de hablar."""
        self.on_event("grabando", "")
        blocks: list[np.ndarray] = []
        try:
            audio_io.com_ready()
            with self.mic_factory().recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK * 4) as recorder:
                started = time.monotonic()
                released_at: float | None = None  # cuándo lo soltaste, si fue un toque
                vad, spoke, quiet_since = None, False, None
                while self.running and time.monotonic() - started < self.MAX_SECONDS:
                    block = audio_io.to_mono(recorder.record(numframes=BLOCK))
                    blocks.append(block)
                    now, held = time.monotonic(), self._held()
                    if released_at is None:
                        if held:
                            continue
                        if now - started >= self.TAP_S:
                            break  # lo mantuviste apretado mientras hablabas: al soltarlo, listo
                        released_at = now  # fue un toque: se escucha hasta que termines de hablar
                        self.on_event("escuchando", "")
                        vad = self._new_vad()
                        block = np.concatenate(blocks)
                    if held and now - released_at > 0.3:
                        break  # otro toque: terminaste
                    probs = vad.feed(block)
                    if len(probs) and float(np.max(probs)) >= 0.5:
                        spoke, quiet_since = True, None
                    elif len(probs) and spoke:
                        quiet_since = quiet_since or now
                        if now - quiet_since >= self.END_SILENCE_S:
                            break
                    if not spoke and now - released_at >= self.NO_SPEECH_S:
                        self.on_event("error", "No te escuché: tocá el botón y hablá")
                        return None
        except Exception as exc:  # noqa: BLE001
            self.on_event("error", f"No se pudo usar el micrófono: {exc}")
            return None
        return np.concatenate(blocks) if blocks else None

    def speak(self, recorded: np.ndarray) -> None:
        """Tu voz grabada → texto → traducción → voz sintética → salida. Separado para poder probarlo sin micrófono."""
        try:
            heard = self.transcriber.transcribe(recorded, language=self.my_language)
            if heard is None:
                self.on_event("error", "No se entendió lo que dijiste")
                return
            self.on_event("entendi", heard.text)
            translated = self.translate(heard.text)
            if translated is None:
                self.on_event("error", "No se pudo traducir")
                return
            text, language = translated
            self.on_event("traduccion", text)
            if not self.out.say(text, language):
                self.on_event("error", f"No hay voz para el idioma «{language}»")
        except Exception as exc:  # noqa: BLE001
            log.exception("Falló la traducción de tu voz")
            self.on_event("error", str(exc))


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
    ) -> None:
        from .live import LiveListener, Settings

        self.out = out
        self.translate = translate
        self.on_event = on_event
        self.target = target  # idioma al que se traduce (para precargar su voz)
        mine = my_language.split("-")[0].lower()
        # Tu idioma ya se sabe: no se detecta. Tu texto no hace falta verlo mientras hablás: se lee solo en las
        # pausas, con el modelo preciso, y esa lectura ya es la final. Así la traducción se pide en la pausa y
        # nunca hay que repetirla.
        settings = Settings(fast_final_languages=(mine,), first_partial_s=60.0, partial_every_s=60.0)
        self.listener = LiveListener(final_asr, self._caption, partial_asr=final_asr, source_factory=mic_factory,
                                     on_error=lambda msg: on_event("error", msg), settings=settings,
                                     language=mine)
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="bubble-directo-trad")
        self._asked: dict[int, tuple[str, Future]] = {}
        self._queue: queue.Queue = queue.Queue()
        self._running = threading.Event()
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

    def _mute(self, seconds: float) -> None:
        if not self.out.output.is_cable or self.out.hear_myself:
            self.listener.muted_until = time.monotonic() + seconds + 0.3

    def _caption(self, caption) -> None:
        from .captions import same_words

        if caption.final:
            if not caption.text:
                return
            asked = self._asked.pop(caption.id, None)
            if asked is None or not same_words(asked[0], caption.text):
                asked = (caption.text, self._pool.submit(self.translate, caption.text))
            self.on_event("entendi", caption.text)
            self._queue.put(asked)
        elif caption.stable and caption.id not in self._asked:
            self._asked[caption.id] = (caption.text, self._pool.submit(self.translate, caption.text))

    def _speak_in_order(self) -> None:
        while self.running:
            item = self._queue.get()
            if item is None:
                break
            _text, future = item
            try:
                translated = future.result(timeout=25)
                if translated is None:
                    self.on_event("error", "No se pudo traducir")
                    continue
                text, language = translated
                self.on_event("traduccion", text)
                if not self.out.say(text, language):
                    self.on_event("error", f"No hay voz para el idioma «{language}»")
            except Exception as exc:  # noqa: BLE001
                log.exception("Falló la traducción directa")
                self.on_event("error", str(exc))
