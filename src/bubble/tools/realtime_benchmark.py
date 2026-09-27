"""Prueba en tiempo real de Bubble contra el simulador de Roblox, con distintas velocidades de chat.

Para cada escenario abre el simulador y la app real (con traducciones de verdad por tu suscripción), registra
cuándo aparece cada mensaje, cuándo Bubble lo detecta, cuándo termina la traducción y qué muestra en las
burbujas, y al final arma un informe.

Uso:  python -m bubble.tools.realtime_benchmark [lento medio rapido rafagas]
"""

from __future__ import annotations

import ctypes
import json
import os
import statistics
import subprocess
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path

SCENARIOS = ["lento", "medio", "rapido", "rafagas"]
CHAT_REGION_REAL = (10, 64, 490, 236)  # la zona de mensajes del simulador (x, y, ancho, alto)


def _out_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "benchmark"
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- app instrumentada
SENDS = [  # (texto, esperar la vista previa antes de Enter)
    ("hola a todos, alguien quiere intercambiar mascotas?", True),
    ("jajaja sos re crack, me enseñás a pasar el obby?", False),
    ("ya vuelvo, voy a comer algo", True),
]


def run_app(log_path: Path, max_seconds: float, sends: bool = False, detect: bool = False) -> None:
    import bubble.roblox as rbx
    import bubble.shortcut as shortcut
    import bubble.win32 as w
    from bubble.capture.ocr import WindowsOcr
    from bubble.config import load_config
    from bubble.tools.chat_simulator import CHAT_REGION, TITLE

    w.enable_dpi_awareness()
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    fake: dict = {"hwnd": None, "seen": False}

    def find():
        hwnd = fake["hwnd"]
        if not hwnd or not user32.IsWindow(ctypes.c_void_p(hwnd)):
            hwnd = user32.FindWindowW(None, TITLE) or None
            fake["hwnd"] = hwnd
        return hwnd

    # El simulador no es RobloxPlayerBeta.exe: se lo presenta como si lo fuera.
    w.find_roblox_window = find
    w.roblox_is_foreground = lambda: bool(find()) and user32.GetForegroundWindow() == find()
    shortcut.ensure_desktop_shortcut = lambda force=False: False

    log = log_path.open("w", encoding="utf-8")
    if detect:
        # Sin chat calibrado: la app lo tiene que encontrar sola (en memoria: no se toca tu calibración real).
        memory: dict = {}

        def save_region(absolute, client):
            memory["region"] = {"relative": True, "x": absolute.left - client.left, "y": absolute.top - client.top,
                                "w": absolute.width, "h": absolute.height}
            write("chat_detectado", **memory["region"])

        rbx.load_chat_region = lambda: memory.get("region")
        rbx.save_chat_region = save_region
    else:
        rbx.load_chat_region = lambda: dict(CHAT_REGION)

    def write(event: str, **data) -> None:
        log.write(json.dumps({"t": time.time(), "event": event, **data}, ensure_ascii=False) + "\n")
        log.flush()

    ocr_stats = {"calls": 0, "seconds": 0.0}
    original_recognize = WindowsOcr.recognize

    async def counted_recognize(self, image, upscale=True):
        start = time.perf_counter()
        try:
            return await original_recognize(self, image, upscale)
        finally:
            ocr_stats["calls"] += 1
            ocr_stats["seconds"] += time.perf_counter() - start

    WindowsOcr.recognize = counted_recognize

    # Lo que el tracker ve en cada captura (solo cuando cambia), para poder explicar cada mensaje perdido.
    from bubble.capture.chat_parser import ChatTracker

    original_update = ChatTracker.update
    last_frame: list = [None]

    def logged_update(self, lines, uncertain=None):
        new = original_update(self, lines, uncertain)
        visible = [f"{line.speaker}: {line.text}" for line in lines]
        if visible != last_frame[0] or new:
            last_frame[0] = visible
            write("frame", lines=visible, new=[f"{line.speaker}: {line.text}" for line in new])
        return new

    ChatTracker.update = logged_update

    # Cuándo se prende y se apaga cada traducción en pantalla (para medir parpadeos).
    from bubble.ui import inline

    shown_keys: set = set()
    original_show, original_keep = inline.PatchLayer.show, inline.PatchLayer.keep_only

    def logged_show(self, key, image, x, y):
        if key not in shown_keys:
            shown_keys.add(key)
            write("patch", key=str(key), on=True, kind="bubble" if isinstance(key, int) else "chat")
        return original_show(self, key, image, x, y)

    def logged_keep(self, keys):
        for key in [k for k in list(self._patches) if k not in keys]:
            if key in shown_keys:
                shown_keys.discard(key)
                write("patch", key=str(key), on=False, kind="bubble" if isinstance(key, int) else "chat")
        return original_keep(self, keys)

    inline.PatchLayer.show, inline.PatchLayer.keep_only = logged_show, logged_keep

    import bubble.ui.main_window as mw

    mw.load_state = lambda: {"tutorial_seen": True}
    window_class = mw.BubbleWindow
    originals = {name: getattr(window_class, name) for name in
                 ("_on_chat_line", "_ev_chat_pending", "_ev_chat", "_ev_bubbles", "_ev_info", "_ev_started",
                  "_ev_chat_shift", "_ev_chat_frame", "_compose_send")}

    def compose_send(self, key):
        messages, *_rest = self._compose_results[key]
        write("enviando", text=key[0], target=key[1], messages=messages)
        originals["_compose_send"](self, key)

    def ev_chat_shift(self, dy):
        write("shift", dy=dy)
        originals["_ev_chat_shift"](self, dy)

    def ev_chat_frame(self, frame):
        originals["_ev_chat_frame"](self, frame)
        if frame is not None:
            write("render", lines=len(frame.items))

    def on_chat_line(self, line):
        write("detected", speaker=line.speaker, text=line.text)
        originals["_on_chat_line"](self, line)

    def ev_chat_pending(self, payload):
        write("pending", speaker=payload[1].speaker, text=payload[1].text)
        originals["_ev_chat_pending"](self, payload)

    def ev_chat(self, payload):
        _msg_id, line, result = payload
        write("final", speaker=line.speaker, text=line.text, status=result.status, translation=result.translation)
        originals["_ev_chat"](self, payload)

    shown_bubbles: dict = {}

    def ev_bubbles(self, payload):
        originals["_ev_bubbles"](self, payload)
        for item, entry in self.bubbles._pairs:
            translation = self._bubble_text(entry)
            if translation and shown_bubbles.get(entry.key) != (item.text, translation):
                shown_bubbles[entry.key] = (item.text, translation)
                write("bubble", ocr_text=item.text, translation=translation)

    def ev_info(self, message):
        write("info", message=message)
        originals["_ev_info"](self, message)

    def ev_started(self, error):
        write("ready", error=str(error) if error else None)
        originals["_ev_started"](self, error)

    for name, function in {"_on_chat_line": on_chat_line, "_ev_chat_pending": ev_chat_pending, "_ev_chat": ev_chat,
                           "_ev_bubbles": ev_bubbles, "_ev_info": ev_info, "_ev_started": ev_started,
                           "_ev_chat_shift": ev_chat_shift, "_ev_chat_frame": ev_chat_frame,
                           "_compose_send": compose_send}.items():
        setattr(window_class, name, function)

    config = load_config()
    config.roblox.display_mode = "inline"
    config.roblox.translate_bubbles = True
    config.roblox.read_chat = True
    config.roblox.hotkey = "ctrl+alt+F12"  # que no choque con tu Bubble abierto
    app = window_class(config)
    app.root.geometry("+1200+40")
    started = time.time()
    views = {"count": 0, "tick": 0}

    def save_player_view() -> None:
        """Lo que ve el jugador: el juego con las traducciones encima. Las traducciones no salen en capturas de
        pantalla (a propósito, para que el OCR no se lea a sí mismo), así que se dibujan sobre la captura."""
        from bubble.capture.screen import grab

        hwnd = find()
        if not hwnd or not fake.get("focused"):
            return
        area = w.client_rect(hwnd)
        image = grab(area).convert("RGBA")
        chat = app.inline_chat.last_frame
        patches = []
        if chat is not None:
            patches += [(p, chat.region.left + x, chat.region.top + y) for p, x, y in app.inline_chat.last_patches]
        patches += list(app.bubbles.last_patches)
        for patch, x, y in patches:
            if 0 <= x - area.left < area.width and 0 <= y - area.top < area.height:
                image.alpha_composite(patch.convert("RGBA"), (x - area.left, y - area.top))
        views["count"] += 1
        image.convert("RGB").save(log_path.with_name(f"{log_path.stem.replace('_app', '')}_vista_{views['count']:02d}.png"))

    plan = list(SENDS) if sends else []

    def do_send() -> None:
        """Como un jugador: abre la barra con el atajo, escribe y aprieta Enter."""
        if not plan or not app.ready:
            return
        text, wait_preview = plan.pop(0)
        write("barra", text=text)
        app._on_hotkey()
        app.compose.entry.delete(0, "end")
        app.compose.entry.insert(0, text)
        if wait_preview:
            app.compose._schedule_preview()  # como si hubiera escrito: la vista previa se pide sola
            app.root.after(3500, app.compose._submit)
        else:
            app.root.after(400, app.compose._submit)  # Enter enseguida: traduce y después manda

    def watchdog():
        views["tick"] += 1
        if plan and fake.get("focused") and views["tick"] % 40 == 20:  # uno cada ~12 s
            do_send()
        if views["tick"] % 10 == 0:
            try:
                save_player_view()
            except Exception as exc:  # noqa: BLE001 - es solo para revisar a ojo
                write("info", message=f"no se pudo guardar la vista: {exc}")
        hwnd = find()
        if hwnd and not fake["seen"]:
            fake["seen"] = True
            w.force_foreground(hwnd)
        elif hwnd:
            # Bubble solo lee con "Roblox" al frente: si usás otra ventana durante la prueba, se anota.
            focused = bool(user32.GetForegroundWindow() == hwnd)
            if focused != fake.get("focused"):
                fake["focused"] = focused
                write("focus", active=focused)
        elif fake["seen"] and not hwnd or time.time() - started > max_seconds:
            provider = app.translator.router.providers[0]
            usage = getattr(provider, "usage", None)
            chat_pacer = app.watcher.pacer if app.watcher else None
            bubble_pacer = app.bubble_watcher.pacer if app.bubble_watcher else None
            write("usage", claude_calls=getattr(usage, "requests", 0), cost_usd=getattr(usage, "cost_usd", 0.0),
                  ocr_calls=ocr_stats["calls"], ocr_seconds=ocr_stats["seconds"],
                  cpu_seconds=time.process_time(), wall_seconds=time.time() - started,
                  ui_cpu_seconds=time.thread_time(),  # el watchdog corre en el hilo de la interfaz
                  chat_cost_ms=1000 * chat_pacer.cost if chat_pacer else None,
                  chat_sleep_ms=1000 * chat_pacer.sleep if chat_pacer else None,
                  bubble_cost_ms=1000 * bubble_pacer.cost if bubble_pacer else None,
                  bubble_sleep_ms=1000 * bubble_pacer.sleep if bubble_pacer else None)
            app._on_close()
            return
        app.root.after(300, watchdog)

    app.root.after(300, watchdog)
    app.run()


