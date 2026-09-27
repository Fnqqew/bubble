"""El botón del micrófono de Roblox (muteado o no) y la escucha de solo el sonido de Roblox."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from bubble.roblox_mic import find_mic
from bubble.voice import audio as audio_io

FIXTURES = Path(__file__).parent / "fixtures"


def window_with(bar: str) -> Image.Image:
    """La barra de arriba de una grabación real, en una ventana de 1920x1040."""
    window = Image.new("RGB", (1920, 1040), (40, 40, 40))
    window.paste(Image.open(FIXTURES / bar).convert("RGB"), (0, 0))
    return window


def test_muted_mic_is_found():
    state = find_mic(window_with("roblox_bar_mic_off.png"))
    assert state is not None and state.muted
    assert 200 <= state.x <= 250 and 20 <= state.y <= 60  # el botón está después del chat


def test_unmuted_mic_is_found_in_the_same_place():
    state = find_mic(window_with("roblox_bar_mic_on.png"))
    assert state is not None and not state.muted
    assert 200 <= state.x <= 250 and 15 <= state.y <= 60


def test_no_mic_button_without_voice_chat():
    assert find_mic(Image.new("RGB", (1920, 1040), (40, 40, 40))) is None


def test_red_text_in_the_bar_is_not_the_mic():
    window = Image.new("RGB", (1920, 1040), (40, 40, 40))
    window.paste((230, 60, 60), (300, 30, 420, 38))  # una franja roja horizontal (una bandera, un texto)
    assert find_mic(window) is None


class _Broken:
    def recorder(self, **_options):
        return self

    def __enter__(self):
        raise OSError("Windows no deja escuchar solo a un proceso")

    def __exit__(self, *_exc):
        return False


class _Speaker:
    entered = False

    def recorder(self, **_options):
        return self

    def __enter__(self):
        _Speaker.entered = True
        return self

    def __exit__(self, *_exc):
        return False


def test_game_audio_falls_back_to_the_whole_pc():
    source = audio_io._WithFallback(_Broken(), _Speaker)
    with source.recorder(samplerate=16000, channels=1) as active:
        assert isinstance(active, _Speaker) and _Speaker.entered


def test_process_loopback_reads_in_real_time():
    """Escucha solo a este mismo proceso (que no suena): silencio, al ritmo real."""
    import os
    import time

    from bubble.voice.process_audio import ProcessLoopback

    source = ProcessLoopback(os.getpid())
    try:
        with source.recorder(samplerate=16000, channels=1) as recorder:
            started = time.perf_counter()
            audio = np.concatenate([recorder.record(1600) for _ in range(3)])
            took = time.perf_counter() - started
    except OSError as exc:
        pytest.skip(f"Windows sin captura por proceso: {exc}")
    assert len(audio) == 4800 and not np.abs(audio).any()
    assert 0.2 <= took <= 0.6
