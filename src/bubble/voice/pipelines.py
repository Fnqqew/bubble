"""Las dos direcciones de la voz, cada una en sus propios hilos (el audio no espera a nadie).

VoiceListener: lo que suena (el chat de voz del juego) → frases → Whisper → `on_phrase(transcripción)`.
VoiceSpeaker: mientras mantenés apretada la tecla, graba tu micrófono → Whisper → `translate` (Claude) → voz
sintética → micrófono virtual (o tus parlantes, si no está instalado: para probar).
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from typing import Callable

import numpy as np

from . import audio as audio_io
from .segmenter import SAMPLE_RATE, SpeechSegmenter
from .stt import Transcriber, Transcript
from .tts import Voices

log = logging.getLogger(__name__)
BLOCK = 480  # 30 ms a 16 kHz


class VoiceListener:
    def __init__(self, transcriber: Transcriber, on_phrase: Callable[[Transcript], None],
                 source_factory: Callable = audio_io.speaker_loopback,
                 on_error: Callable[[str], None] = lambda _msg: None) -> None:
        self.transcriber = transcriber
        self.on_phrase = on_phrase
        self.source_factory = source_factory
        self.on_error = on_error
        self.muted_until = 0.0  # mientras suena tu propia voz traducida por los parlantes, no se escucha
        self._running = threading.Event()
        self._phrases: queue.Queue = queue.Queue(maxsize=8)
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self._threads = [threading.Thread(target=self._capture, name="bubble-voz-captura", daemon=True),
                         threading.Thread(target=self._transcribe, name="bubble-voz-texto", daemon=True)]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._running.clear()

    def feed(self, samples: np.ndarray, segmenter: SpeechSegmenter) -> None:
        """Audio (mono, 16 kHz) → frases a la cola. Separado de la captura para poder probarlo sin dispositivos."""
        if time.monotonic() < self.muted_until:
            return
        for phrase in segmenter.feed(samples):
            try:
                self._phrases.put_nowait(phrase)
            except queue.Full:
                log.debug("Demasiadas frases en espera: se descarta una")

    def _capture(self) -> None:
        segmenter = SpeechSegmenter()
        try:
            with self.source_factory().recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK * 4) as recorder:
                while self.running:
                    self.feed(audio_io.to_mono(recorder.record(numframes=BLOCK)), segmenter)
        except Exception as exc:  # noqa: BLE001 - sin audio no hay subtítulos: se avisa
            log.exception("No se pudo capturar el audio")
            self.on_error(f"No se pudo escuchar el audio de la PC: {exc}")
            self._running.clear()

    def _transcribe(self) -> None:
        while self.running:
            try:
                phrase = self._phrases.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                result = self.transcriber.transcribe(phrase)
                if result is not None:
                    self.on_phrase(result)
            except Exception:  # noqa: BLE001 - una frase que falla no corta los subtítulos
                log.exception("No se pudo transcribir una frase")


class VoiceSpeaker:
    MAX_SECONDS = 20.0

    def __init__(
        self,
        transcriber: Transcriber,
        voices: Voices,
        translate: Callable[[str], tuple[str, str] | None],
        push_to_talk_vk: int,
        my_language: str,
        on_event: Callable[[str, str], None] = lambda _kind, _text: None,
        mic_factory: Callable = audio_io.microphone,
        on_playing: Callable[[float], None] = lambda _seconds: None,
    ) -> None:
        self.transcriber = transcriber
        self.voices = voices
        self.translate = translate  # texto en tu idioma -> (traducción, idioma) o None
        self.vk = push_to_talk_vk
        self.my_language = my_language
        self.on_event = on_event  # ("grabando" | "entendi" | "traduccion" | "error", texto)
        self.mic_factory = mic_factory
        self.on_playing = on_playing  # segundos que va a sonar tu voz traducida (para no escucharla como ajena)
        self.output = audio_io.voice_output()
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
                recorded = self._record_while_held()
                if recorded is not None and len(recorded) > SAMPLE_RATE * 0.4:
                    threading.Thread(target=self.speak, args=(recorded,), name="bubble-tu-voz-trad", daemon=True).start()
                down = False
            was_down = down
            time.sleep(0.015)

    def _record_while_held(self) -> np.ndarray | None:
        self.on_event("grabando", "")
        blocks: list[np.ndarray] = []
        try:
            with self.mic_factory().recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=BLOCK * 4) as recorder:
                started = time.monotonic()
                while self._held() and time.monotonic() - started < self.MAX_SECONDS and self.running:
                    blocks.append(audio_io.to_mono(recorder.record(numframes=BLOCK)))
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
            speech = self.voices.synthesize(text, language)
            if speech is None:
                self.on_event("error", f"No hay voz para el idioma «{language}»")
                return
            self.on_playing(len(speech.audio) / speech.sample_rate)
            audio_io.play(self.output, speech.audio, speech.sample_rate)
        except Exception as exc:  # noqa: BLE001
            log.exception("Falló la traducción de tu voz")
            self.on_event("error", str(exc))