# ---------------------------------------------------------------- análisis
def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _key(text: str) -> str:
    return " ".join("".join(c if c.isalnum() else " " for c in text.casefold()).split())


def _similar(a: str, b: str, threshold: float = 0.75) -> bool:
    ka, kb = _key(a), _key(b)
    return ka == kb or SequenceMatcher(None, ka, kb).ratio() >= threshold


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else float("nan")


def analyze(sim_log: Path, app_log: Path, my_lang: str = "es") -> dict:
    sim, app = _read(sim_log), _read(app_log)
    messages = [e for e in sim if e["event"] == "message"]
    detected = [e for e in app if e["event"] == "detected"]
    finals = [e for e in app if e["event"] == "final"]
    pendings = [e for e in app if e["event"] == "pending"]
    bubbles = [e for e in app if e["event"] == "bubble"]
    usage = next((e for e in app if e["event"] == "usage"), {})

    report: dict = {"messages": len(messages)}
    # Momentos en que la ventana del simulador no estaba al frente (Bubble no lee el chat a propósito).
    unfocused: list[tuple[float, float]] = []
    since = None
    for event in (e for e in app if e["event"] == "focus"):
        if not event["active"] and since is None:
            since = event["t"]
        elif event["active"] and since is not None:
            unfocused.append((since, event["t"]))
            since = None
    if since is not None:
        unfocused.append((since, float("inf")))

    def while_unfocused(t: float) -> bool:
        return any(start - 0.5 <= t <= end for start, end in unfocused)

    players = [m for m in messages if m["kind"] in ("player", "spam")]
    matched_detections: set[int] = set()
    detection_delays, translation_delays, pending_delays = [], [], []
    missed, missed_unfocused, duplicates, native_sent, foreign_not_translated = [], [], 0, [], []
    for message in players:
        hits = [
            i for i, d in enumerate(detected)
            if d["t"] >= message["t"] - 0.2 and _similar(d["text"], message["text"])
            and SequenceMatcher(None, _key(d["speaker"]), _key(message["speaker"])).ratio() >= 0.6
            and i not in matched_detections
        ]
        if not hits:
            if message["kind"] == "player":
                label = f'{message["speaker"]}: {message["text"]}'
                (missed_unfocused if while_unfocused(message["t"]) else missed).append(label)
            continue
        first = hits[0]
        matched_detections.add(first)
        detection_delays.append(detected[first]["t"] - message["t"])
        text = detected[first]["text"]
        final = next((f for f in finals if f["t"] >= detected[first]["t"] and _similar(f["text"], text)), None)
        pending = next((p for p in pendings if p["t"] >= detected[first]["t"] and _similar(p["text"], text)), None)
        if pending:
            pending_delays.append(pending["t"] - message["t"])
        if final and final["status"] in ("translated", "adapted", "cache"):
            translation_delays.append(final["t"] - message["t"])
            if message["lang"] == my_lang and final["status"] != "adapted":
                native_sent.append(f'{message["text"]} -> {final["translation"]}')
        elif message["lang"] != my_lang and message["kind"] == "player" and final:
            if final["status"] not in ("universal", "local"):
                foreign_not_translated.append(f'{message["text"]} [{final["status"]}]')
    # Detecciones repetidas del mismo mensaje (sin contar el spam, que repite a propósito).
    for message in players:
        if message["kind"] == "spam":
            continue
        count = sum(1 for d in detected if d["t"] >= message["t"] - 0.2 and _similar(d["text"], message["text"])
                    and _key(d["speaker"])[:4] == _key(message["speaker"])[:4])
        same_text_sent = sum(1 for m in players if _similar(m["text"], message["text"]) and m["speaker"] == message["speaker"])
        duplicates += max(0, count - same_text_sent)
    initial = [e for e in sim if e["event"] == "initial"]  # ya estaban en el chat al entrar
    garbage = [d for i, d in enumerate(detected) if i not in matched_detections
               and not any(_similar(d["text"], m["text"]) for m in messages + initial)]
    system_as_player = [d for d in detected if any(_similar(d["text"], m["text"]) for m in messages if m["kind"] == "system")]

    # Burbujas: lo que se lee tiene que ser una burbuja que se ve en ese momento, y la traducción mostrada no
    # puede ser la de otro mensaje (la traducción vieja que quedaba cuando el jugador escribía de nuevo).
    translations: dict[str, set[str]] = {}
    for f in finals:
        translations.setdefault(_key(f["text"]), set()).add(_key(f["translation"]))
    stale = []
    for shown in bubbles:
        visible = [m for m in messages if m.get("bubble") and m["t"] <= shown["t"] <= m["t"] + 12]
        source = max(visible, key=lambda m: SequenceMatcher(None, _key(m["text"]), _key(shown["ocr_text"])).ratio(),
                     default=None)
        if source is None or not _similar(shown["ocr_text"], source["text"], 0.6):
            stale.append(f'{shown["ocr_text"]} -> {shown["translation"]} (no es una burbuja visible)')
            continue
        shown_key = _key(shown["translation"])
        own = translations.get(_key(source["text"]), set())
        others = {t for text, ts in translations.items() if not _similar(text, source["text"]) for t in ts}
        if shown_key not in own and shown_key in others:
            stale.append(f'{shown["ocr_text"]} -> {shown["translation"]} (traducción de otro mensaje)')

    # Parpadeos: una traducción que se apaga y se vuelve a prender enseguida (el mensaje seguía ahí).
    flickers = {"chat": 0, "bubble": 0}
    off_at: dict[str, float] = {}
    for event in (e for e in app if e["event"] == "patch"):
        if not event["on"]:
            off_at[event["key"]] = event["t"]
        elif event["key"] in off_at and event["t"] - off_at.pop(event["key"]) < 1.5:
            flickers[event["kind"]] += 1

    # Reacomodo: cuánto tardan las traducciones en moverse con el chat cuando llega un mensaje (mientras tanto quedan
    # corridas una línea). Se mueven con el desplazamiento instantáneo ("shift") o, si no, con la lectura siguiente.
    shifts = [e["t"] for e in app if e["event"] == "shift"]
    renders = [e["t"] for e in app if e["event"] == "render"]
    realign, by_shift = [], 0
    for message in messages:
        after_shift = next((t for t in shifts if t >= message["t"]), None)
        after_render = next((t for t in renders if t >= message["t"] + 0.02), None)
        options = [t for t in (after_shift, after_render) if t is not None and t - message["t"] < 2]
        if options:
            realign.append(min(options) - message["t"])
            by_shift += after_shift is not None and min(options) == after_shift

    # Envío con la barra: ¿llegó cada mensaje traducido al chat, sin tocar ninguna otra tecla del juego?
    bars = [e for e in app if e["event"] == "barra"]
    sending = [e for e in app if e["event"] == "enviando"]
    arrived = [e for e in sim if e["event"] == "enviado"]
    expected = [m for e in sending for m in e["messages"]]
    report["envios"] = {
        "barras": len(bars),
        "mandados": len(sending),
        "llegaron": sum(1 for text in expected if any(_similar(text, a["text"], 0.9) for a in arrived)),
        "esperados": len(expected),
        "chat_abierto": sum(1 for e in sim if e["event"] == "abrir_chat"),
        "teclas_al_juego": [e["keysym"] for e in sim if e["event"] == "tecla_juego"],
        "teclas_extra": [e["keysym"] for e in sim if e["event"] == "tecla_extra"],
        "propios_traducidos": [d["text"] for d in detected if any(_similar(d["text"], a["text"]) for a in arrived)],
        "detalle": [f'{e["text"]}  →  {" / ".join(e["messages"])}' for e in sending],
    } if bars else None

    found = next((e for e in app if e["event"] == "chat_detectado"), None)
    start = next((e["t"] for e in sim if e["event"] == "start"), None)
    report["chat_encontrado"] = (
        {"segundos": found["t"] - start if start else None,
         "zona": (found["x"], found["y"], found["w"], found["h"])} if found else ("no" if any(
             e["event"] == "ready" for e in app) and "detectar" in str(app_log) else None))

    report.update({
        "reacomodo_p50": _pct(realign, 0.5), "reacomodo_p95": _pct(realign, 0.95),
        "reacomodo_por_desplazamiento": by_shift,
        "parpadeos_chat": flickers["chat"],
        "parpadeos_burbujas": flickers["bubble"],
        "jugadores": len(players),
        "detectados": len(detection_delays),
        "perdidos": missed,
        "perdidos_sin_foco": missed_unfocused,
        "segundos_sin_foco": sum(min(end, messages[-1]["t"] if messages else end) - start for start, end in unfocused),
        "repetidos": duplicates,
        "basura": [f'{d["speaker"]}: {d["text"]}' for d in garbage],
        "sistema_tomado_como_jugador": len(system_as_player),
        "en_tu_idioma_enviados_a_claude": native_sent,
        "en_otro_idioma_sin_traducir": foreign_not_translated,
        "deteccion_p50": _pct(detection_delays, 0.5), "deteccion_p95": _pct(detection_delays, 0.95),
        "lugar_reservado_p50": _pct(pending_delays, 0.5),
        "traduccion_p50": _pct(translation_delays, 0.5), "traduccion_p95": _pct(translation_delays, 0.95),
        "traduccion_max": max(translation_delays) if translation_delays else float("nan"),
        "traducidos": len(translation_delays),
        "burbujas_mostradas": len(bubbles), "burbujas_desactualizadas": stale,
        "spam_avisos": sum(1 for e in app if e["event"] == "info" and "Spam" in e.get("message", "")),
        "llamadas_claude": usage.get("claude_calls"), "costo_usd": usage.get("cost_usd"),
        "ocr_llamadas": usage.get("ocr_calls"),
        "cpu_pct": 100 * usage.get("cpu_seconds", 0) / max(usage.get("wall_seconds", 1), 1),
        "cpu_interfaz_pct": 100 * (usage.get("ui_cpu_seconds") or 0) / max(usage.get("wall_seconds", 1), 1),
        "chat_ms": (usage.get("chat_cost_ms"), usage.get("chat_sleep_ms")),
        "burbujas_ms": (usage.get("bubble_cost_ms"), usage.get("bubble_sleep_ms")),
    })
    return report


