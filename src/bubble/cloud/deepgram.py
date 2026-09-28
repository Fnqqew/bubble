"""Reconocimiento de voz en la nube con Deepgram (Nova-3), para Bubble Pro.

Dos formas, con la misma cara que el reconocimiento de tu PC (voice/asr.py y voice/live.py), así el resto de Bubble
no cambia:
- `DeepgramClip.transcribe(audio)`: una frase entera (tu voz con el botón, la página Pruebas). Se manda y vuelve el
  texto (~0,3 a 0,6 s).
- `DeepgramListener`: en vivo (las voces del juego, tu voz en modo directo). El audio va por una conexión abierta y
  Deepgram devuelve el texto mientras hablan (~0,3 s) y la frase terminada cuando hacen una pausa; con quién habla
  (Voz 1, Voz 2…) y el idioma de cada palabra (inglés, español, portugués… mezclados en la misma frase).

Solo se manda audio cuando el detector de voz de tu PC oye a alguien (con un poco de antes y después): Deepgram cobra
por el audio que recibe, y los silencios del juego no se pagan.

Si algo falla (sin conexión, sin saldo, la clave no sirve), Bubble vuelve solo al reconocimiento de tu PC y avisa.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import wave
from collections import Counter
from typing import Callable

import numpy as np

from .. import pro
from ..voice.asr import Heard
from .connection import pool
from .errors import BadKey, CloudError, NoCredit, error_for
from ..voice.hearing import cloud_noise, speech_level
from ..voice.live import Caption

log = logging.getLogger(__name__)
API = "https://api.deepgram.com/v1"
LISTEN_WS = "wss://api.deepgram.com/v1/listen"
MODEL = "nova-3"
# Idiomas que Nova-3 entiende mezclados en la misma frase ("hagamos pvp" en español con palabras en inglés).
MULTI = {"en", "es", "fr", "de", "hi", "ru", "pt", "ja", "it", "nl"}
SAMPLE_RATE = 16000


_error_for = error_for
# Idiomas que Nova-3 entiende de a uno (tu voz, cuando tu idioma no está entre los que se mezclan), según la
# documentación de Deepgram (28/9/2026). Si alguno lo rechaza, la conexión sigue con Nova-2 (el modelo anterior).
NOVA3 = MULTI | set("""af ar hy as be bn bs bg ca zh hr cs da et fi fr ka de el gu he hi hu id it ja kn kk ko lv lt mk
    ms mr mn ne no ps fa pl pt ro ru sr sk sl es sv tl ta te th tr uk ur vi""".split())
NOVA2 = set("""bg ca zh cs da nl en et fi fr de el hi hu id it ja ko lv lt ms no pl pt ro ru sk es sv th tr uk
    vi""".split())
# Palabras de juego que la nube tiene que reconocer bien (Deepgram las prioriza: "keyterm"). Se suman tus palabras.
GAME_TERMS = ("pvp", "obby", "noob", "lag", "gg", "afk", "Robux", "Roblox", "tradear", "farmear", "spawn", "loot",
              "nerf", "buff", "boss", "lobby", "tryhard", "carry", "clutch", "Brookhaven", "Blox Fruits", "Adopt Me",
              "Murder Mystery", "Bedwars", "Tower of Hell", "Jailbreak", "Doors", "Arsenal", "Da Hood", "Rivals",
              "Grow a Garden", "Steal a Brainrot", "99 Nights in the Forest")
KEYTERM_TOKENS = 450  # Deepgram acepta hasta 500 "tokens" entre todas


def language_param(language: str | None) -> str:
    code = (language or "").split("-")[0].lower()
    return "multi" if not code or code in MULTI else code


def model_for(language: str | None) -> str:
    """Nova-3 para casi todo; Nova-2 para un idioma que Nova-3 todavía no entiende."""
    code = (language or "").split("-")[0].lower()
    if not code or code in NOVA3:
        return MODEL
    return "nova-2" if code in NOVA2 else MODEL


def understands(language: str | None) -> bool:
    """¿La nube entiende ese idioma? (si no, tu voz se entiende con tu PC)."""
    code = (language or "").split("-")[0].lower()
    return not code or code in NOVA3 or code in NOVA2


def fit_keyterms(terms) -> list[str]:
    """Las palabras para priorizar, sin repetir y dentro del límite de Deepgram (las primeras son las que importan)."""
    chosen, used, seen = [], 0, set()
    for term in terms or ():
        term = " ".join(str(term).split())
        if not term or term.casefold() in seen or len(term) > 60:
            continue
        cost = max(1, round(len(term) / 3.5))  # ~3,5 letras por token
        if used + cost > KEYTERM_TOKENS or len(chosen) >= 100:
            break
        chosen.append(term)
        seen.add(term.casefold())
        used += cost
    return chosen


def check_key(key: str, timeout: float = 8.0) -> tuple[bool, str]:
    """¿La clave sirve? (le pregunta a Deepgram por tus proyectos; no gasta nada)."""
    if not key.strip():
        return False, "Pegá tu clave de Deepgram."
    request = urllib.request.Request(f"{API}/projects", headers={"Authorization": f"Token {key.strip()}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            json.loads(response.read() or b"{}")
        return True, "La clave anda."
    except urllib.error.HTTPError as exc:
        return False, str(_error_for(exc.code))
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, "No me pude conectar con Deepgram (¿internet?)."


def _wav(audio: np.ndarray) -> bytes:
    samples = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype(np.int16)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.tobytes())
    return buffer.getvalue()


def trim_silence(audio: np.ndarray, pad_s: float = 0.25) -> np.ndarray:
    """Saca el silencio del principio y del final (deja un poquito): Deepgram cobra por segundo mandado."""
    audio = np.asarray(audio, dtype=np.float32).ravel()
    frame = 512
    count = len(audio) // frame
    if count < 4:
        return audio
    rms = np.sqrt(np.mean(audio[:count * frame].reshape(count, frame) ** 2, axis=1)) + 1e-9
    level = 20 * np.log10(rms)
    loud = np.flatnonzero(level > max(-50.0, float(level.max()) - 35.0))
    if not len(loud):
        return audio[:0]
    pad = int(pad_s * SAMPLE_RATE)
    return audio[max(0, loud[0] * frame - pad):min(len(audio), (loud[-1] + 1) * frame + pad)]


def _majority(values, default):
    values = [v for v in values if v is not None and v != ""]
    return Counter(values).most_common(1)[0][0] if values else default


def parse_clip(data: dict, language: str | None, seconds: float, took: float) -> Heard | None:
    """La respuesta de Deepgram para una frase → lo mismo que devuelve Whisper (Heard)."""
    channel = (data.get("results", {}).get("channels") or [{}])[0]
    alternative = (channel.get("alternatives") or [{}])[0]
    text = (alternative.get("transcript") or "").strip()
    if not text:
        return None
    confidence = float(alternative.get("confidence") or 0.0)
    words = alternative.get("words") or []
    fallback = (alternative.get("languages") or [channel.get("detected_language") or (language or "en")])[0]
    detected = _majority([w.get("language") for w in words], fallback)
    return Heard(text, str(detected).split("-")[0].lower(), 1.0, 0.0, math.log(max(confidence, 1e-3)), seconds, took)


class DeepgramClip:
    """Una frase entera a la nube (misma forma que FastWhisper.transcribe)."""

    def __init__(self, key: str, timeout: float = 6.0, keyterms: Callable[[], list[str]] | list[str] = ()) -> None:
        self.key = key
        self.timeout = timeout
        self.name = "deepgram"
        self.keyterms = keyterms

    def warm(self) -> None:
        pool(self.timeout).warm()

    def transcribe(self, audio: np.ndarray, language: str | None = None, beam_size: int = 1,
                   prior: dict[str, float] | None = None, retry_beam: int = 0, hint="", clean: bool = False
                   ) -> Heard | None:
        started = time.perf_counter()
        audio = trim_silence(audio)  # el silencio de antes y después no se paga
        seconds = len(audio) / SAMPLE_RATE
        if seconds < 0.2:
            return None
        params = {"model": model_for(language), "language": language_param(language), "smart_format": "true",
                  "punctuate": "true"}
        terms = self.keyterms() if callable(self.keyterms) else self.keyterms
        query = [*params.items()]
        chosen = fit_keyterms(terms) if params["model"] == MODEL else []
        query += [("keyterm", term) for term in chosen]
        raw = pool(self.timeout).request("POST", f"/v1/listen?{urllib.parse.urlencode(query)}", _wav(audio),
                                         {"Authorization": f"Token {self.key}", "Content-Type": "audio/wav"})
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise CloudError("Deepgram respondió algo que no se entiende") from exc
        pro.count_seconds(seconds, multi=params["language"] == "multi", keyterms=bool(chosen), clip=True)
        heard = parse_clip(data, language, seconds, time.perf_counter() - started)
        if heard is not None and clean and heard.logprob < math.log(0.35):
            return None  # casi seguro que era un ruido, no una frase
        return heard


class WithFallback:
    """Primero la nube; si falla (sin conexión, sin saldo), el reconocimiento de tu PC. `on_fail` avisa."""

    def __init__(self, cloud, local, on_fail: Callable[[CloudError], None]) -> None:
        self.cloud, self.local, self.on_fail = cloud, local, on_fail
        self.name = getattr(cloud, "name", "nube")

    def transcribe(self, audio, *args, **kwargs):
        try:
            return self.cloud.transcribe(audio, *args, **kwargs)
        except CloudError as exc:
            self.on_fail(exc)
            return self.local.transcribe(audio, *args, **kwargs)


_FINALIZE = object()


def _next(outbox: queue.Queue):
    """Lo próximo para mandar (None si en un segundo no hubo nada: para mandar KeepAlive o ver si se apagó)."""
    try:
        return outbox.get(timeout=1.0)
    except queue.Empty:
        return None


class DeepgramListener:
    """En vivo, con la cara de LiveListener: avisa con `on_caption(Caption)`, la misma frase (mismo `id`) se va
    completando hasta `final`."""

    PREROLL_S = 0.4  # se manda un poco de antes de que empiece a hablar (si no, se come la primera sílaba)
    # Después de la última voz se manda un poquito de silencio (para que Deepgram note la pausa y cierre la frase
    # enseguida) y no más: los silencios largos dentro de una frase no se pagan. A los HOLD_S se da por terminada.
    TAIL_S = 0.4
    HOLD_S = 1.2
    KEEPALIVE_S = 3.0  # Deepgram corta si pasan 10 s sin recibir nada
    BLOCK = 1536  # ~96 ms por lectura

    def __init__(self, key: str, on_caption: Callable[[Caption], None], source_factory: Callable,
                 on_error: Callable[[str], None] = lambda _msg: None,
                 on_fatal: Callable[[CloudError], None] = lambda _exc: None, language: str | None = None,
                 diarize: bool = True, judge=None, vad=None, endpointing_ms: int = 300, earshot=None,
                 noise_filter: bool = False, keyterms: Callable[[], list[str]] | list[str] = (),
                 speakers=None) -> None:
        self.key = key
        self.on_caption = on_caption
        self.source_factory = source_factory
        self.on_error = on_error
        self.on_fatal = on_fatal
        self.language = language.split("-")[0].lower() if language else None
        self.diarize = diarize
        self.judge = judge  # cómo lo dijo esa voz (tu perfil); sin eso, se compara cada voz con cómo viene hablando
        self.endpointing_ms = endpointing_ms
        self._older_model = False  # Nova-3 no aceptó el idioma: se usa Nova-2
        # Radio de escucha y filtro de ruido (voice/hearing.py): lo que suena lejos ni se manda (no se paga).
        self.earshot = earshot
        self.noise_filter = noise_filter
        self._far = False  # la voz que está sonando quedó fuera del radio: no se manda hasta el próximo silencio
        # Palabras que conviene reconocer bien (jerga de juego, tus palabras): Deepgram las prioriza (cobra aparte).
        self.keyterms = keyterms
        self._with_keyterms = False
        # Quién habla sin pagar la separación de la nube: el reconocimiento de voces de tu PC (voice/speakers.py).
        self.speakers = speakers if not diarize else None
        self._open = False  # hay una frase abierta (se está hablando o hubo voz hace menos de HOLD_S)
        self.muted_until = 0.0  # mientras suena tu voz traducida por los parlantes, no se escucha
        self._vad = vad
        self._running = threading.Event()
        self._outbox: queue.Queue = queue.Queue()
        self._threads: list[threading.Thread] = []
        # lo que se viene mandando: para medir la entonación de cada frase (los tiempos de Deepgram son de este audio)
        self._sent = np.zeros(0, dtype=np.float32)
        self._sent_offset = 0.0  # segundos de audio mandado que ya no están en `_sent`
        self._recent = np.zeros(0, dtype=np.float32)
        self._sending = False
        self._last_voice = 0.0
        self._clock = 0.0  # segundos de audio escuchado
        self._unbilled = 0.0
        self._pieces: list[tuple[str, list[dict], float, float]] = []
        self._ids = iter(range(1, 1 << 30))
        self._current = next(self._ids)
        self._usual: dict[int, object] = {}

    @property
    def running(self) -> bool:
        return self._running.is_set()

    def start(self) -> None:
        if self.running:
            return
        # Cada vez que se prende, su propia señal y su propia cola: si se apaga y se prende enseguida, los hilos de
        # antes terminan solos y no se mezclan con los nuevos.
        run, outbox = threading.Event(), queue.Queue()
        run.set()
        self._running, self._outbox = run, outbox
        self._threads = [threading.Thread(target=self._capture, args=(run,), name="bubble-nube-captura", daemon=True),
                         threading.Thread(target=self._network, args=(run, outbox), name="bubble-nube-red",
                                          daemon=True)]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._running.clear()
        self._outbox.put(None)
        self._flush_usage()

    # ------------------------------------------------------------ audio → solo lo que tiene voz
    def _capture(self, run: threading.Event) -> None:
        from ..voice import audio as audio_io

        try:
            audio_io.com_ready()
            with self.source_factory().recorder(samplerate=SAMPLE_RATE, channels=1,
                                                blocksize=SAMPLE_RATE // 2) as recorder:
                while run.is_set():
                    self.feed(audio_io.to_mono(recorder.record(numframes=self.BLOCK)))
        except Exception as exc:  # noqa: BLE001 - sin audio no hay subtítulos: se avisa
            log.exception("No se pudo capturar el audio (nube)")
            self.on_error(f"No se pudo escuchar el audio: {exc}")
            run.clear()

    def feed(self, samples: np.ndarray) -> None:
        """Audio nuevo (mono, 16 kHz). Separado de la captura para poder probarlo sin dispositivos."""
        samples = np.asarray(samples, dtype=np.float32).ravel()
        now = self._clock = self._clock + len(samples) / SAMPLE_RATE
        if time.monotonic() < self.muted_until:
            samples = np.zeros_like(samples)  # suena tu voz traducida: no se escucha
        if self._vad is None:
            from ..voice.vad import StreamingVad

            self._vad = StreamingVad()
        probs = self._vad.feed(samples)
        voice = len(probs) > 0 and float(np.max(probs)) >= 0.5
        if voice:
            self._last_voice = now
            if not self._open and not self._far and self.earshot is not None:
                level = speech_level(np.concatenate([self._recent, samples]))
                self._far = not self.earshot.hears(level)
                self.earshot.learn(level)
            if not self._sending and not self._far:
                self._sending = self._open = True
                self._send(self._recent)  # lo de antes: el comienzo de la palabra
        elif self._far and now - self._last_voice > self.HOLD_S:
            self._far = False  # terminó esa voz: la próxima se vuelve a medir
        self._recent = np.concatenate([self._recent, samples])[-int(self.PREROLL_S * SAMPLE_RATE):]
        if self._sending:
            self._send(samples)
            if now - self._last_voice > self.TAIL_S:
                self._sending = False  # pausa: no se manda más silencio (si vuelve a hablar, se retoma)
        if self._open and now - self._last_voice > self.HOLD_S:
            self._open = False
            self._outbox.put(_FINALIZE)  # terminó de hablar: que Deepgram cierre la frase ya

    def _send(self, samples: np.ndarray) -> None:
        if not len(samples):
            return
        self._sent = np.concatenate([self._sent, samples])
        keep = 30 * SAMPLE_RATE
        if len(self._sent) > keep:
            self._sent_offset += (len(self._sent) - keep) / SAMPLE_RATE
            self._sent = self._sent[-keep:]
        self._unbilled += len(samples) / SAMPLE_RATE
        if self._unbilled >= 30:
            self._flush_usage()
        self._outbox.put((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())

    def _flush_usage(self) -> None:
        seconds, self._unbilled = self._unbilled, 0.0
        try:
            pro.count_seconds(seconds, diarized=self.diarize, multi=language_param(self.language) == "multi",
                              keyterms=self._with_keyterms)
        except Exception:  # noqa: BLE001 - es solo la cuenta
            log.debug("No se pudo anotar el uso de la nube", exc_info=True)

    # ------------------------------------------------------------ la conexión con Deepgram
    def url(self) -> str:
        model = "nova-2" if self._older_model else model_for(self.language)
        params = {"model": model, "language": language_param(self.language), "encoding": "linear16",
                  "sample_rate": SAMPLE_RATE, "channels": 1, "interim_results": "true",
                  "endpointing": self.endpointing_ms, "utterance_end_ms": 1000, "smart_format": "true",
                  "punctuate": "true", "diarize": "true" if self.diarize else "false"}
        terms = self.keyterms() if callable(self.keyterms) else self.keyterms
        if params["model"] != MODEL:
            terms = ()  # Nova-2 no las acepta
        chosen = fit_keyterms(terms)
        self._with_keyterms = bool(chosen)
        query = urllib.parse.urlencode([*params.items(), *(("keyterm", term) for term in chosen)])
        return f"{LISTEN_WS}?{query}"

    def _network(self, run: threading.Event, outbox: queue.Queue) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._sessions(run, outbox))
        finally:
            loop.close()

    async def _sessions(self, run: threading.Event, outbox: queue.Queue) -> None:
        from websockets.asyncio.client import connect
        from websockets.exceptions import InvalidStatus

        wait = 1.0
        while run.is_set():
            try:
                async with connect(self.url(), additional_headers={"Authorization": f"Token {self.key}"},
                                   open_timeout=10, ping_interval=None, max_size=None) as socket:
                    wait = 1.0
                    sender = asyncio.ensure_future(self._sender(socket, run, outbox))
                    try:
                        async for message in socket:
                            if isinstance(message, str):
                                self.handle(json.loads(message))
                    finally:
                        sender.cancel()
            except InvalidStatus as exc:
                error = _error_for(exc.response.status_code)
                if isinstance(error, (BadKey, NoCredit)):
                    self.on_fatal(error)
                    run.clear()
                    return
                if exc.response.status_code == 400 and not self._older_model and self.language in NOVA2:
                    self._older_model = True  # ese idioma, con el modelo anterior
                    continue
                self.on_error(str(error))
            except Exception as exc:  # noqa: BLE001 - se reintenta
                if run.is_set():
                    log.warning("Se cortó la conexión con Deepgram: %s", exc)
            if run.is_set():
                await asyncio.sleep(wait)
                wait = min(wait * 2, 15.0)

    async def _sender(self, socket, run: threading.Event, outbox: queue.Queue) -> None:
        loop = asyncio.get_running_loop()
        await socket.send(json.dumps({"type": "KeepAlive"}))
        last = time.monotonic()
        while run.is_set():
            item = await loop.run_in_executor(None, _next, outbox)
            if item is None and not run.is_set():
                await socket.send(json.dumps({"type": "CloseStream"}))
                return
            if item is _FINALIZE:
                await socket.send(json.dumps({"type": "Finalize"}))
            elif item is not None:
                await socket.send(item)
                last = time.monotonic()
            if time.monotonic() - last >= self.KEEPALIVE_S:
                # Sin audio (silencio, fuera del juego) la conexión sigue abierta: se cuenta desde el último audio,
                # no solo en los ratos sin nada (antes, tras un "Finalize" se cortaba a los 10 s: NET-0001).
                await socket.send(json.dumps({"type": "KeepAlive"}))
                last = time.monotonic()

    # ------------------------------------------------------------ lo que responde → frases
    def handle(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "Results":
            alternative = (message.get("channel", {}).get("alternatives") or [{}])[0]
            text = (alternative.get("transcript") or "").strip()
            start = float(message.get("start") or 0.0)
            end = start + float(message.get("duration") or 0.0)
            piece = (text, alternative.get("words") or [], start, end)
            if message.get("is_final"):
                if text:
                    self._pieces.append(piece)
                if message.get("speech_final"):
                    self._emit(final=True)
                elif self._pieces:
                    self._emit(final=False)
            elif text:
                self._emit(final=False, interim=piece)
        elif kind == "UtteranceEnd" and self._pieces:
            self._emit(final=True)

    def _emit(self, final: bool, interim: tuple | None = None) -> None:
        pieces = [*self._pieces, *([interim] if interim else [])]
        if not pieces:
            return
        text = " ".join(p[0] for p in pieces if p[0]).strip()
        words = [w for p in pieces for w in p[1]]
        start, end = pieces[0][2], pieces[-1][3]
        if final and self.noise_filter and cloud_noise(text, [float(w.get("confidence", 0.0)) for w in words]):
            text = ""  # ruido, no una voz: si se estaba mostrando, se retira
        speaker = int(_majority([w.get("speaker") for w in words], -1)) + 1 if self.diarize else 0
        if final and text and self.speakers is not None:
            speaker = self._local_speaker(start, end)
        language = str(_majority([w.get("language") for w in words], self.language or "en")).split("-")[0]
        confidence = float(np.mean([w.get("confidence", 0.0) for w in words])) if words else 0.0
        intonation = self._intonation(start, end, speaker) if final else ""
        self.on_caption(Caption(self._current, start, end, text, language, max(0, speaker), final, False,
                                intonation, confidence > 0.8))
        if final:
            self._pieces = []
            self._current = next(self._ids)

    def _segment(self, start: float, end: float) -> np.ndarray:
        """El audio que se mandó para esa frase (los tiempos de Deepgram son de ese audio)."""
        begin = int((start - self._sent_offset) * SAMPLE_RATE)
        stop = int((end - self._sent_offset) * SAMPLE_RATE)
        if begin < 0 or stop <= begin:
            return np.zeros(0, dtype=np.float32)
        return self._sent[begin:stop]

    def _local_speaker(self, start: float, end: float) -> int:
        try:
            audio = self._segment(start, end)
            return self.speakers.identify(audio) if len(audio) >= SAMPLE_RATE // 2 else 0
        except Exception:  # noqa: BLE001 - sin saber quién es, queda "Voz"
            log.debug("No se pudo reconocer la voz", exc_info=True)
            return 0

    def _intonation(self, start: float, end: float, speaker: int) -> str:
        """Cómo lo dijo (pregunta, grito…), con el audio que se mandó para esa frase."""
        from ..voice.speech import Usual, melody

        begin = int((start - self._sent_offset) * SAMPLE_RATE)
        stop = int((end - self._sent_offset) * SAMPLE_RATE)
        if begin < 0 or stop <= begin:
            return ""
        tune = melody(self._sent[begin:stop])
        if tune is None:
            return ""
        if self.judge is not None:
            return self.judge(tune)
        usual = self._usual.setdefault(speaker, Usual())
        kind = tune.kind(usual.get())
        usual.add(tune)
        return kind
