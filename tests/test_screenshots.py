"""Las traducciones aparecen en las capturas de pantalla del jugador, y el OCR no lee mientras tanto."""

import asyncio
import threading
import time

from PIL import Image

from bubble import layered, screenshots
from bubble.capture import chat_watcher, screen
from bubble.capture.chat_parser import ChatTracker
from bubble.geometry import Rect
from bubble.screenshots import SHOT_HOLD_S, VK_LWIN, VK_S, VK_SNAPSHOT, WIN_HOLD_S, ScreenshotKeys, hold_for


def test_screenshot_keys():
    assert hold_for(VK_SNAPSHOT, True, win=False, shift=False) == SHOT_HOLD_S  # Impr Pant (Herramienta Recortes)
    # Win: debe estar visible antes de la otra tecla
    assert hold_for(VK_LWIN, True, win=True, shift=False) == WIN_HOLD_S
    assert hold_for(VK_S, True, win=True, shift=True) == SHOT_HOLD_S  # Win + Shift + S
    assert hold_for(VK_S, True, win=False, shift=False) == 0  # retroceder en el juego
    assert hold_for(VK_S, True, win=False, shift=True) == 0
    assert hold_for(VK_SNAPSHOT, False, win=False, shift=False) == 0  # soltar la tecla no cuenta


def test_translations_become_capturable_from_another_thread():
    window = layered.LayeredWindow()  # oculta: no aparece en pantalla
    try:
        assert not layered.capturable(window)
        worker = threading.Thread(target=layered.set_capturable, args=(True,))
        worker.start()
        worker.join()
        assert layered.capturable(window)
        late = layered.LayeredWindow()  # una traducción nueva durante la captura también aparece
        assert layered.capturable(late)
        late.destroy()
        layered.set_capturable(False)
        assert not layered.capturable(window)
    finally:
        layered.set_capturable(False)
        window.destroy()


def test_reading_pauses_while_capturable():
    keys = ScreenshotKeys(active=lambda: True)
    window = layered.LayeredWindow()
    try:
        before = screen.reading_mark()
        assert before is not None
        keys._show(True)
        assert keys.showing and layered.capturable(window)
        assert screen.reading_mark() is None  # el OCR no lee: captaría las traducciones
        assert not screen.still_readable(before)  # una captura iniciada antes tampoco es válida
        keys._show(False)
        assert not keys.showing and not layered.capturable(window)
        after = screen.reading_mark()
        assert after is not None and after != before and not screen.still_readable(before)
    finally:
        keys._show(False)
        window.destroy()


def test_keys_hold_and_release():
    keys = ScreenshotKeys(active=lambda: True)
    try:
        keys._until = time.monotonic() + 0.15
        keys._update()
        assert keys.showing
        time.sleep(0.2)
        keys._update()
        assert not keys.showing and screen.reading_mark() is not None
    finally:
        keys._show(False)


def test_chat_watcher_does_not_read_while_capturable(monkeypatch):
    grabs = []

    def fake_grab(region):
        grabs.append(time.monotonic())
        return Image.new("RGB", (region.width, region.height)), b""

    monkeypatch.setattr(chat_watcher, "_grab_fingerprint", fake_grab)
    watcher = chat_watcher.ChatWatcher(None, ChatTracker(), lambda: Rect(0, 0, 200, 100), lambda _line: None,
                                       interval_s=0.02)

    async def run() -> None:
        watcher.start()
        await asyncio.sleep(0.25)
        watcher.stop()

    screen.pause_reading(True)
    try:
        asyncio.run(run())
    finally:
        screen.pause_reading(False)
    assert grabs == []


def test_listener_starts_and_stops():
    keys = ScreenshotKeys(active=lambda: False)  # solo inicia y detiene: no pulsa ninguna tecla
    keys.start()
    assert keys.error == ""
    keys.stop()
    keys.join(2)
    assert not keys.is_alive() and not keys.showing
    assert screenshots.layered is layered
