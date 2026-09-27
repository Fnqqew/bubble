"""Entrada y salida de audio (WASAPI, vía soundcard).

- Lo que suena en la PC (el juego, incluido el chat de voz): captura "loopback" del parlante.
- Tu micrófono.
- La salida hacia el micrófono virtual (VB-Audio Virtual Cable): lo que se reproduce en «CABLE Input» Roblox lo recibe
  por «CABLE Output» si lo elegís como micrófono.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 16000
CABLE_NAMES = ("cable input", "vb-audio virtual cable", "voicemeeter input")


def _sc():
    import soundcard

    return soundcard


def speaker_loopback():
    """Lo que suena en tus parlantes o auriculares (el juego)."""
    sc = _sc()
    return sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True)


def microphone():
    return _sc().default_microphone()


def virtual_cable():
    """La entrada del micrófono virtual, si está instalado (VB-Audio Virtual Cable u otro parecido)."""
    for speaker in _sc().all_speakers():
        if any(name in speaker.name.lower() for name in CABLE_NAMES):
            return speaker
    return None


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


def play(output: Output, audio: np.ndarray, rate: int) -> None:
    """Reproduce (bloquea hasta terminar)."""
    output.device.play(np.clip(audio, -1, 1).astype(np.float32), samplerate=rate)
