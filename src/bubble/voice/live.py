"""Escucha en vivo: convierte el audio del juego en frases con su hablante, el texto mientras hablan y el texto final.

Tres pasos, en hilos separados para que el audio nunca espere:

1. Captura y detector de voz (Silero): cada 32 ms determina si alguien habla. Una frase empieza con un poco de voz y
   termina con un silencio corto (o, si es muy larga, en su pausa más marcada).
2. Mientras la persona habla, cada ~0,4 s se transcribe lo acumulado con el modelo rápido para mostrarlo de inmediato.
3. Al terminar, se transcribe con el modelo preciso y se reconoce la voz (Voz 1, Voz 2…).

Cada cambio se notifica con `on_caption(Caption)`: la misma frase (mismo `id`) se actualiza hasta que llega `final`.
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
from .hearing import looks_like_noise, speech_level
from .asr import FastWhisper, Heard
from .speakers import SpeakerTracker
from .speech import Usual, melody, sounds_finished, sounds_unfinished
from .vad import FRAME, StreamingVad

log = logging.getLogger(__name__)
SAMPLE_RATE = 16000
BLOCK = FRAME * 3  # ~96 ms por lectura del dispositivo


@dataclass(frozen=True)
class Caption:
    id: int
    start: float  # posición en el audio escuchado (segundos desde el inicio de la escucha)
    end: float
    text: str = ""
    language: str = ""
    speaker: int = 0  # 0 = hablante aún desconocido
    final: bool = False
    stable: bool = False  # hubo una pausa y la frase parece terminada: el texto casi no cambiará
    intonation: str = ""  # "question" (la voz sube al final) | "exclaim" | "": Whisper no agrega ¿? ni ¡!
    sure: bool = False  # se entendió con seguridad


@dataclass
class Settings:
    start_prob: float = 0.5  # probabilidad de voz para iniciar una frase
    keep_prob: float = 0.35  # por debajo de este valor se considera silencio
    start_frames: int = 2  # ~64 ms de voz continua para iniciar
    end_silence_s: float = 0.45  # silencio que cierra la frase
    # Basta una pausa más corta si la frase ya parece terminada (". ? !") o ya es larga, para que salga antes.
    quick_end_s: float = 0.22
    quick_min_s: float = 1.0  # no se aplica a frases muy cortas: un fragmento breve no basta para detectar el idioma
    # Un fragmento corto que no se entendió con claridad ("no, pará," y una pausa) espera más: probablemente la frase
    # continúa y solo se entiende bien junto con lo que sigue.
    short_s: float = 1.0
    short_end_silence_s: float = 0.9
    long_after_s: float = 2.5
    max_seconds: float = 8.0  # una frase más larga se corta en la pausa más marcada
    split_overlap_s: float = 1.5  # la frase siguiente comienza un poco antes del corte (ver `drop_overlap`)
    pre_roll_s: float = 0.2
    min_speech_s: float = 0.25
    partial_every_s: float = 0.6
    first_partial_s: float = 0.45
    speaker_after_s: float = 1.0  # a partir de aquí se intenta identificar al hablante
    # En estos idiomas el modelo rápido entiende casi igual que el preciso: si su última lectura abarcó toda la frase,
    # esa es la versión final (sale ~0,5 s antes). En los demás (hindi, ruso…) el modelo preciso es necesario.
    fast_final_languages: tuple[str, ...] = ("en",)
    # Voz del jugador: en una pausa no se corta la frase hasta leer cómo termina lo dicho (la lectura comienza al
    # iniciarse la pausa). Si quedó inconclusa ("y…", "porque…", "el…"), se espera hasta `unfinished_end_s`.
    wait_for_tail: bool = False
    unfinished_end_s: float = 1.5


_SENTENCE_END = re.compile(r"[.!?…。！？]['\"»”)]*$")


def _plain(word: str) -> str:
    return re.sub(r"[^\w']", "", word.lower())


def drop_overlap(previous: str, text: str) -> str:
    """Una frase larga se corta con un poco de audio repetido (Whisper suele perder las últimas palabras de un audio
    cortado a mitad de oración). Aquí se eliminan del comienzo de `text` las palabras que ya estaban al final de
    `previous`.
    """
    before = [_plain(word) for word in previous.split()]
    words = text.split()
    after = [_plain(word) for word in words]
    for start in range(max(0, len(before) - 10), len(before)):
        tail = before[start:]
        # Una sola palabra repetida cuenta si es larga ("where"); "a" o "the" pueden coincidir por casualidad.
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
        self.partial_samples = 0  # audio de la última transcripción parcial
        self.want_tail = False  # comenzó un silencio: conviene leer ya cómo termina lo dicho
        self.overlaps = 0  # número de la frase anterior con la que comparte audio (frases largas cortadas)
        self.tail: tuple[Heard, int] | None = None  # última lectura hecha en una pausa y cuánto audio abarcó
        self.sentence_done = False  # lo último leído termina como una oración completa
        self.unfinished = False  # lo último leído quedó inconcluso ("y…", "porque…"): probablemente continúa
        self.tail_pending = False  # se está leyendo cómo termina (comenzó una pausa)
        self.clear = False  # lo último leído se entendió con seguridad
        self.language = ""
        self.speaker = 0
        self.text = ""
        self.far = False  # fuera del radio de escucha: no se transcribe ni se muestra

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
        source_factory: Callable = audio_io.game_audio,
        on_error: Callable[[str], None] = lambda _msg: None,
        settings: Settings | None = None,
        vad=None,
        language: str | None = None,
        native: str = "",
        hint="",
        judge=None,
        clean: bool = False,
        earshot=None,
        noise_filter: bool = False,
    ) -> None:
        """`hint`: ejemplo de cómo se habla, para Whisper (texto o función idioma → texto). `judge`: función que
        evalúa cómo lo dijo esa voz (perfil del jugador: su voz habitual y sus umbrales); sin ella, se compara
        cada voz del juego con su propio patrón. `earshot`: radio de escucha (voice/hearing.py): las voces
        lejanas no se entienden. `noise_filter`: lo que suena a ruido y no a una persona hablando no se muestra.
        """
        self.final_asr = final_asr
        self.partial_asr = partial_asr
        self.speakers = speakers
        self.on_caption = on_caption
        self.source_factory = source_factory
        self.on_error = on_error
        self.settings = settings or Settings()
        self.muted_until = 0.0  # mientras suena la voz traducida del jugador por los parlantes, no se escucha
        self._vad = vad or StreamingVad()
        # si se conoce (voz del jugador), no se detecta
        self.language = language.split("-")[0].lower() if language else None
        # Idioma del jugador: pesa más al detectar (el español rioplatense a veces se detecta como portugués), de modo
        # que lo dicho en ese idioma no se subtitula.
        self.native = native.split("-")[0].lower()
        self.hint = hint
        self.judge = judge
        # micrófono del jugador (no el juego): el ruido convertido en texto se descarta con más rigor
        self.clean = clean
        self.earshot = earshot
        self.noise_filter = noise_filter
        self._usual: dict[int, Usual] = {}  # cómo habla cada voz del juego (para detectar sus gritos)
        self._running = threading.Event()
        self._wake = threading.Condition()
        self._lock = threading.Lock()
        self._numbers = iter(range(1, 1 << 30))
        self._samples = 0  # total de audio recibido
        self._recent = np.zeros(0, dtype=np.float32)  # audio previo a una frase (para no cortar el comienzo)
        self._loud = 0
        self._silence = 0
        self._current: _Utterance | None = None
        self._finished: list[_Utterance] = []
        self._final_texts: dict[int, str] = {}
        self._languages: dict[str, float] = {}  # idiomas escuchados hasta ahora (pesan más al detectar)
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
            # Búfer de ~0,5 s: si la PC se ocupa un instante (juego, OCR), no se pierde audio.
            with self.source_factory().recorder(samplerate=SAMPLE_RATE, channels=1, blocksize=SAMPLE_RATE // 2) as recorder:
                while self.running:
                    self.feed(audio_io.to_mono(recorder.record(numframes=BLOCK)))
        except Exception as exc:  # noqa: BLE001 - sin audio no hay subtítulos: se avisa
            log.exception("No se pudo capturar el audio")
            self.on_error(f"No se pudo escuchar el audio de la PC: {exc}")
            self._running.clear()

    def feed(self, samples: np.ndarray) -> None:
        """Audio nuevo (mono, 16 kHz). Está separado de la captura para poder probarlo sin dispositivos."""
        samples = np.asarray(samples, dtype=np.float32).ravel()
        if time.monotonic() < self.muted_until:
            # Suena la voz traducida del jugador: no se escucha y se descarta lo acumulado.
            with self._lock:
                self._samples += len(samples)
                self._current, self._loud, self._recent = None, 0, np.zeros(0, dtype=np.float32)
            return
        probs = self._vad.feed(samples)
        # El detector trabaja en bloques de 32 ms: el audio se recorre al mismo paso.
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
                current.want_tail = current.sentence_done = current.clear = current.unfinished = False
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
            if s.wait_for_tail and (current.unfinished or current.tail_pending or current.want_tail):
                end = s.unfinished_end_s  # aún no se sabe cómo terminó, o quedó inconcluso: se espera más
                quick = quick and not current.unfinished
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
            # Sigue hablando: lo que queda es el comienzo de la frase siguiente (casi seguro del mismo hablante).
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
            if current.far:
                return None
            if tail or (first and seconds >= s.first_partial_s) or (not first and new >= s.partial_every_s):
                audio = current.audio().copy()
                if first and self.earshot is not None and not self.earshot.hears(speech_level(audio)):
                    current.far = True  # suena lejos: no se transcribe ni se muestra (y no consume procesador)
                    return None
                current.partial_samples = current.samples
                current.want_tail = False
                current.tail_pending = tail
                return lambda: self._partial(current, audio, tail)
        return None

    def _caption(self, utterance: _Utterance, text: str, language: str, final: bool,
                 stable: bool = False, intonation: str = "", sure: bool = False) -> Caption:
        start = utterance.start_sample / SAMPLE_RATE
        return Caption(utterance.number, start, start + utterance.samples / SAMPLE_RATE, text, language,
                       utterance.speaker, final, stable, intonation, sure)

    def _intonation(self, audio: np.ndarray, speaker: int = 0, learn: bool = False) -> str:
        """Cómo lo dijo, comparado con cómo habla esa voz normalmente (perfil del jugador o lo escuchado de esa
        voz).
        """
        tune = melody(audio)
        if tune is None:
            return ""
        if self.judge is not None:
            return self.judge(tune)
        usual = self._usual.setdefault(speaker, Usual()) if speaker else None
        kind = tune.kind(usual.get() if usual else None)
        if learn and usual is not None:
            usual.add(tune)
        return kind

    def _prior(self, strength: float) -> dict[str, float]:
        """Peso de cada idioma al detectarlo: los escuchados hasta ahora valen más (con poco audio, Whisper duda)."""
        total = sum(self._languages.values()) or 1.0
        prior = {language: 1.0 + strength * count / total for language, count in self._languages.items()}
        if self.native:
            prior[self.native] = prior.get(self.native, 1.0) + 1.0
        # En Roblox lo más común es el inglés: con acento o ruido, Whisper lo confundía con otros idiomas.
        prior["en"] = prior.get("en", 1.0) + 0.5
        return prior

    def _partial(self, utterance: _Utterance, audio: np.ndarray, tail: bool = False) -> None:
        strength = 3.0 if len(audio) < 2 * SAMPLE_RATE else 1.0
        try:
            heard = self.partial_asr.transcribe(audio, language=utterance.language or self.language,
                                                prior=self._prior(strength), hint=self.hint, clean=self.clean)
        finally:
            if tail:
                utterance.tail_pending = False
        if heard is None:
            return
        if tail:
            with self._lock:
                # Si mientras tanto volvió a hablar, esto ya no es el final.
                still_quiet = utterance.last_speech_sample <= utterance.start_sample + len(audio)
                language = heard.language or self.language or ""
                if self.settings.wait_for_tail:
                    utterance.sentence_done = still_quiet and sounds_finished(heard.text, language)
                    utterance.unfinished = still_quiet and sounds_unfinished(heard.text, language)
                else:
                    utterance.sentence_done = still_quiet and bool(_SENTENCE_END.search(heard.text))
                utterance.clear = still_quiet and heard.no_speech < 0.35 and heard.logprob > -0.8
                utterance.tail = (heard, len(audio))
        if not utterance.language and heard.language_prob >= 0.7 and len(audio) >= SAMPLE_RATE:
            utterance.language = heard.language  # idioma conocido: las pasadas siguientes no lo detectan
        if (self.speakers and not utterance.speaker
                and len(audio) >= self.settings.speaker_after_s * SAMPLE_RATE):
            utterance.speaker = self.speakers.peek(audio)
        utterance.text = heard.text
        stable = tail and utterance.sentence_done
        # Se notifica aunque la frase ya haya terminado: el texto final se calcula después, en este mismo hilo.
        self.on_caption(self._caption(utterance, heard.text, heard.language, final=False, stable=stable,
                                      intonation=self._intonation(audio) if stable else ""))

    def _finalize(self, utterance: _Utterance) -> None:
        audio = utterance.audio()
        if self.earshot is not None:
            level = speech_level(audio)
            near = self.earshot.hears(level)
            self.earshot.learn(level)
            if not near:
                if utterance.partial_samples:
                    self.on_caption(self._caption(utterance, "", utterance.language, final=True))  # se retira
                return
        heard: Heard | None = None
        if utterance.tail:
            quick, covered = utterance.tail
            if (quick.language in self.settings.fast_final_languages and quick.logprob > -0.7
                    and covered >= len(audio) - int(0.15 * SAMPLE_RATE)):
                heard = quick
        if heard is None:
            heard = self.final_asr.transcribe(audio, language=self.language, prior=self._prior(0.5), retry_beam=5,
                                              hint=self.hint, clean=self.clean)
        if self.speakers:
            # Si el audio no alcanza para reconocer la voz, se conserva la identificada mientras hablaba (o "Voz").
            utterance.speaker = self.speakers.identify(audio, hint=utterance.speaker)
        text = heard.text if heard else ""
        if text and self.noise_filter:
            ratio = min(1.0, utterance.speech_frames * FRAME / max(1, len(audio)))
            if looks_like_noise(text, heard.no_speech, heard.logprob, heard.language_prob, ratio):
                log.debug("Ruido, no una voz: %r", text)
                text = ""
        if text and utterance.overlaps:
            text = drop_overlap(self._final_texts.get(utterance.overlaps, ""), text)
        self._final_texts[utterance.number] = heard.text if heard else ""
        if len(self._final_texts) > 50:
            self._final_texts.pop(next(iter(self._final_texts)))
        if not text:
            if utterance.partial_samples:
                # Se había mostrado texto mientras hablaba: se retira.
                self.on_caption(self._caption(utterance, "", utterance.language, final=True))
            return
        for language in self._languages:
            self._languages[language] *= 0.9
        self._languages[heard.language] = self._languages.get(heard.language, 0.0) + 1.0
        self.on_caption(self._caption(utterance, text, heard.language, final=True,
                                      intonation=self._intonation(audio, utterance.speaker, learn=True),
                                      sure=heard.logprob > -0.35 and heard.no_speech < 0.3))
