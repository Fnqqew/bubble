"""Laboratorio del chat, sin pantalla y sin Claude: el ciclo real de lectura de Bubble (captura → OCR → seguidor del
chat → píldoras) contra el simulador de Roblox dibujado en memoria, con traducciones falsas que llegan solas.

Como se sabe dónde está cada mensaje en cada momento, mide lo que ve el jugador:
- parpadeos (una traducción que se apaga y se vuelve a prender enseguida);
- píldoras fuera de lugar (tapando otro renglón);
- mensajes en otro idioma que nunca se taparon;
- basura (traducciones de cosas que no eran mensajes).

Uso:  python -m bubble.tools.chat_lab [medio_transparente rafagas_transparente medio …] [--segundos N]
"""

from __future__ import annotations

import argparse
import asyncio
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from PIL import Image, ImageFont

from ..capture import chat_watcher
from ..capture.chat_parser import ChatTracker
from ..capture.chat_watcher import ChatWatcher
from ..capture.ocr import WindowsOcr
from ..geometry import Rect
from ..translate.base import ChatLine
from ..ui import inline
from . import chat_simulator as sim

TRANSLATE_S = 1.2  # lo que "tarda" la traducción falsa
MY_LANGUAGE = "es"


class _NullLog:
    def write(self, _text):
        pass

    def flush(self):
        pass


class OfflineSimulator(sim.Simulator):
    """El simulador sin ventana: cada captura dibuja la escena en el momento actual."""

    def __init__(self, scenario: str) -> None:  # noqa: D107 - no llama al de la ventana
        self.always_faded = scenario.endswith("_transparente")
        self.events = sim.build_scenario(scenario.removesuffix("_transparente"))
        self.fade = True
        self.last_activity = time.time()
        self.log = _NullLog()
        self.font = ImageFont.truetype("arialbd.ttf", 15)
        self.bubble_font = ImageFont.truetype("arial.ttf", 16)
        self.messages: list[sim.Message] = []
        self.bubbles = {name: [] for name in sim.BUBBLE_PLAYERS}
        self.typing = None
        self.start = time.time() + 1.0
        self.next_event = 0
        self.appeared: dict[int, float] = {}  # id del mensaje → cuándo apareció
        self._lock = threading.Lock()

    def capture(self) -> tuple[Image.Image, list[tuple[sim.Message, int, int]]]:
        """(recorte del chat, renglones visibles: (mensaje, número de renglón, arriba en el recorte))."""
        with self._lock:
            now = time.time()
            while self.next_event < len(self.events) and now - self.start >= self.events[self.next_event][0]:
                _t, speaker, text, lang, kind = self.events[self.next_event]
                message = sim.Message(speaker, text, lang, "system" if kind == "system" else kind)
                self._add(message)
                self.appeared[id(message)] = now
                self.next_event += 1
            ox, oy = self._camera(now)
            image = self._scene(ox, oy)
            self._draw_chat(image, self._panel_brightness(now))
            x0, y0, x1, y1 = sim.CHAT
            rows = [(message, index) for message in self.messages for index, _line in enumerate(message.lines)][-10:]
            top = y1 - len(rows) * sim.LINE_HEIGHT - y0
            placed = [(message, index, top + k * sim.LINE_HEIGHT) for k, (message, index) in enumerate(rows)]
            region = sim.CHAT_REGION
            crop = image.crop((region["x"], region["y"], region["x"] + region["w"], region["y"] + region["h"]))
            return crop, placed

    @property
    def finished(self) -> bool:
        return self.next_event >= len(self.events) and time.time() - self.start > self.events[-1][0] + 4


class FakeLayer:
    """En vez de ventanas, anota dónde está cada píldora y cuándo se prende y se apaga."""

    def __init__(self, lab: Lab) -> None:
        self.lab = lab
        self.shown: dict[object, tuple[int, int, int]] = {}

    def show(self, key, image, x, y) -> None:
        if key not in self.shown:
            self.lab.on(key)
        self.shown[key] = (x, y, image.height)

    def keep_only(self, keys) -> None:
        for key in [k for k in self.shown if k not in keys]:
            del self.shown[key]
            self.lab.off(key)

    def hide_all(self) -> None:
        self.keep_only(set())


