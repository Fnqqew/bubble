"""Escucha en vivo: lo que suena en el juego → frases con quién las dice, el texto mientras hablan y el final.

Tres pasos, en hilos separados para que el audio nunca espere:

1. Captura + detector de voz (Silero): cada 32 ms, ¿hay alguien hablando? Una frase empieza con un poco de voz y
   termina con un silencio corto (o si es muy larga, en su pausa más marcada).
2. Mientras la persona habla, cada ~0,4 s se transcribe lo que va (modelo rápido) para mostrarlo ya.
3. Cuando termina, se transcribe con el modelo preciso y se reconoce la voz (Voz 1, Voz 2…).

Cada cambio se avisa con `on_caption(Caption)`: la misma frase (mismo `id`) se va actualizando hasta `final`.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import audio as audio_io
from .asr import FastWhisper, Heard
from .speakers import SpeakerTracker
from .vad import FRAME, StreamingVad

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
BLOCK = FRAME * 3  # ~96 ms por lectura del dispositivo


@dataclass(frozen=True)
class Caption:
    id: int
    start: float  # posición en el audio escuchado (segundos desde que empezó a escuchar)
    end: float
    text: str = ""
    language: str = ""
    speaker: int = 0  # 0 = todavía no se sabe quién es
    final: bool = False
    stable: bool = False  # hizo una pausa y la frase suena terminada: el texto casi seguro no cambia


@dataclass
class Settings:
    start_prob: float = 0.5  # probabilidad de voz para empezar una frase
    keep_prob: float = 0.35  # por debajo de esto es silencio
    start_frames: int = 2  # ~64 ms de voz seguida para empezar
    end_silence_s: float = 0.45  # silencio que cierra la frase
    # Una pausa más corta alcanza si la frase ya suena terminada (". ? !") o si ya es larga: sale antes.
    quick_end_s: float = 0.22
    quick_min_s: float = 1.0  # pero no en frases muy cortas: un pedazo corto no alcanza para saber el idioma
    # Un pedazo corto que no se entendió claro ("no, pará," y una pausa) espera más: seguramente la frase sigue, y
    # solo se entiende bien junto con lo que viene.
    short_s: float = 1.0
    short_end_silence_s: float = 0.9
    long_after_s: float = 2.5
    max_seconds: float = 8.0  # más largo que esto se corta en la pausa más marcada
    split_overlap_s: float = 1.5  # la frase siguiente arranca un poco antes del corte (ver `drop_overlap`)
    pre_roll_s: float = 0.2
    min_speech_s: float = 0.25
    partial_every_s: float = 0.6
    first_partial_s: float = 0.45
    speaker_after_s: float = 1.0  # con esto ya se intenta saber quién es
    # En estos idiomas el modelo rápido entiende casi igual que el preciso: si su última lectura abarcó toda la
    # frase, esa es la versión final (sale ~0,5 s antes). En los demás (hindi, ruso…) el preciso hace falta.
    fast_final_languages: tuple[str, ...] = ("en",)


_SENTENCE_END = re.compile(r"[.!?…。！？]['\"»”)]*$")


def _plain(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def drop_overlap(previous: str, text: str) -> str:
    """Una frase larga se corta con un poco de audio repetido (Whisper suele perder las últimas palabras de un audio
    cortado a mitad de oración). Acá se sacan del comienzo de `text` las palabras que ya estaban al final de
    `previous`."""
    before = [_plain(word) for word in previous.split()]
    words = text.split()
    after = [_plain(word) for word in words]
    for start in range(max(0, len(before) - 10), len(before)):
        tail = before[start:]
        # Una sola palabra repetida cuenta si es larga ("where"); "a" o "the" pueden coincidir de casualidad.
        if (len(tail) >= 2 or len(tail[0]) >= 4) and after[:len(tail)] == tail:
            return " ".join(words[len(tail):])
    return text


class _Utterance:
    def __init__(self, number: int, start_sample: int, audio: np.ndarray) -> None:
        self.number = number
        self.start_sample = start_sample
        self.chunks = [audio]
        self.samples = len(audio)
        self.speech_frames = 0
        self.last_speech_sample = start_sample + len(audio)
        self.probs: list[float] = []
        self.partial_samples = 0  # cuánto audio tenía la última transcripción parcial
        self.want_tail = False  # empezó un silencio: conviene leer ya cómo termina lo que dijo
        self.overlaps = 0  # número de la frase anterior con la que comparte audio (frases largas cortadas)
        self.tail: tuple[Heard, int] | None = None  # última lectura hecha en una pausa y cuánto audio abarcó
        self.sentence_done = False  # lo último leído termina como una oración completa
        self.clear = False  # lo último leído se entendió con seguridad
        self.language = ""
        self.speaker = 0
        self.text = ""

    def audio(self) -> np.ndarray:
        if len(self.chunks) > 1:
            self.chunks = [np.concatenate(self.chunks)]
        return self.chunks[0]


class LiveListener:
    def __init__(
        self,
        final_asr: FastWhisper,
        on_caption: Callable[[Caption], None],
        partial_asr: FastWhisper | None = None,
        speakers: SpeakerTracker | None = None,
        source_factory: Callable = audio_io.speaker_loopback,
        on_error: Callable[[str], None] = lambda _msg: None,
        settings: Settings | None = None,
        vad=None,
        language: str | None = None,
        native: str = "",
    ) -> None:
        self.final_asr = final_asr
        self.partial_asr = partial_asr
        self.speakers = speakers
        self.on_caption = on_caption
        self.source_factory = source_factory
        self.on_error = on_error
        self.settings = settings or Settings()
        self.muted_until = 0.0  # mientras suena tu propia voz traducida por los parlantes, no se escucha
        self._vad = vad or StreamingVad()
        self.language = language.split("-")[0].lower() if language else None  # si se sabe (tu voz), no se detecta
        # Tu idioma: pesa más al detectar (el español rioplatense a veces sale como portugués), así lo que se dice
        # en tu idioma no se subtitula.
        self.native = native.split("-")[0].lower()
        self._running = threading.Event()
        self._wake = threading.Condition()
        self._lock = threading.Lock()
        self._numbers = iter(range(1, 1 << 30))
        self._samples = 0  # audio recibido en total
        self._recent = np.zeros(0, dtype=np.float32)  # lo último antes de una frase (para no cortar el comienzo)
        self._loud = 0
        self._silence = 0
        self._current: _Utterance | None = None
        self._finished: list[_Utterance] = []
        self._final_texts: dict[int, str] = {}
        self._languages: dict[str, float] = {}  # idiomas que se vienen escuchando (pesan más al detectar)
        self._threads: list[threading.Thread] = []

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        self._running.set()
        self._threads = [threading.Thread(target=self._capture, name="bubble-voz-captura", daemon=True),
                         threading.Thread(target=self._work, name="bubble-voz-texto", daemon=True)]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._running.clear()
        with self._wake:
            self._wake.notify_all()

    # ------------------------------------------------------------ 1. captura y detector de voz
    def _capture(self) -> None:
        try:
            audio_io.com_ready()
            # Búfer de ~0,5 s: si la PC está ocupada un instante (el juego, el OCR), no se pierde audio.
            with self.source_factory().recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=SAMPLE_RATE // 2) as recorder:
                while self.running:
                    self.feed(audio_io.to_mono(recorder.record(numframes=BLOCK)))
        except Exception as exc:  # noqa: BLE001 - sin audio no hay subtítulos: se avisa
            log.exception("No se pudo capturar el audio")
            self.on_error(f"No se pudo escuchar el audio de la PC: {exc}")
            self._running.clear()

    def feed(self, samples: np.ndarray) -> None:
        """Audio nuevo (mono, 16 kHz). Separado de la captura para poder probarlo sin dispositivos."""
        samples = np.asarray(samples, dtype=np.float32).ravel()
        if time.monotonic() < self.muted_until:
            # Suena tu propia voz traducida: no se escucha (y lo que se venía escuchando se descarta).
            with self._lock:
                self._samples += len(samples)
                self._current, self._loud, self._recent = None, 0, np.zeros(0, dtype=np.float32)
            return
        probs = self._vad.feed(samples)
        # El detector trabaja de a 32 ms: se recorre el audio al mismo paso.
        offset = len(samples) - len(probs) * FRAME
        pending, samples = samples[:max(0, offset)], samples[max(0, offset):]
        if len(pending):
            self._append(pending, None)
        for index, prob in enumerate(probs):
            self._append(samples[index * FRAME:(index + 1) * FRAME], float(prob))

    def _append(self, frame: np.ndarray, prob: float | None) -> None:
        s = self.settings
        self._samples += len(frame)
        with self._lock:
            current = self._current
            if current is None:
                self._recent = np.concatenate([self._recent, frame])[-int(s.pre_roll_s * SAMPLE_RATE):]
                if prob is None:
                    return
                self._loud = self._loud + 1 if prob >= s.start_prob else 0
                if self._loud >= s.start_frames:
                    start = self._samples - len(self._recent)
                    self._current = _Utterance(next(self._numbers), start, self._recent.copy())
                    self._current.speech_frames = self._loud
                    self._recent = np.zeros(0, dtype=np.float32)
                    self._silence = 0
                return
            current.chunks.append(frame)
            current.samples += len(frame)
            if prob is None:
                return
            current.probs.append(prob)
            if prob >= s.keep_prob:
                current.speech_frames += 1
                current.last_speech_sample = self._samples
                current.want_tail = current.sentence_done = current.clear = False
                self._silence = 0
            else:
                self._silence += 1
                if self._silence == 1:
                    current.want_tail = True
            seconds = current.samples / SAMPLE_RATE
            silence = self._silence * FRAME / SAMPLE_RATE
            quick = seconds >= s.long_after_s or (current.sentence_done and seconds >= s.quick_min_s)
            short_and_unclear = current.speech_frames * FRAME / SAMPLE_RATE < s.short_s and not current.clear
            end = s.short_end_silence_s if short_and_unclear else s.end_silence_s
            if silence >= end or (quick and silence >= s.quick_end_s):
                self._close(current)
            elif seconds >= s.max_seconds:
                self._split(current)
        with self._wake:
            self._wake.notify()

    def _close(self, utterance: _Utterance, keep_from: int | None = None) -> None:
        """Termina la frase (con el lock tomado). `keep_from`: muestra donde se corta una frase larga."""
        s = self.settings
        audio = utterance.audio()
        if keep_from is None:
            tail = int(0.1 * SAMPLE_RATE)
            end = min(len(audio), utterance.last_speech_sample - utterance.start_sample + tail)
            rest = np.zeros(0, dtype=np.float32)
        else:
            end = keep_from
            rest = audio[max(0, keep_from - int(s.split_overlap_s * SAMPLE_RATE)):]
        utterance.chunks = [audio[:end]]
        utterance.samples = end
        self._current = None
        self._silence = 0
        self._loud = 0
        if utterance.speech_frames * FRAME / SAMPLE_RATE >= s.min_speech_s:
            self._finished.append(utterance)
        if len(rest):
            # Sigue hablando: lo que queda es el comienzo de la frase siguiente (misma persona, casi seguro).
            follow = _Utterance(next(self._numbers), utterance.start_sample + len(audio) - len(rest), rest)
            follow.speech_frames = int(len(rest) / FRAME)
            follow.last_speech_sample = utterance.start_sample + len(audio)
            follow.speaker = utterance.speaker
            follow.language = utterance.language
            follow.overlaps = utterance.number
            self._current = follow

    def _split(self, utterance: _Utterance) -> None:
        # Frase muy larga: se corta en la pausa más larga de los últimos 2,5 s (o en el momento más "silencioso").
        probs = np.array(utterance.probs)
        window = min(len(probs), int(2.5 * SAMPLE_RATE / FRAME))
        recent = probs[-window:]
        quiet = recent < self.settings.keep_prob
        best, best_len, run = -1, 0, 0
        for index, is_quiet in enumerate(quiet):
            run = run + 1 if is_quiet else 0
            if run >= best_len and run >= 2:
                best, best_len = index - run // 2, run
        if best < 0:
            smooth = np.convolve(recent, np.ones(3) / 3, mode="same")
            best = int(np.argmin(smooth))
        position = best + len(probs) - window
        keep_from = utterance.samples - (len(probs) - position) * FRAME
        self._close(utterance, keep_from=max(FRAME, keep_from))

    # ------------------------------------------------------------ 2 y 3. texto y quién habla
    def _work(self) -> None:
        while self.running:
            job = None
            with self._wake:
                job = self._next_job()
                if job is None:
                    self._wake.wait(timeout=0.2)
                    continue
            try:
                job()
            except Exception:  # noqa: BLE001 - una frase que falla no corta los subtítulos
                log.exception("Falló la transcripción de una frase")

    def _next_job(self):
        s = self.settings
        with self._lock:
            if self._finished:
                utterance = self._finished.pop(0)
                return lambda: self._finalize(utterance)
            current = self._current
            if current is None or self.partial_asr is None:
                return None
            seconds = current.samples / SAMPLE_RATE
            new = (current.samples - current.partial_samples) / SAMPLE_RATE
            first = current.partial_samples == 0
            tail = current.want_tail and seconds >= min(s.first_partial_s, 0.45)
            if tail or (first and seconds >= s.first_partial_s) or (not first and new >= s.partial_every_s):
                current.partial_samples = current.samples
                current.want_tail = False
                audio = current.audio().copy()
                return lambda: self._partial(current, audio, tail)
        return None

    def _caption(self, utterance: _Utterance, text: str, language: str, final: bool,
                 stable: bool = False) -> Caption:
        start = utterance.start_sample / SAMPLE_RATE
        return Caption(utterance.number, start, start + utterance.samples / SAMPLE_RATE, text, language,
                       utterance.speaker, final, stable)

    def _prior(self, strength: float) -> dict[str, float]:
        """Peso de cada idioma al detectarlo: los que se vienen escuchando valen más (con poco audio, Whisper duda)."""
        total = sum(self._languages.values()) or 1.0
        prior = {language: 1.0 + strength * count / total for language, count in self._languages.items()}
        if self.native:
            prior[self.native] = prior.get(self.native, 1.0) + 1.0
        return prior

    def _partial(self, utterance: _Utterance, audio: np.ndarray, tail: bool = False) -> None:
        strength = 3.0 if len(audio) < 2 * SAMPLE_RATE else 1.0
        heard = self.partial_asr.transcribe(audio, language=utterance.language or self.language,
                                            prior=self._prior(strength))
        if heard is None:
            return
        if tail:
            with self._lock:
                # Si mientras tanto volvió a hablar, esto ya no es el final.
                still_quiet = utterance.last_speech_sample <= utterance.start_sample + len(audio)
                utterance.sentence_done = still_quiet and bool(_SENTENCE_END.search(heard.text))
                utterance.clear = still_quiet and heard.no_speech < 0.35 and heard.logprob > -0.8
                utterance.tail = (heard, len(audio))
        if not utterance.language and heard.language_prob >= 0.7 and len(audio) >= SAMPLE_RATE:
            utterance.language = heard.language  # ya se sabe el idioma: las próximas pasadas no lo detectan
        if (self.speakers and not utterance.speaker
                and len(audio) >= self.settings.speaker_after_s * SAMPLE_RATE):
            utterance.speaker = self.speakers.peek(audio)
        utterance.text = heard.text
        # Se avisa aunque la frase ya haya terminado: el texto final se calcula en este mismo hilo, después.
        self.on_caption(self._caption(utterance, heard.text, heard.language, final=False,
                                      stable=tail and utterance.sentence_done))

    def _finalize(self, utterance: _Utterance) -> None:
        audio = utterance.audio()
        heard: Heard | None = None
        if utterance.tail:
            quick, covered = utterance.tail
            if (quick.language in self.settings.fast_final_languages and quick.logprob > -0.7
                    and covered >= len(audio) - int(0.15 * SAMPLE_RATE)):
                heard = quick
        if heard is None:
            heard = self.final_asr.transcribe(audio, language=self.language, beam_size=1, prior=self._prior(0.5))
        if self.speakers:
            # Si no alcanza el audio para reconocer la voz, queda la que se supo mientras hablaba (o "Voz").
            utterance.speaker = self.speakers.identify(audio, hint=utterance.speaker)
        text = heard.text if heard else ""
        if text and utterance.overlaps:
            text = drop_overlap(self._final_texts.get(utterance.overlaps, ""), text)
        self._final_texts[utterance.number] = heard.text if heard else ""
        if len(self._final_texts) > 50:
            self._final_texts.pop(next(iter(self._final_texts)))
        if not text:
            if utterance.partial_samples:
                # Se había mostrado algo mientras hablaba: se retira.
                self.on_caption(self._caption(utterance, "", utterance.language, final=True))
            return
        for language in self._languages:
            self._languages[language] *= 0.9
        self._languages[heard.language] = self._languages.get(heard.language, 0.0) + 1.0
        self.on_caption(self._caption(utterance, text, heard.language, final=True))