def print_report(name: str, r: dict) -> None:
    print(f"\n=== Escenario: {name} ({r['messages']} mensajes, {r['jugadores']} de jugadores) ===")
    if r["perdidos_sin_foco"] or r["segundos_sin_foco"] > 0.5:
        print(f"  (el simulador no estuvo al frente {r['segundos_sin_foco']:.0f}s: {len(r['perdidos_sin_foco'])} "
              f"mensajes de ese momento no cuentan como perdidos)")
    print(f"  detectados: {r['detectados']}/{r['jugadores']}  | perdidos: {len(r['perdidos'])}  | repetidos: "
          f"{r['repetidos']}  | basura del OCR: {len(r['basura'])}  | sistema tomado como jugador: "
          f"{r['sistema_tomado_como_jugador']}")
    print(f"  detección: p50 {r['deteccion_p50']:.2f}s  p95 {r['deteccion_p95']:.2f}s  | lugar reservado p50 "
          f"{r['lugar_reservado_p50']:.2f}s")
    print(f"  traducción lista: p50 {r['traduccion_p50']:.2f}s  p95 {r['traduccion_p95']:.2f}s  máx "
          f"{r['traduccion_max']:.2f}s  ({r['traducidos']} traducidos)")
    print(f"  en tu idioma enviados a Claude: {len(r['en_tu_idioma_enviados_a_claude'])}  | en otro idioma sin "
          f"traducir: {len(r['en_otro_idioma_sin_traducir'])}  | avisos de spam: {r['spam_avisos']}")
    print(f"  burbujas mostradas: {r['burbujas_mostradas']}  | desactualizadas: {len(r['burbujas_desactualizadas'])}"
          f"  | parpadeos: chat {r['parpadeos_chat']}, burbujas {r['parpadeos_burbujas']}")
    print(f"  traducciones reacomodadas al llegar un mensaje: p50 {r['reacomodo_p50']:.2f}s  p95 "
          f"{r['reacomodo_p95']:.2f}s  ({r['reacomodo_por_desplazamiento']} al instante, sin esperar el OCR)")
    print(f"  llamadas a Claude: {r['llamadas_claude']}  costo ${r['costo_usd'] or 0:.3f}  | lecturas OCR: "
          f"{r['ocr_llamadas']}  | CPU de Bubble: {r['cpu_pct']:.0f}% (interfaz {r['cpu_interfaz_pct']:.0f}%)")
    chat, bubbles = r["chat_ms"], r["burbujas_ms"]
    if chat[0] is not None and bubbles[0] is not None:
        print(f"  por vuelta: chat {chat[0]:.0f} ms cada {chat[1]:.0f} ms | burbujas {bubbles[0]:.0f} ms cada "
              f"{bubbles[1]:.0f} ms")
    found = r.get("chat_encontrado")
    if found == "no":
        print("  CHAT: no se encontró solo")
    elif found:
        print(f"  CHAT encontrado solo a los {found['segundos']:.1f}s del arranque, zona {found['zona']} "
              f"(real {tuple(CHAT_REGION_REAL)})")
    sends = r.get("envios")
    if sends:
        print(f"  ENVÍO con la barra: {sends['mandados']}/{sends['barras']} mandados, {sends['llegaron']}/"
              f"{sends['esperados']} llegaron al chat, chat abierto {sends['chat_abierto']} veces | teclas que le "
              f"llegaron al juego: {sends['teclas_al_juego'] or 'ninguna'} | teclas extra: "
              f"{sends['teclas_extra'] or 'ninguna'} | tus mensajes traducidos por error: "
              f"{len(sends['propios_traducidos'])}")
        for line in sends["detalle"]:
            print(f"    · {line}")
    for label in ("perdidos", "basura", "en_tu_idioma_enviados_a_claude", "en_otro_idioma_sin_traducir",
                  "burbujas_desactualizadas"):
        for item in r[label][:6]:
            print(f"    · {label}: {item}")


