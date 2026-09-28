"""Entrada y salida de audio (WASAPI, vía soundcard).

- Lo que suena en la PC (el juego, incluido el chat de voz): captura "loopback" del parlante.
- Tu micrófono.
- La salida hacia el micrófono virtual (VB-Audio Virtual Cable): lo que se reproduce en «CABLE Input» Roblox lo recibe
  por «CABLE Output» si lo elegís como micrófono.
"""

from __future__ import annotations

from dataclasses import dataclass

from typing import Callable

import numpy as np

SAMPLE_RATE = 16000


def com_ready() -> None:
    """El audio de Windows (WASAPI) usa COM, que se prepara por hilo. soundcard lo prepara solo en el hilo que lo
    importa: si ese hilo termina, los demás fallaban con "Error 0x800401f0". Se prepara en cada hilo que lo usa."""
    import ctypes
    import warnings

    # Primero soundcard (al cargarse prepara COM en su hilo y falla si ya estaba preparado), después este hilo.
    warnings.filterwarnings("ignore", message="data discontinuity in recording")
    import soundcard  # noqa: F401

    ctypes.windll.ole32.CoInitializeEx(None, 0)  # COINIT_MULTITHREADED; si ya estaba, no hace nada


def _sc():
    import warnings

    com_ready()
    import soundcard

    # Aviso de soundcard al empezar a grabar (y si el búfer se atrasa): llenaba el registro de errores.
    warnings.filterwarnings("ignore", message="data discontinuity in recording")
    return soundcard


def speaker_loopback():
    """Lo que suena en tus parlantes o auriculares (el juego)."""
    sc = _sc()
    return sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)


class _WithFallback:
    """Una fuente que prueba primero `primary` y, si Windows no la deja abrir, usa la de `fallback()`."""

    def __init__(self, primary, fallback) -> None:
        self.primary, self.fallback = primary, fallback
        self._active = None

    def recorder(self, **options):
        self._options = options
        return self

    def __enter__(self):
        import logging

        try:
            self._active = self.primary.recorder(**self._options).__enter__()
        except OSError as exc:
            logging.getLogger(__name__).info("No se pudo escuchar solo a Roblox (%s): se escucha toda la PC", exc)
            self._active = self.fallback().recorder(**self._options).__enter__()
        return self._active

    def __exit__(self, *exc):
        return self._active.__exit__(*exc) if self._active is not None else False


def game_audio():
    """Lo que suena en Roblox, y nada más: ni Discord, ni YouTube, ni música (Windows 11). Si Roblox todavía no abrió,
    silencio hasta que abra. Solo en un Windows que no permite escuchar un programa suelto se escucha toda la PC."""
    from .process_audio import RobloxAudio

    return _WithFallback(RobloxAudio(), speaker_loopback)


def microphone():
    return _sc().default_microphone()


def virtual_cable():
    """La entrada del micrófono virtual, si está instalado: la misma que usa el puente con tu micrófono (bridge.py).
    VB-Cable también instala «CABLE In 16ch»: reproducir ahí fallaba (0x8889000A) y la voz traducida nunca llegaba a
    Roblox."""
    from .bridge import cable_input

    return cable_input()


def default_speaker():
    return _sc().default_speaker()


def to_mono(block: np.ndarray) -> np.ndarray:
    block = np.asarray(block, dtype=np.float32)
    return block.mean(axis=1) if block.ndim == 2 else block


def resample(audio: np.ndarray, rate: int, target: int = SAMPLE_RATE) -> np.ndarray:
    if rate == target or not len(audio):
        return audio.astype(np.float32)
    count = int(len(audio) * target / rate)
    return np.interp(np.linspace(0, len(audio) - 1, count), np.arange(len(audio)), audio).astype(np.float32)


@dataclass
class Output:
    """Dónde sale tu voz traducida."""

    device: object
    is_cable: bool

    @property
    def name(self) -> str:
        return getattr(self.device, "name", "?")


def voice_output(prefer_cable: bool = True) -> Output:
    cable = virtual_cable() if prefer_cable else None
    return Output(cable, True) if cable is not None else Output(default_speaker(), False)


TAIL_S = 0.5


def play(output: Output, audio: np.ndarray, rate: int) -> None:
    """Reproduce (bloquea hasta terminar). Termina con medio segundo de silencio: el reproductor se cierra apenas
    recibe el último pedazo, y el final de la frase se cortaba."""
    com_ready()
    audio = np.clip(audio, -1, 1).astype(np.float32)
    tail = np.zeros((int(rate * TAIL_S), *audio.shape[1:]), dtype=np.float32)
    output.device.play(np.concatenate([audio, tail]), samplerate=rate)


STREAM_BUFFER_S = 0.25  # colchón del reproductor (el de Windows por defecto es ~10 ms: con eso la voz se cortaba)
STREAM_START_S = 0.35  # se empieza a reproducir con esto listo


def play_stream(output: Output, pieces, rate: int, on_piece: Callable[[float], None] | None = None) -> float:
    """Reproduce pedazos a medida que llegan (bloquea hasta terminar). Devuelve los segundos que sonó.

    Los pedazos se traen en otro hilo, así ir a buscar el próximo (la red) nunca frena la reproducción, y el
    reproductor tiene un colchón de STREAM_BUFFER_S: una demora corta de la red no se nota. Antes el mismo hilo
    reproducía y esperaba la red con ~10 ms de colchón, y la voz sonaba entrecortada. `on_piece(segundos)`: antes de
    cada pedazo que se manda a sonar."""
    import queue
    import threading

    com_ready()
    incoming: queue.Queue = queue.Queue()

    def produce() -> None:
        try:
            for piece in pieces:
                incoming.put(np.asarray(piece, dtype=np.float32).ravel())
        except Exception:  # noqa: BLE001 - se corta la voz, no Bubble
            import logging

            logging.getLogger(__name__).debug("Se cortó la voz que llegaba", exc_info=True)
        finally:
            incoming.put(None)

    threading.Thread(target=produce, name="bubble-voz-llega", daemon=True).start()
    played = 0.0
    buffer: list[np.ndarray] = []
    buffered, done = 0, False
    while buffered < rate * STREAM_START_S:  # un poco listo antes de empezar
        piece = incoming.get()
        if piece is None:
            done = True
            break
        buffer.append(piece)
        buffered += len(piece)
    with output.device.player(samplerate=rate, channels=1, blocksize=int(rate * STREAM_BUFFER_S)) as player:
        while True:
            if buffer:
                chunk = np.clip(np.concatenate(buffer), -1, 1)
                buffer = []
                if on_piece:
                    on_piece(len(chunk) / rate)
                player.play(chunk)  # vuelve cuando queda ~STREAM_BUFFER_S por sonar
                played += len(chunk) / rate
            if done:
                break
            piece = incoming.get()
            if piece is None:
                done = True
                continue
            buffer.append(piece)
            while True:  # y todo lo que ya llegó, junto
                try:
                    extra = incoming.get_nowait()
                except queue.Empty:
                    break
                if extra is None:
                    done = True
                    break
                buffer.append(extra)
        player.play(np.zeros(int(rate * TAIL_S), dtype=np.float32))
    return played


def monitor_output() -> Output:
    """Tus parlantes o auriculares: para que escuches lo que dijo tu voz traducida."""
    return Output(default_speaker(), False)
