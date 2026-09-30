"""Botón del micrófono de Roblox (silenciado o no) y captura exclusiva del audio de Roblox."""

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from bubble.roblox_mic import find_mic
from bubble.voice import audio as audio_io

FIXTURES = Path(__file__).parent / "fixtures"


def window_with(bar: str) -> Image.Image:
    """Barra superior de una grabación real, en una ventana de 1920x1040."""
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
    window.paste((230, 60, 60), (300, 30, 420, 38))  # franja roja horizontal (una bandera, un texto)
    assert find_mic(window) is None


def test_warns_if_muted_but_never_clicks(monkeypatch):
    from bubble import roblox_mic, win32
    from bubble.geometry import Rect

    bar = window_with("roblox_bar_mic_off.png")
    monkeypatch.setattr(win32, "find_roblox_window", lambda: 1)
    monkeypatch.setattr(win32, "roblox_is_foreground", lambda: True)
    monkeypatch.setattr(win32, "client_rect", lambda _hwnd: Rect(0, 0, 1920, 1040))
    grab = lambda rect: bar.crop((rect.left, rect.top, rect.right, rect.bottom))  # noqa: E731
    assert roblox_mic.roblox_muted(grab) is True
    bar = window_with("roblox_bar_mic_on.png")
    assert roblox_mic.roblox_muted(grab) is False
    assert not hasattr(roblox_mic, "_click") and not hasattr(roblox_mic, "RobloxMic")  # sin clics
    monkeypatch.setattr(win32, "roblox_is_foreground", lambda: False)
    assert roblox_mic.roblox_muted(grab) is None  # sin Roblox en primer plano no se analiza


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


def test_roblox_audio_follows_the_process_that_plays(monkeypatch):
    """Roblox cambió de proceso (se reabrió): Bubble pasa al nuevo en lugar de quedarse en silencio con el anterior."""
    import time

    from bubble.voice import process_audio

    opened = []

    class FakeLoopback:
        def __init__(self, pid):
            self.pid = pid

        def recorder(self, *_a, **_k):
            return self

        def start(self):
            opened.append(self.pid)

        def stop(self):
            pass

        def record(self, n):
            return np.full(n, self.pid, np.float32)

    monkeypatch.setattr(process_audio, "ProcessLoopback", FakeLoopback)
    pids = iter([100, 100, 200, 200])
    source = process_audio.RobloxAudio(find_pid=lambda: next(pids, 200))
    source.CHECK_S = 0.0
    with source.recorder(samplerate=16000) as rec:
        values = [rec.record(160)[0] for _ in range(3)]
    assert opened == [100, 200] and values[-1] == 200
    empty = process_audio.RobloxAudio(find_pid=lambda: 0)
    with empty.recorder(samplerate=16000) as rec:
        started = time.perf_counter()
        assert not rec.record(1600).any()  # Roblox cerrado: silencio a ritmo real
        assert time.perf_counter() - started >= 0.09


def test_process_loopback_reads_in_real_time():
    """Captura solo este mismo proceso (que no emite sonido): silencio, a ritmo real."""
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


def test_translated_voice_goes_to_cable_input_not_the_16ch_one(monkeypatch):
    """VB-Cable instala «CABLE In 16ch» (primero en la lista) y «CABLE Input». Reproducir en el de 16 canales fallaba y
    la voz traducida nunca llegaba a Roblox.
    """
    from types import SimpleNamespace

    from bubble.voice import bridge

    speakers = [SimpleNamespace(name="CABLE In 16ch (VB-Audio Virtual Cable)"),
                SimpleNamespace(name="Speakers (Realtek(R) Audio)"),
                SimpleNamespace(name="CABLE Input (VB-Audio Virtual Cable)")]
    monkeypatch.setattr(bridge, "_sc", lambda: SimpleNamespace(all_speakers=lambda: speakers))
    assert bridge.cable_input().name == "CABLE Input (VB-Audio Virtual Cable)"
    assert audio_io.virtual_cable().name == "CABLE Input (VB-Audio Virtual Cable)"