def run(scenarios: list[str]) -> None:
    python = sys.executable
    results = {}
    for name in scenarios:
        stamp = time.strftime("%H%M%S")
        sim_log, app_log = _out_dir() / f"{name}_{stamp}_sim.jsonl", _out_dir() / f"{name}_{stamp}_app.jsonl"
        # "envio": el chat lento y además se escriben mensajes con la barra (atajo → escribir → Enter).
        # "detectar": el chat medio, sin calibrar: la app lo tiene que encontrar sola.
        extra = [name] if name in ("envio", "detectar") else []
        app = subprocess.Popen([python, "-m", "bubble.tools.realtime_benchmark", "--app", str(app_log), "240", *extra])
        for _ in range(120):  # esperar a que Bubble esté listo (sesiones de Claude abiertas)
            time.sleep(0.5)
            if app_log.exists() and '"ready"' in app_log.read_text(encoding="utf-8"):
                break
        scenario = {"envio": "lento", "detectar": "medio"}.get(name, name)
        sim = subprocess.Popen([python, "-m", "bubble.tools.chat_simulator", scenario, str(sim_log)])
        sim.wait()
        app.wait(timeout=60)
        results[name] = analyze(sim_log, app_log)
        print_report(name, results[name])
    (_out_dir() / "ultimo_informe.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--app":
        run_app(Path(sys.argv[2]), float(sys.argv[3]), sends="envio" in sys.argv[4:], detect="detectar" in sys.argv[4:])
        return
    run(sys.argv[1:] or SCENARIOS)


if __name__ == "__main__":
    main()