@dataclass
class Lab:
    scenario: str
    flickers: int = 0
    misplaced: int = 0
    placed_checks: int = 0
    first_cover: dict[int, float] = field(default_factory=dict)  # id del mensaje → cuándo se tapó por primera vez
    garbage: list[str] = field(default_factory=list)
    misplaced_examples: list[str] = field(default_factory=list)
    detected: list[str] = field(default_factory=list)
    off_at: dict = field(default_factory=dict)
    lines: dict[int, ChatLine] = field(default_factory=dict)  # id de la traducción → mensaje leído

    def on(self, key) -> None:
        if key in self.off_at and time.time() - self.off_at.pop(key) < 1.5:
            self.flickers += 1

    def off(self, key) -> None:
        self.off_at[key] = time.time()


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def run(scenario: str) -> Lab:
    lab = Lab(scenario)
    simulator = OfflineSimulator(scenario)
    tracker = ChatTracker()
    view = _make_view(tracker, lab)  # las píldoras de verdad, pero sin ventanas
    truth_by_capture: dict[int, list] = {}
    region = Rect(0, 0, sim.CHAT_REGION["w"], sim.CHAT_REGION["h"])
    ids = iter(range(1, 1 << 30))
    loop = asyncio.new_event_loop()

    def grab(_region):
        image, placed = simulator.capture()
        truth_by_capture[id(image)] = placed
        return image

    def translate_later(msg_id: int, line: ChatLine) -> None:
        # Mensaje en tu idioma: no se tapa; si no, llega una traducción falsa.
        best = max(simulator.messages, key=lambda m: _similar(f"{m.speaker}: {m.text}", f"{line.speaker}: {line.text}"),
                   default=None)
        real = best if best and _similar(best.text, line.text) >= 0.6 else None
        if real is None:
            lab.garbage.append(f"{line.speaker}: {line.text}")
        text = None if real is not None and real.lang == MY_LANGUAGE else f"«{line.text.upper()}»"
        loop.call_later(TRANSLATE_S, lambda: view.final(msg_id, line, text))

    def on_message(line: ChatLine) -> None:
        msg_id = next(ids)
        lab.lines[msg_id] = line
        lab.detected.append(f"{line.speaker}: {line.text}")
        view.pending(msg_id, line)
        translate_later(msg_id, line)

    def on_frame(frame) -> None:
        view.render(frame, frame is not None)
        if frame is None:
            return
        placed = truth_by_capture.pop(id(frame.image), None)
        if placed is None:
            return
        _check(lab, view, frame, placed, simulator)

    chat_watcher.grab = grab
    watcher = ChatWatcher(WindowsOcr("es"), tracker, lambda: region, on_message, interval_s=0.06,
                          on_frame=on_frame, on_shift=view.shift)

    async def main():
        watcher.start()
        while not simulator.finished:
            await asyncio.sleep(0.2)
        watcher.stop()

    loop.run_until_complete(main())
    lab.simulator = simulator
    return lab


def _make_view(tracker: ChatTracker, lab: Lab) -> inline.InlineChatView:
    original = inline.PatchLayer
    inline.PatchLayer = lambda root=None: FakeLayer(lab)  # noqa: E731
    try:
        return inline.InlineChatView(None, tracker.same_message)
    finally:
        inline.PatchLayer = original


def _check(lab: Lab, view, frame, placed, simulator: OfflineSimulator) -> None:
    """¿Cada píldora está sobre el renglón de su mensaje?"""
    layer: FakeLayer = view.layer
    now = time.time()
    for key, (_x, y, height) in layer.shown.items():
        entry_key, _identity, index = key
        entry = view.by_id.get(entry_key)
        if entry is None:
            continue
        # El renglón de ese mensaje más cercano a la píldora (el mismo texto puede estar dos veces en el chat).
        candidates = [(message, row, top) for message, row, top in placed
                      if row == index and _similar(message.text, entry.original) >= 0.6]
        if not candidates:
            continue
        center = y + height / 2
        message, _row, top = min(candidates, key=lambda c: abs(center - (c[2] + sim.LINE_HEIGHT / 2)))
        lab.placed_checks += 1
        off_by = center - (top + sim.LINE_HEIGHT / 2)
        if abs(off_by) > sim.LINE_HEIGHT * 0.45:
            lab.misplaced += 1
            if len(lab.misplaced_examples) < 8:
                lab.misplaced_examples.append(f"{message.speaker}: {message.text[:30]} (renglón {index}) corrida "
                                              f"{off_by:+.0f}px")
        lab.first_cover.setdefault(id(message), now)


def report(lab: Lab) -> list[str]:
    simulator = lab.simulator
    foreign = [m for m in simulator.messages if m.kind == "player" and m.lang != MY_LANGUAGE]
    covered = [m for m in foreign if id(m) in lab.first_cover]
    delays = sorted(lab.first_cover[id(m)] - simulator.appeared.get(id(m), lab.first_cover[id(m)]) - TRANSLATE_S
                    for m in covered if id(m) in simulator.appeared)
    p50 = delays[len(delays) // 2] if delays else None
    p95 = delays[int(len(delays) * 0.95)] if delays else None
    misplaced_share = lab.misplaced / max(1, lab.placed_checks)
    lines = [
        f"=== {lab.scenario}",
        f"  tapados {len(covered)}/{len(foreign)} mensajes en otro idioma · detección p50 "
        f"{p50 if p50 is None else round(p50, 2)}s p95 {p95 if p95 is None else round(p95, 2)}s",
        f"  parpadeos {lab.flickers} · fuera de lugar {misplaced_share:.1%} de las veces · basura {len(lab.garbage)}",
    ]
    for text in lab.garbage[:6]:
        lines.append(f"    · basura: {text}")
    for text in lab.misplaced_examples:
        lines.append(f"    · fuera de lugar: {text}")
    missing = [f"{m.speaker}: {m.text}" for m in foreign if id(m) not in lab.first_cover]
    for text in missing[:6]:
        lines.append(f"    · sin tapar: {text}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="Laboratorio del chat (sin pantalla ni Claude)")
    parser.add_argument("escenarios", nargs="*", default=["medio_transparente", "rafagas_transparente"])
    args = parser.parse_args()
    for scenario in args.escenarios:
        print("\n".join(report(run(scenario))), flush=True)


if __name__ == "__main__":
    main()
