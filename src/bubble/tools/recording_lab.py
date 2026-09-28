"""Laboratorio con grabaciones reales de Roblox: el mismo código que corre en el juego, contra un video.

El grabador de Roblox (Configuración → Grabar) guarda el juego SIN las traducciones de Bubble encima: es una entrada
limpia. Cada prueba reproduce el video a su velocidad real y le "muestra" a Bubble los cuadros como si fueran tu
pantalla (o el audio como si fuera tu parlante):

  chat      lectura del chat → seguidor → traducciones (falsas o de una tabla). Mide mensajes encontrados contra
            una lista de los reales (--esperados), repetidos o basura, traducciones fuera del chat y parpadeos.
  burbujas  detector de burbujas → lecturas → traducciones. Mide cuántas burbujas a la vista tienen traducción y
            cuántas traducciones quedan fuera de lugar, y anota cada lectura (para ver si se corta el texto).
  voz       oído en vivo (detector de voz, Whisper, quién habla). Con --referencia (una transcripción buena,
            JSON con "segments": [{start, end, text}]) mide palabras mal entendidas (WER) y frases perdidas.

Uso:
  python -m bubble.tools.recording_lab chat "C:\\...\\Videos\\Roblox\\Roblox-....mp4" [--esperados msgs.json]
  python -m bubble.tools.recording_lab burbujas VIDEO
  python -m bubble.tools.recording_lab voz VIDEO [--referencia ref.json]

Guarda cómo se vería (cada 2 s) en %LOCALAPPDATA%\\Bubble\\recording_lab\\. Las grabaciones tienen nombres de otros
jugadores: no se suben a ningún lado.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image

from ..geometry import Rect
from ..voice.models import models_dir

OUT = models_dir().parent / "recording_lab"


def ffmpeg() -> str:
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


class LiveVideo:
    """El video "en vivo": ffmpeg lo entrega a su velocidad real y queda solo el último cuadro (como la pantalla)."""

    def __init__(self, video: Path, crop: Rect | None = None, size: tuple[int, int] = (1920, 1040)) -> None:
        self.crop = crop
        self.w, self.h = (crop.width, crop.height) if crop else size
        vf = [f"crop={crop.width}:{crop.height}:{crop.left}:{crop.top}"] if crop else []
        cmd = [ffmpeg(), "-loglevel", "error", "-re", "-i", str(video), "-map", "0:0"]
        if vf:
            cmd += ["-vf", ",".join(vf)]
        self.proc = subprocess.Popen(cmd + ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        self.latest = np.zeros((self.h, self.w, 3), np.uint8)
        self.done = threading.Event()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        size = self.w * self.h * 3
        while True:
            raw = self.proc.stdout.read(size)
            if len(raw) < size:
                break
            self.latest = np.frombuffer(raw, np.uint8).reshape(self.h, self.w, 3)
        self.done.set()

    def image(self) -> Image.Image:
        return Image.fromarray(self.latest)


def frame_at(video: Path, seconds: float) -> Image.Image:
    raw = subprocess.run([ffmpeg(), "-loglevel", "error", "-ss", str(seconds), "-i", str(video), "-map", "0:0",
                          "-frames:v", "1", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True,
                         check=True).stdout
    import io

    return Image.open(io.BytesIO(raw)).convert("RGB")


class Layer:
    """En vez de ventanas: dónde está cada traducción, y los parpadeos (se apaga y vuelve enseguida)."""

    def __init__(self) -> None:
        self.shown: dict = {}
        self.off_at: dict = {}
        self.flickers = 0

    def show(self, key, image, x, y) -> None:
        if key not in self.shown and key in self.off_at and time.monotonic() - self.off_at.pop(key) < 1.5:
            self.flickers += 1
        self.shown[key] = (image, x, y)

    def keep_only(self, keys) -> None:
        for key in [k for k in self.shown if k not in keys]:
            del self.shown[key]
            self.off_at[key] = time.monotonic()

    def hide_all(self) -> None:
        self.keep_only(set())


def fake_translation(text: str) -> str:
    """Traducción falsa un 25 % más larga (como el español), para ver si entra."""
    words = text.split()
    return " ".join(words) + " " + " ".join(words[: max(1, len(words) // 4)])


def _translator(table: dict[str, str]):
    def translate(text: str) -> str:
        if not table:
            return fake_translation(text)
        best = max(table, key=lambda k: SequenceMatcher(None, k.lower(), text.lower()).ratio())
        return table[best] if SequenceMatcher(None, best.lower(), text.lower()).ratio() >= 0.6 else text
    return translate


# ---------------------------------------------------------------- chat
def run_chat(video: Path, expected: list[str], translations: dict[str, str], locate_at: float) -> dict:
    from ..capture import chat_watcher
    from ..capture.chat_locator import find_chat_region
    from ..capture.chat_parser import ChatTracker
    from ..capture.chat_watcher import ChatWatcher
    from ..capture.ocr import WindowsOcr, prepare_chat_image
    from ..ui import inline

    out = OUT / f"chat_{video.stem[-12:]}"
    out.mkdir(parents=True, exist_ok=True)
    ocr = WindowsOcr("es")
    loop = asyncio.new_event_loop()
    first = frame_at(video, locate_at)
    guess = find_chat_region(loop.run_until_complete(ocr.recognize(prepare_chat_image(first))), first.width,
                             first.height)
    if guess is None:
        raise SystemExit("No encontré el chat en ese momento del video: probá con --buscar-en otro segundo.")
    r = guess.region
    region = Rect(r.left // 2 * 2, r.top // 2 * 2, r.width // 2 * 2, r.height // 2 * 2)  # ffmpeg recorta en pares
    live = LiveVideo(video, region)
    layer = Layer()
    tracker = ChatTracker()
    original = inline.PatchLayer
    inline.PatchLayer = lambda root=None: layer
    view = inline.InlineChatView(None, tracker.same_message)
    inline.PatchLayer = original
    translate = _translator(translations)
    detected: list[str] = []
    ids = iter(range(1, 1 << 30))
    start = time.monotonic()
    snaps = {"next": 2.0, "outside": 0, "checks": 0}
    panel_right = region.width  # sin más datos: el borde de la zona

    def on_message(line) -> None:
        msg_id = next(ids)
        detected.append(f"{line.speaker}: {line.text}")
        view.pending(msg_id, line)
        loop.call_later(1.5, lambda: view.final(msg_id, line, translate(line.text)))

    def on_frame(frame) -> None:
        view.render(frame, frame is not None)
        if frame is None:
            return
        for image, x, _y in layer.shown.values():
            snaps["checks"] += 1
            snaps["outside"] += x + image.width > panel_right + 6
        t = time.monotonic() - start
        if t >= snaps["next"]:
            snaps["next"] += 2.0
            shot = frame.image.convert("RGBA")
            for image, x, y in layer.shown.values():
                shot.alpha_composite(image.convert("RGBA"), (int(x), int(y)))
            shot.convert("RGB").save(out / f"t{t:05.1f}.png")

    chat_watcher.grab = lambda _region: live.image()
    watcher = ChatWatcher(ocr, tracker, lambda: Rect(0, 0, region.width, region.height), on_message, interval_s=0.1,
                          on_frame=on_frame, on_shift=view.shift)

    async def run() -> None:
        watcher.start()
        while not live.done.is_set():
            await asyncio.sleep(0.2)
        watcher.stop()

    loop.run_until_complete(run())
    result = {"zona": [region.left, region.top, region.width, region.height], "detectados": detected,
              "parpadeos": layer.flickers, "fuera_de_la_zona": snaps["outside"], "revisadas": snaps["checks"]}
    if expected:
        left, extra = list(expected), []
        for line in detected:
            text = line.split(": ", 1)[-1].lower()
            if not any(c.isalnum() for c in text):
                continue  # "####": lo tapó el filtro de Roblox, no se traduce
            scores = [(SequenceMatcher(None, text, e.lower()).ratio(), i) for i, e in enumerate(left)]
            best = max(scores, default=(0, -1))
            if best[0] >= 0.75:
                left.pop(best[1])
            else:
                extra.append(line)
        result.update(reales=len(expected), encontrados=len(expected) - len(left), sobrantes=len(extra),
                      perdidos=left, ejemplos_sobrantes=extra[:8])
    return result


# ---------------------------------------------------------------- burbujas
def run_bubbles(video: Path, translations: dict[str, str]) -> dict:
    from ..capture import bubble_tracker
    from ..capture.bubble_tracker import BubbleWatcher, find_bubble_boxes
    from ..capture.ocr import WindowsOcr
    from ..config import RobloxConfig
    from ..performance import MIN_INTERVAL_FACTOR, SHARES, Pacer
    from ..ui import inline

    out = OUT / f"burbujas_{video.stem[-12:]}"
    out.mkdir(parents=True, exist_ok=True)
    live = LiveVideo(video)
    view = inline.BubbleView(None, lambda a, b: a.text == b.text)
    view.layer = Layer()
    translate = _translator(translations)
    ready: dict[str, str] = {}
    reads: list[str] = []
    loop = asyncio.new_event_loop()
    start = time.monotonic()
    stats = {"next": 1.5, "boxes": 0, "covered": 0, "pills": 0, "misplaced": 0}
    area = Rect(0, 0, live.w, live.h)

    def on_bubbles(game, items) -> None:
        for item in items:
            entry, is_new = view.entry_for(item.text)
            if is_new:
                reads.append(f"{time.monotonic() - start:5.1f}s {item.text}")

                def done(entry=entry) -> None:
                    entry.status, entry.text = "done", translate(entry.original)
                    ready[entry.original] = entry.text
                loop.call_later(1.5, done)
        view.render(game, items, game is not None, lambda entry: ready.get(entry.original, ""))

    bubble_tracker.grab = lambda _area: live.image()
    base = RobloxConfig().bubble_interval_s
    pacer = Pacer(SHARES["alta"]["bubbles"], base * MIN_INTERVAL_FACTOR["alta"], 1.0)
    watcher = BubbleWatcher(WindowsOcr("en"), lambda: area, lambda: None, on_bubbles, interval_s=base, pacer=pacer)
    # Cuánto tarda cada burbuja desde que se la ve por primera vez hasta que se lee su texto (y si se leyó a medias).
    seen: dict[int, float] = {}
    read: dict[int, float] = {}
    texts: dict[int, list[str]] = {}
    update, read_text = watcher.tracker.update, watcher._read_text

    def timed_update(boxes, captured_at):
        tracks = update(boxes, captured_at)
        for track in tracks:
            seen.setdefault(track.id, time.monotonic())
        return tracks

    async def timed_read(track, image):
        await read_text(track, image)
        if track.text and (not texts.get(track.id) or texts[track.id][-1] != track.text):
            texts.setdefault(track.id, []).append(track.text)
            read.setdefault(track.id, time.monotonic())

    watcher.tracker.update, watcher._read_text = timed_update, timed_read

    async def animate() -> None:
        while True:
            view.animate()
            t = time.monotonic() - start
            if t >= stats["next"]:
                stats["next"] += 1.5
                frame = live.image()
                truth = [b for b in find_bubble_boxes(frame) if b.bottom < live.h - 40]
                shot = frame.convert("RGBA")
                shown = list(view.layer.shown.values())
                for image, x, y in shown:
                    shot.alpha_composite(image.convert("RGBA"), (int(x), int(y)))
                    stats["pills"] += 1
                    overlap = max((max(0, min(x + image.width, b.right) - max(x, b.left)) *
                                   max(0, min(y + image.height, b.bottom) - max(y, b.top)) for b in truth), default=0)
                    stats["misplaced"] += overlap < 0.5 * image.width * image.height
                for box in truth:
                    stats["boxes"] += 1
                    stats["covered"] += any(x < box.right and box.left < x + im.width and y < box.bottom
                                            and box.top < y + im.height for im, x, y in shown)
                shot.convert("RGB").save(out / f"t{t:05.1f}.png")
            await asyncio.sleep(0.015)

    async def run() -> None:
        watcher.start()
        task = loop.create_task(animate())
        while not live.done.is_set():
            await asyncio.sleep(0.2)
        watcher.stop()
        task.cancel()

    loop.run_until_complete(run())
    delays = sorted(read[i] - seen[i] for i in read if i in seen)

    def percentile(p: float) -> float | None:
        return round(delays[min(len(delays) - 1, int(p * len(delays)))], 2) if delays else None

    return {"burbujas_con_traduccion": f"{stats['covered']}/{stats['boxes']}",
            "fuera_de_lugar": f"{stats['misplaced']}/{stats['pills']}",
            "hasta_leer_p50": percentile(0.5), "hasta_leer_p90": percentile(0.9),
            "leidas_a_medias": sum(len(v) > 1 for v in texts.values()), "burbujas_leidas": len(texts),
            "lecturas": reads}


# ---------------------------------------------------------------- voz
def _words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).replace("'", "").split()


def run_voice(video: Path, reference: list[dict]) -> dict:
    from ..tools.voice_lab import FakeSource, wer
    from ..voice.asr import FastWhisper, pick_models
    from ..voice.live import LiveListener
    from ..voice.speakers import SpeakerTracker

    raw = subprocess.run([ffmpeg(), "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-f",
                          "f32le", "-"], capture_output=True, check=True).stdout
    track = np.frombuffer(raw, np.float32).copy()
    partial_name, final_name = pick_models()
    final = FastWhisper(final_name)
    partial = FastWhisper(partial_name, 2) if partial_name else None
    source = FakeSource(track)
    finals: dict[int, tuple] = {}

    def on_caption(caption) -> None:
        if caption.final:
            finals[caption.id] = (caption, time.perf_counter())

    listener = LiveListener(final, on_caption, partial_asr=partial, speakers=SpeakerTracker(),
                            source_factory=lambda: source)
    cpu0, wall0 = time.process_time(), time.perf_counter()
    listener.start()
    time.sleep(len(track) / 16000 + 4)
    listener.stop()
    cpu = (time.process_time() - cpu0) / (time.perf_counter() - wall0)
    got = sorted((c for c, _ in finals.values() if c.text), key=lambda c: c.start)
    delays = [now - source.wall(c.end) for c, now in finals.values() if c.text]
    result = {"frases": [f"{c.start:5.1f}-{c.end:5.1f} [{c.language}] voz {c.speaker}: {c.text}" for c in got],
              "final_tras_terminar_p50_s": round(float(np.percentile(delays, 50)), 2) if delays else None,
              "cpu_nucleos": round(cpu, 2)}
    if reference:
        hypothesis = " ".join(_words(" ".join(c.text for c in got)))
        truth = " ".join(_words(" ".join(s["text"] for s in reference)))
        covered = sum(any(min(c.end, s["end"]) - max(c.start, s["start"]) > 0.3 for c in got) for s in reference)
        result.update(palabras_mal=round(wer(truth, hypothesis), 3), frases_cubiertas=f"{covered}/{len(reference)}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Bubble contra grabaciones reales de Roblox")
    parser.add_argument("prueba", choices=["chat", "burbujas", "voz"])
    parser.add_argument("video", type=Path)
    parser.add_argument("--esperados", type=Path, help="chat: JSON con la lista de mensajes reales, en orden")
    parser.add_argument("--traducciones", type=Path, help="JSON {original: traducción} para ver traducciones reales")
    parser.add_argument("--referencia", type=Path, help="voz: JSON con segments [{start, end, text}]")
    parser.add_argument("--buscar-en", type=float, default=20.0, help="chat: segundo del video donde buscar el chat")
    args = parser.parse_args()
    load = lambda path, default: json.loads(path.read_text(encoding="utf-8")) if path else default  # noqa: E731
    OUT.mkdir(parents=True, exist_ok=True)
    if args.prueba == "chat":
        result = run_chat(args.video, load(args.esperados, []), load(args.traducciones, {}), args.buscar_en)
    elif args.prueba == "burbujas":
        result = run_bubbles(args.video, load(args.traducciones, {}))
    else:
        reference = load(args.referencia, {})
        result = run_voice(args.video, reference.get("segments", reference) if isinstance(reference, dict) else reference)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    (OUT / f"{args.prueba}_{args.video.stem[-12:]}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                                     encoding="utf-8")


if __name__ == "__main__":
    main()
