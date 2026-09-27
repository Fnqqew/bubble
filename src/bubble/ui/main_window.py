"""Ventana principal de Bubble: la lógica (chat, burbujas, escribir, voz). Lo que se ve se arma en app_view.py."""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path

from .. import roblox, shortcut, win32
from ..async_runner import AsyncRunner
from PIL import ImageDraw

from ..capture.bubble_tracker import BubbleWatcher, find_bubble_boxes
from ..capture.chat_parser import ChatTracker, SpamFilter, parse_chat
from ..capture.chat_locator import find_chat_region
from ..capture.chat_watcher import ChatWatcher
from ..capture.ocr import WindowsOcr, prepare_chat_image
from ..capture.screen import grab
from ..config import Config, save_setting
from ..geometry import Rect
from ..performance import detect_hardware
from ..translate import build_translator
from ..translate.base import TONE_NAMES, ChatLine, TranslationResult
from ..translate.languages import LOCALE_CHOICES
from ..state import load_state, update_state
from .inline import BubbleView, Entry, InlineChatView
from .overlays import CalibrationOverlay, ComposeBar, HotkeyCaptureDialog, TranslationOverlay
from . import app_view
from .theme import apply_theme
from .tutorial import TutorialWindow, build_steps
from .voice_panel import VoicePanel

log = logging.getLogger(__name__)
ICON_PATH = Path(__file__).resolve().parent.parent / "assets" / "bubble.ico"
STATUS_TEXT = {
    "translated": "",
    "adapted": "jerga de otro país adaptada",
    "same_language": "ya estaba en tu idioma",
    "universal": "no necesita traducción",
    "filtered": "tapado por el filtro de Roblox: no se traduce",
    "local": "risa traducida al instante",
    "cache": "desde cache",
    "error": "ERROR",
}
LANG_CHOICES = [name for _code_, name in LOCALE_CHOICES]  # en la lista se ve solo el nombre
_BY_NAME = {name: code for code, name in LOCALE_CHOICES}
AUTO_CHOICE = "Automático (el del chat)"
MULTI = "*"  # destino especial: todos los idiomas principales del chat
MULTI_MAX = 3
TONE_CHOICES = [name for _level, name in sorted(TONE_NAMES.items())]
TONE_HINTS = {
    1: "Claro y correcto, sin jerga: el que menos confusiones genera.",
    2: "Natural y cálido, con palabras completas.",
    3: "Relajado, solo jerga muy conocida.",
    4: "Abreviaturas y jerga comunes de los jugadores de ese idioma.",
    5: "Como escribe un gamer nativo de ese país.",
}
# Mensajes que se muestran en el overlay (los que ya estaban en tu idioma no hace falta).
OVERLAY_STATUSES = {"translated", "adapted", "local", "cache", "error"}


def _unchanged(original: str, translation: str) -> bool:
    """La 'traducción' quedó igual al original (nombres, "Kikuuu"): no vale la pena mostrarla."""
    def key(text: str) -> str:
        return "".join(c for c in text.casefold() if c.isalnum())

    return key(original) == key(translation)


def _code(choice: str) -> str:
    if choice == AUTO_CHOICE:
        return "auto"
    return _BY_NAME.get(choice, choice.split(" - ", 1)[0])


def _choice(code: str) -> str:
    if code == "auto":
        return AUTO_CHOICE
    exact = next((name for known, name in LOCALE_CHOICES if known == code), None)
    # "es-PE" (no está en la lista): el de su idioma sin región.
    return exact or next((name for known, name in LOCALE_CHOICES if known == code.split("-")[0]), code)


def _debug_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


class BubbleWindow:
    def __init__(self, config: Config, root: tk.Tk | None = None) -> None:
        self.config = config
        self.runner = AsyncRunner()
        # El traductor (el SDK de Claude tarda ~2 s en cargarse) se arma en segundo plano, con la ventana ya abierta.
        self.translator = None
        self.tracker = ChatTracker(username=config.roblox.username)
        self.spam = SpamFilter()
        self.ocr: WindowsOcr | None = None
        self.watcher: ChatWatcher | None = None
        self.bubble_watcher: BubbleWatcher | None = None
        self.hardware = None  # procesador y placa de video (se detectan al arrancar)
        self._detecting = False  # buscando el chat en la ventana de Roblox
        self.inline_mode = config.roblox.display_mode != "panel"
        # Barra para escribir: traducciones ya hechas (vista previa) y cuál se manda apenas esté lista.
        self._compose_results: dict[tuple[str, str, int], tuple[list[str], list[str]]] = {}
        self._compose_running: set[tuple[str, str, int]] = set()
        self._send_when_ready: tuple[str, str, int] | None = None
        self._send_as_voice = False  # Ctrl+Enter: se dice en voz en vez de mandarse al chat
        self.saved_region = roblox.load_chat_region()
        self.events: queue.Queue = queue.Queue()
        self.ready = False
        self.roblox_hwnd: int | None = None
        self.compose_hwnd: int | None = None
        self._msg_ids = itertools.count()
        self._chat_rows: dict[int, object] = {}
        self._chat_stream: dict[int, str] = {}

        win32.enable_dpi_awareness()
        win32.set_app_id("Bubble.Translator")
        # La ventana se arma escondida y aparece entera de una vez (antes se veía armarse por partes).
        self.root = root or tk.Tk()
        self.root.withdraw()
        self.root.title("Bubble")
        self.root.geometry("600x820")
        self.root.minsize(540, 620)
        if ICON_PATH.exists():
            self.root.iconbitmap(default=str(ICON_PATH))
        apply_theme(self.root, config.appearance.theme)
        self.voice_panel = VoicePanel(self)
        app_view.build(self)
        app_view.apply_overlay_style(self)
        self._refresh_hotkey_label()
        self.overlay = TranslationOverlay(
            self.root, config.roblox.overlay_seconds, self._overlay_anchor, self._overlay_visible
        )
        self.compose = ComposeBar(self.root, self._compose_preview, self._compose_submit, self._compose_closed)
        self.inline_chat = InlineChatView(self.root, self.tracker.same_message)
        self.bubbles = BubbleView(self.root, self.tracker.same_message)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(30, self._drain_events)
        self.root.after(200, self._poll_roblox)

        self.hotkey = None
        self._start_hotkey()
        self.screenshots = None
        self._start_screenshots()
        # Mantener el acceso directo del escritorio al día (ícono y ubicación) sin frenar el arranque.
        threading.Thread(target=self._sync_shortcut, name="bubble-shortcut", daemon=True).start()
        # El tutorial se abre solo la primera vez; después, solo desde el botón "Tutorial".
        self.tutorial: TutorialWindow | None = None
        if not load_state().get("tutorial_seen"):
            self.root.after(600, self.open_tutorial)
        # Conexión: se conecta al abrir y se desconecta sola si cerrás Roblox (vuelve sola cuando lo abrís).
        self.link = "desconectado"  # "conectando" | "conectado" | "desconectando" | "desconectado" | "error"
        self._closed_by_roblox = False
        self._roblox_seen = False
        self._roblox_gone_since: float | None = None
        self._redetect_chat = False
        self._connect()

    def show(self) -> None:
        """Muestra la ventana ya armada (una sola vez, completa)."""
        self.root.update_idletasks()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_set()  # que ninguna lista arranque con el texto resaltado

    # ================= interfaz (ver app_view.py) =================
    def _region_text(self) -> str:
        if not self.saved_region:
            return "Todavía no vi el chat: abrí Roblox y lo busco solo."
        return "Ya sé dónde está el chat. Si cambiás de juego y no lo encuentro, buscalo en Ajustes."

    # ================= arranque y cierre =================
    async def _startup(self) -> None:
        self.translator = await asyncio.to_thread(build_translator, self.config)
        await self.translator.start()
        for provider in self.translator.router.providers:
            if hasattr(provider, "keep_warm_when"):
                provider.keep_warm_when = win32.roblox_is_foreground  # solo mientras jugás
        rbx = self.config.roblox
        if self.ocr is None:
            self.ocr = WindowsOcr(rbx.ocr_language)
        if self.hardware is None:
            # Se adapta a esta PC: captura por GPU si hay, y lecturas más o menos seguidas según el procesador.
            self.hardware = await asyncio.to_thread(detect_hardware, rbx.gpu_capture, rbx.performance)
            self.events.put(("hardware", self.hardware))
        self.watcher = ChatWatcher(
            self.ocr, self.tracker, self._reading_region, self._on_chat_line,
            on_error=lambda msg: self.events.put(("info", msg)),
            interval_s=rbx.poll_interval_s,
            on_frame=lambda frame: self.events.put(("chat_frame", frame)),
            pacer=self.hardware.pacer("chat", rbx.poll_interval_s, max_s=0.5),
            on_shift=lambda dy: self.events.put(("chat_shift", dy)),
        )
        self.bubble_watcher = BubbleWatcher(
            WindowsOcr(rbx.ocr_language),  # motor propio: así chat y burbujas se leen en paralelo
            self._game_area, self._reading_region,
            on_bubbles=lambda area, items: self.events.put(("bubbles", (area, items))),
            interval_s=rbx.bubble_interval_s,
            pacer=self.hardware.pacer("bubbles", rbx.bubble_interval_s, max_s=1.0),
        )
        if rbx.read_chat:
            self.watcher.start()
        if rbx.translate_bubbles:
            self.bubble_watcher.start()

    def _start_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        spec = self.config.roblox.hotkey
        # Solo con Roblox al frente: en otros programas la tecla escribe normalmente.
        fire, active = (lambda: self.events.put(("hotkey", None))), win32.roblox_is_foreground
        try:
            self.hotkey = win32.start_trigger(spec, fire, active)
        except ValueError as exc:
            # Ej. "°" en un teclado que no lo tiene: se usa F8 para no quedarse sin atajo.
            self._append(f"Atajo inválido ({exc}). Se usa F8; cambialo con «Cambiar…».\n", "error")
            self.config.roblox.hotkey = "F8"
            self.hotkey = win32.start_trigger("F8", fire, active)
        if self.hotkey.error:
            self._append(f"{self.hotkey.error}. Elegí otro con «Cambiar…».\n", "error")
        self._refresh_hotkey_label()

    def _refresh_hotkey_label(self) -> None:
        if hasattr(self, "hotkey_label"):
            self.hotkey_label.configure(text=win32.describe_binding(self.config.roblox.hotkey))

    def _change_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()  # que el atajo actual no se dispare mientras elegís otro
            self.hotkey = None
        HotkeyCaptureDialog(self.root, self._on_hotkey_captured)

    def _on_hotkey_captured(self, spec: str | None) -> None:
        if spec:
            self.config.roblox.hotkey = spec
            save_setting("roblox", "hotkey", spec)
            self._set_status(f"Nuevo atajo: {win32.describe_binding(spec)}")
        self._start_hotkey()

    def open_tutorial(self) -> None:
        if self.tutorial is not None:
            self.tutorial.lift()
            return
        steps = build_steps(win32.describe_binding(self.config.roblox.hotkey), self._detect_chat, self._capture_test)
        self.tutorial = TutorialWindow(self.root, steps, self._on_tutorial_closed, ICON_PATH.with_suffix(".png"))

    def _on_tutorial_closed(self, reason: str) -> None:
        # Cualquier forma de cerrarlo (completado, saltado, "no mostrar más") cuenta como visto.
        self.tutorial = None
        update_state(tutorial_seen=True)
        if reason != "completado":
            self._set_status("Podés volver a ver el tutorial cuando quieras con el botón «Tutorial».")

    def _sync_shortcut(self) -> None:
        try:
            shortcut.ensure_desktop_shortcut()
        except Exception as exc:  # noqa: BLE001 - no es crítico
            self.events.put(("info", f"No se pudo actualizar el acceso directo: {exc}"))

    async def _shutdown(self) -> None:
        if self.watcher:
            self.watcher.stop()
        if self.bubble_watcher:
            self.bubble_watcher.stop()
        if self.translator is not None:
            try:
                async with asyncio.timeout(8):
                    await self.translator.close()
            except (Exception, TimeoutError):  # noqa: BLE001 - las sesiones colgadas se abandonan
                log.warning("No se pudo cerrar la conexión con Claude a tiempo")

    # ================= conectarse / desconectarse / refrescar =================
    GONE_S = 5.0  # Roblox se reinicia al pasar de un juego a otro: se espera un poco antes de desconectar

    def _connect(self, message: str = "") -> None:
        if self.link in ("conectando", "conectado", "desconectando"):
            return
        self.link = "conectando"
        self._start_error = None
        if message:
            self._append(f"{message}\n", "info")
        self._set_status("Conectando con tu suscripción de Claude…")
        self._refresh_header()
        future = self.runner.submit(self._startup())
        future.add_done_callback(lambda f: self.events.put(("started", f.exception())))

    def _disconnect(self, message: str, by_roblox: bool = False, then=None) -> None:
        """Apaga todo: lectura del chat y de las burbujas, la voz, el micrófono virtual y la conexión con Claude. Y
        olvida la partida (lo que se veía en pantalla, los mensajes, las voces)."""
        if self.link in ("desconectando", "desconectado"):
            if then:
                then()
            return
        self.link = "desconectando"
        self.ready = False
        self._closed_by_roblox = by_roblox
        self._reset_session()
        self.voice_panel.pause()
        self._refresh_header()
        future = self.runner.submit(self._shutdown())
        future.add_done_callback(lambda _f: self.events.put(("disconnected", (message, then))))

    def _ev_disconnected(self, payload) -> None:
        message, then = payload
        self.link = "desconectado"
        self.watcher = self.bubble_watcher = None
        self.translator = None
        if message:
            self._append(f"{message}\n", "info")
        self._set_status("")
        self._refresh_header()
        if then:
            then()

    def _reset_session(self) -> None:
        self.tracker = ChatTracker(username=self.config.roblox.username)
        self.spam = SpamFilter()
        self.inline_chat.reset(self.tracker.same_message)
        self.bubbles.reset(self.tracker.same_message)
        if self.compose.visible:
            self.compose.close()
        self._compose_results.clear()
        self._compose_running.clear()
        self._send_when_ready = None

    def _refresh(self) -> None:
        """Botón «Refrescar»: si algo anda raro, reinicia todo el mecanismo sin cerrar Bubble (lectura, traducciones
        en pantalla, voz, atajo y conexión con Claude) y vuelve a buscar el chat."""
        if self.link in ("conectando", "desconectando"):
            return
        self._redetect_chat = True
        self._start_hotkey()
        self._set_status("Refrescando…")
        if self.link == "desconectado":
            self._connect("Refresqué todo: me vuelvo a conectar.")
        else:
            self._disconnect("", then=lambda: self._connect("Refresqué todo: me vuelvo a conectar."))

    def _watch_roblox_session(self, running: bool) -> None:
        """Si cerrás Roblox, Bubble se desconecta; cuando lo abrís de nuevo, se reconecta solo."""
        if running:
            self._roblox_seen = True
            self._roblox_gone_since = None
            if self.link == "desconectado" and self._closed_by_roblox:
                self._closed_by_roblox = False
                self._connect("Roblox volvió: me conecto de nuevo.")
            return
        if not self._roblox_seen or self.link != "conectado":
            return
        now = time.monotonic()
        if self._roblox_gone_since is None:
            self._roblox_gone_since = now
        elif now - self._roblox_gone_since >= self.GONE_S:
            self._roblox_gone_since = None
            self._disconnect("Roblox se cerró, así que me desconecté. Cuando lo abras, me conecto solo.",
                             by_roblox=True)

    def _start_screenshots(self) -> None:
        """Que las traducciones salgan en tus capturas de pantalla (ver screenshots.py)."""
        from ..screenshots import ScreenshotKeys

        if self.screenshots:
            self.screenshots.stop()
            self.screenshots = None
        if self.config.appearance.in_screenshots:
            self.screenshots = ScreenshotKeys(win32.roblox_is_foreground)
            self.screenshots.start()

    def _on_close(self) -> None:
        self._set_status("Cerrando...")
        if self.hotkey:
            self.hotkey.stop()
        if self.screenshots:
            self.screenshots.stop()
        self.voice_panel.stop()
        try:
            self.runner.submit(self._shutdown()).result(timeout=5)
        except Exception:  # noqa: BLE001 - se cierra igual
            pass
        self.runner.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()

    # ================= Roblox =================
    def _current_region(self) -> Rect | None:
        return roblox.resolve_chat_region(self.saved_region)

    def _reading_region(self) -> Rect | None:
        # Solo se lee con Roblox en primer plano: si no, en esa zona de la pantalla hay otra cosa.
        return roblox.resolve_chat_region(self.saved_region, require_foreground=True)

    def _overlay_visible(self) -> bool:
        return win32.roblox_is_foreground() or self.compose.visible

    def _poll_roblox(self) -> None:
        from .widgets import palette

        hwnd = win32.find_roblox_window()
        running = hwnd is not None or win32.roblox_running()
        colors = palette()
        if hwnd:
            self.roblox_status.configure(text="Roblox está abierto.", foreground=colors["good"])
        elif running:
            self.roblox_status.configure(text="Roblox está minimizado.", foreground=colors["muted"])
        else:
            self.roblox_status.configure(text="Roblox está cerrado.", foreground=colors["muted"])
        self._watch_roblox_session(running)
        self._refresh_header(bool(hwnd))
        if self.saved_region and hwnd and roblox.region_too_wide(self.saved_region, win32.client_rect(hwnd)):
            log.info("La zona del chat guardada era casi toda la ventana: se vuelve a buscar el chat")
            roblox.forget_chat_region()
            self.saved_region = None
            self.region_label.configure(text=self._region_text())
        # Sin chat calibrado: se busca solo mientras jugás (apenas haya un par de mensajes a la vista).
        if self.ready and not self.saved_region and hwnd and win32.roblox_is_foreground():
            self._detect_chat(quiet=True)
        self._refresh_perf_label()
        self.root.after(1500, self._poll_roblox)

    def _refresh_header(self, roblox_open: bool | None = None) -> None:
        if roblox_open is None:
            roblox_open = win32.find_roblox_window() is not None
        link = getattr(self, "link", "conectando")
        busy = link in ("conectando", "desconectando")
        self.refresh_button.state(["disabled"] if busy else ["!disabled"])
        if link == "error":
            app_view.set_state(self, "bad", "No pude conectarme con Claude. Mirá «Actividad» o tocá Refrescar.",
                               "Sin conexión")
        elif link == "desconectando":
            app_view.set_state(self, "muted", "Desconectando…", "Desconectando")
        elif link == "desconectado" and self._closed_by_roblox:
            app_view.set_state(self, "muted", "Roblox se cerró, así que me desconecté. Cuando lo abras, vuelvo solo.",
                               "En pausa")
        elif not self.ready:
            app_view.set_state(self, "warn", "Preparando todo… dame un segundito.", "Conectando")
        elif roblox_open:
            app_view.set_state(self, "good", "Todo listo. ¡A jugar!", "Traduciendo")
        else:
            app_view.set_state(self, "good", "Listo. Abrí Roblox y yo me encargo del resto.", "Listo")

    def _refresh_perf_label(self) -> None:
        hardware = getattr(self, "hardware", None)
        if hardware is None:
            return
        text = f"{hardware.summary()}"
        paces = []
        if self.watcher and self.watcher.running and self.watcher.pacer and self.watcher.pacer.cost:
            paces.append(f"chat cada {self.watcher.pacer.sleep:.2f} s")
        bubbles = self.bubble_watcher
        if bubbles and bubbles.running and bubbles.pacer and bubbles.pacer.cost:
            paces.append(f"burbujas cada {bubbles.pacer.sleep:.2f} s")
        if paces:
            text += " · lee " + ", ".join(paces)
        self.perf_label.configure(text=text)

    def _overlay_anchor(self) -> tuple[int, int] | None:
        region = self._current_region()
        if region:
            return region.right + 12, region.top
        hwnd = win32.find_roblox_window()
        if hwnd:
            client = win32.client_rect(hwnd)
            return client.left + 12, client.top + 60
        return None

    def _calibrate(self) -> None:
        hwnd = win32.find_roblox_window()
        if hwnd:
            area = win32.client_rect(hwnd)
            win32.force_foreground(hwnd)
        else:
            area = Rect(0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        client = area if hwnd else None
        self.root.after(250, lambda: CalibrationOverlay(self.root, area, lambda rect: self._on_calibrated(rect, client)))

    def _detect_chat(self, quiet: bool = False) -> None:
        """Busca el chat en la ventana de Roblox (cada juego puede ponerlo en otro lugar). Con `quiet`, sin traer
        Roblox al frente ni avisar si no lo encuentra (se reintenta solo)."""
        hwnd = win32.find_roblox_window()
        if not hwnd or not self.ocr or self._detecting:
            if not quiet:
                self._set_status("Abrí Roblox (y esperá a que Bubble esté listo) para detectar el chat.")
            return
        self._detecting = True
        if not quiet:
            win32.force_foreground(hwnd)

        async def detect():
            await asyncio.sleep(0 if quiet else 0.35)
            client = win32.client_rect(hwnd)
            image = await asyncio.to_thread(grab, client)
            prepared = await asyncio.to_thread(prepare_chat_image, image)
            rows = await self.ocr.recognize(prepared)
            return client, find_chat_region(rows, image.width, image.height)

        future = self.runner.submit(detect())
        future.add_done_callback(lambda f: self.events.put(("chat_detected", (f, quiet))))

    def _ev_chat_detected(self, payload) -> None:
        future, quiet = payload
        self._detecting = False
        if future.exception():
            if not quiet:
                self._append(f"No se pudo detectar el chat: {future.exception()}\n", "error")
            return
        client, guess = future.result()
        if guess is None:
            if not quiet:
                self._set_status("No encontré el chat: esperá a que haya un par de mensajes y probá de nuevo, "
                                 "o marcalo «a mano…».")
            return
        self._on_calibrated(guess.region.offset(client.left, client.top), client)
        self._append(f"Encontré el chat de este juego ({guess.lines} mensajes a la vista).\n", "info")

    def _on_calibrated(self, rect: Rect | None, client: Rect | None) -> None:
        if rect is None:
            self._set_status("Listo, no cambié nada.")
            return
        roblox.save_chat_region(rect, client)
        self.saved_region = roblox.load_chat_region()
        self.region_label.configure(text=self._region_text())
        self._set_status("¡Encontré el chat! Ya lo estoy leyendo.")

    def _toggle_reading(self) -> None:
        enabled = self.read_var.get()
        self.config.roblox.read_chat = enabled

        async def apply() -> None:
            if self.watcher:
                self.watcher.start() if enabled else self.watcher.stop()

        self.runner.submit(apply())
        save_setting("roblox", "read_chat", enabled)
        self._set_status("Traduciendo el chat." if enabled else "Pausé la traducción del chat.")

    def _toggle_bubbles(self) -> None:
        enabled = self.bubbles_var.get()
        self.config.roblox.translate_bubbles = enabled
        save_setting("roblox", "translate_bubbles", enabled)

        async def apply() -> None:
            if self.bubble_watcher:
                self.bubble_watcher.start() if enabled else self.bubble_watcher.stop()

        self.runner.submit(apply())
        self._set_status("Traduciendo las burbujas." if enabled else "Pausé la traducción de las burbujas.")

    def _game_area(self) -> Rect | None:
        """Pantalla del juego (para buscar burbujas), solo con Roblox en primer plano."""
        if not win32.roblox_is_foreground():
            return None
        hwnd = win32.find_roblox_window()
        return win32.client_rect(hwnd) if hwnd else None

    def _capture_test(self) -> None:
        # Al tocar el botón, la ventana activa es Bubble: traer Roblox al frente antes de capturar.
        hwnd = win32.find_roblox_window()
        if hwnd:
            win32.force_foreground(hwnd)

        async def test() -> tuple:
            await asyncio.sleep(0.4 if hwnd else 0)
            region = self._current_region()
            if region is None:
                return None, [], []
            image = await asyncio.to_thread(grab, region)
            path = _debug_dir() / "captura_chat.png"
            image.save(path)
            rows = await self.ocr.recognize(prepare_chat_image(image)) if self.ocr else []
            self._bubble_report = await self._bubble_diagnosis(region)
            return path, rows, parse_chat(rows, self.tracker.is_known_name, frame_width=image.width)

        future = self.runner.submit(test())
        future.add_done_callback(lambda f: self.events.put(("capture_test", f)))

    async def _bubble_diagnosis(self, chat_region: Rect | None) -> list[str]:
        """Burbujas que se ven ahora: se marcan en rojo en una imagen y se lee su texto."""
        hwnd = win32.find_roblox_window()
        if not hwnd or not self.ocr:
            return ["Roblox no está abierto: no se buscaron burbujas."]
        area = win32.client_rect(hwnd)
        image = await asyncio.to_thread(grab, area)
        exclude = chat_region.offset(-area.left, -area.top) if chat_region else None
        boxes = await asyncio.to_thread(find_bubble_boxes, image, exclude)
        marked = image.copy()
        draw = ImageDraw.Draw(marked)
        report = [f"Burbujas encontradas: {len(boxes)}"]
        for box in boxes:
            draw.rectangle((box.left, box.top, box.right, box.bottom), outline=(255, 0, 0), width=3)
            rows = await self.ocr.recognize(image.crop((box.left, box.top, box.right, box.bottom)))
            text = " ".join(r.text for r in rows) or "(sin texto legible)"
            report.append(f"   ({box.left},{box.top}) {box.width}x{box.height}: {text}")
        path = _debug_dir() / "burbujas.png"
        marked.save(path)
        report.append(f"Imagen con las burbujas marcadas en rojo: {path}")
        return report

    def _on_chat_line(self, line: ChatLine) -> None:
        # Hilo de asyncio: traducir sin frenar la lectura del chat. Cada mensaje lleva un número para
        # que el overlay respete el orden del chat aunque las traducciones terminen en otro orden.
        spam = self.spam.check(line)
        if spam:
            self.events.put(("info", f"Spam de {line.speaker} ({spam}), no se traduce: {line.text}"))
            return
        msg_id = next(self._msg_ids)

        async def translate() -> None:
            result = await self.translator.translate_incoming(
                line.text, line.speaker,
                on_delta=lambda chunk: self.events.put(("chat_delta", (msg_id, chunk))),
                on_pending=lambda: self.events.put(("chat_pending", (msg_id, line))),
            )
            self.events.put(("chat", (msg_id, line, result)))

        asyncio.get_running_loop().create_task(translate())

    # ================= escribir en Roblox =================
    def _targets(self) -> tuple[list[str], dict[str, str]]:
        """Idiomas para Tab: el principal, "todos" (si el chat mezcla idiomas), el resto del chat y los comunes."""
        mine = self.translator.my_locale[0]
        in_chat = self.translator.chat_languages()
        ordered = [self.translator.outgoing_target(), *in_chat, "en", "pt", "es", "fr", "hi", "ru", "tr", "id"]
        targets = [c for c in dict.fromkeys(ordered) if c != mine] or ["en"]
        labels = {}
        if len(in_chat) >= 2:
            targets.insert(1, MULTI)
            labels[MULTI] = "Todos los del chat (" + " + ".join(c.upper() for c in in_chat[:MULTI_MAX]) + ")"
        return targets, labels

    def _on_hotkey(self) -> None:
        """El atajo abre la barra para escribir. No toca el juego: ninguna tecla le llega a Roblox hasta que
        mandás el mensaje (y ahí solo: abrir el chat, el texto y Enter)."""
        if not self.ready:
            self._set_status("Todavía conectando con tu suscripción de Claude…")
            return
        if self.compose.visible:
            return
        self.roblox_hwnd = win32.find_roblox_window()
        area = win32.client_rect(self.roblox_hwnd) if self.roblox_hwnd else None
        targets, labels = self._targets()
        self.compose.open(targets, area, self.config.user.tone, labels)

    def _compose_preview(self, text: str, target: str, tone: int) -> None:
        """Traducción para ver mientras escribís (y que al apretar Enter ya esté lista)."""
        key = (text, target, tone)
        if key in self._compose_results:
            messages, _own, _pairs = self._compose_results[key]
            self.compose.show_preview(key, " / ".join(messages), "done")
        elif key not in self._compose_running:
            self._compose_running.add(key)
            self.runner.submit(self._compose_translate(key))

    async def _compose_translate(self, key: tuple[str, str, int]) -> None:
        text, target, tone = key
        partial: list[str] = []

        def on_delta(chunk: str) -> None:
            partial.append(chunk)
            self.events.put(("compose_partial", (key, "".join(partial))))

        try:
            if target == MULTI:
                # Servidor con varios idiomas: se traduce a los principales en paralelo y se manda junto.
                languages = self.translator.chat_languages()[:MULTI_MAX] or ["en"]
                results = await asyncio.gather(
                    *(self.translator.translate_outgoing(text, lang, tone=tone) for lang in languages)
                )
                ok = [(r.target_lang, r.translation) for r in results if r.status != "error"]
                if not ok:
                    raise RuntimeError(results[0].error or "No se pudo traducir")
                messages = roblox.combine_translations(ok)
                own = messages + [translation for _lang, translation in ok]
            else:
                result = await self.translator.translate_outgoing(text, target, on_delta=on_delta, tone=tone)
                if result.status == "error":
                    raise RuntimeError(result.error or "No se pudo traducir")
                messages = own = [result.translation]
                ok = [(result.target_lang or target, result.translation)]
            self.events.put(("compose_done", (key, messages, own, ok, "")))
        except Exception as exc:  # noqa: BLE001 - se muestra en la barra
            self.events.put(("compose_done", (key, [], [], [], str(exc) or "No se pudo traducir")))

    def _ev_compose_partial(self, payload) -> None:
        key, text = payload
        self.compose.show_preview(key, text, "working")

    def _ev_compose_done(self, payload) -> None:
        key, messages, own, pairs, error = payload
        self._compose_running.discard(key)
        if error:
            self.compose.show_preview(key, error, "error")
            if self._send_when_ready == key:
                self._send_when_ready = None
                self.compose.unlock()  # se puede corregir y apretar Enter de nuevo
            return
        if len(self._compose_results) > 60:
            self._compose_results.clear()
        self._compose_results[key] = (messages, own, pairs)
        self.compose.show_preview(key, " / ".join(messages), "done")
        if self._send_when_ready == key:
            self._compose_send(key)

    def _compose_submit(self, text: str, target: str, tone: int, voice: bool = False) -> None:
        key = (text, target, tone)
        self._send_as_voice = voice
        if key in self._compose_results:
            self._compose_send(key)  # la vista previa ya estaba lista: se manda al instante
            return
        self._send_when_ready = key
        self.compose.show_preview(key, "", "working")
        if key not in self._compose_running:
            self._compose_running.add(key)
            self.runner.submit(self._compose_translate(key))

    def _compose_send(self, key: tuple[str, str, int]) -> None:
        self._send_when_ready = None
        messages, own, pairs = self._compose_results[key]
        self.compose.close(sent=True)
        if self._send_as_voice:
            # Escrito a voz: se pide la versión para decir (sin "vc", "kkkk"…, que la voz leería letra por letra) y se
            # dice con la voz sintética.
            if self.roblox_hwnd:
                win32.force_foreground(self.roblox_hwnd)  # volver al juego

            async def speak() -> None:
                spoken = []
                for language, text in pairs:
                    try:
                        result = await self.translator.translate_outgoing(key[0], language, tone=key[2], spoken=True)
                        ok = result.status != "error" and result.translation.strip()
                        spoken.append((result.target_lang or language, result.translation if ok else text))
                    except Exception:  # noqa: BLE001 - se dice la traducción del chat, que ya estaba
                        spoken.append((language, text))
                self.voice_panel.say(spoken, key[0])

            self.runner.submit(speak())
            return
        for text in own:
            self.tracker.mark_sent(text)  # que tu propio mensaje no se traduzca al aparecer en el chat
        self._append(f"Vos: {key[0]}\n   → {' / '.join(messages)}\n", "out")
        hwnd = self.roblox_hwnd
        if hwnd is None:
            self.root.clipboard_clear()
            self.root.clipboard_append(" / ".join(messages))
            self._set_status("Roblox no está abierto: la traducción quedó en el portapapeles.")
            return

        def work() -> None:
            try:
                # Solo: Roblox al frente, abrir el chat, escribir el mensaje y Enter.
                roblox.send_messages(messages, hwnd, self.config.roblox.open_chat_key, self.config.roblox.send_method)
                self.events.put(("status", f"Enviado: {' / '.join(messages)}"))
            except Exception as exc:  # noqa: BLE001 - se muestra al usuario
                self.events.put(("info", f"No se pudo enviar al chat: {exc}"))

        threading.Thread(target=work, name="bubble-send", daemon=True).start()

    def _compose_closed(self) -> None:
        if self.roblox_hwnd:
            win32.force_foreground(self.roblox_hwnd)  # volver al juego

    def _ev_status(self, message: str) -> None:
        self._set_status(message)

    # ================= prueba sin Roblox =================
    def _on_lang_change(self, _event=None) -> None:
        self.config.user.language = _code(self.my_lang.get())
        self.config.user.outgoing_language = _code(self.out_lang.get())
        save_setting("user", "language", self.config.user.language)
        save_setting("user", "outgoing_language", self.config.user.outgoing_language)

    def _on_tone_change(self, _event=None) -> None:
        self.config.user.tone = TONE_CHOICES.index(self.tone.get()) + 1
        self.tone_hint.configure(text=TONE_HINTS[self.config.user.tone])
        save_setting("user", "tone", self.config.user.tone)

    def _send_incoming(self) -> None:
        text = self.incoming_text.get().strip()
        if not text or not self.ready:
            return
        speaker = self.speaker.get().strip() or "Player"
        self.incoming_text.delete(0, "end")
        self._run_sim(self.translator.translate_incoming(text, speaker, self._on_delta), ("in", speaker))

    def _send_outgoing(self) -> None:
        text = self.outgoing_text.get().strip()
        if not text or not self.ready:
            return
        self.outgoing_text.delete(0, "end")
        self._run_sim(self.translator.translate_outgoing(text, on_delta=self._on_delta), ("out", "Yo"))

    def _run_sim(self, coro, meta: tuple[str, str]) -> None:
        self.events.put(("live_reset", None))
        future = self.runner.submit(coro)
        future.add_done_callback(lambda f: self.events.put(("sim_result", (meta, f))))

    def _on_delta(self, chunk: str) -> None:
        self.events.put(("delta", chunk))

    # ================= eventos (hilo de la interfaz) =================
    def _drain_events(self) -> None:
        latest: dict[str, object] = {}  # capturas: solo importa la más reciente
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind in ("chat_frame", "bubbles"):
                    latest[kind] = payload
                    continue
                if kind == "chat_shift":
                    latest.pop("chat_frame", None)  # una lectura anterior al desplazamiento ya quedó vieja
                handler = getattr(self, f"_ev_{kind}", None)
                if handler:
                    handler(payload)
        except queue.Empty:
            pass
        for kind, payload in latest.items():
            getattr(self, f"_ev_{kind}")(payload)
        if "bubbles" not in latest:
            self.bubbles.animate()  # entre detecciones, la traducción sigue a la burbuja
        self.root.after(15, self._drain_events)  # rápido: las burbujas se mueven con la cámara

    def _ev_chat_frame(self, frame) -> None:
        if self.inline_mode:
            self.inline_chat.render(frame, win32.roblox_is_foreground())

    def _ev_chat_shift(self, dy: int) -> None:
        if self.inline_mode and win32.roblox_is_foreground():
            self.inline_chat.shift(dy)

    def _ev_bubbles(self, payload) -> None:
        area, items = payload
        visible = win32.roblox_is_foreground()
        for item in items:
            entry, is_new = self.bubbles.entry_for(item.text)
            if is_new and self.ready:
                self._translate_bubble(entry)
        self.bubbles.render(area, items, visible, self._bubble_text)

    def _bubble_text(self, entry: Entry) -> str:
        """Si el mismo mensaje ya está (o se está) traduciendo en el chat, se reutiliza: no se gasta dos veces."""
        chat = self.inline_chat.find_text(entry.original)
        if chat is not None:
            return chat.text if chat.status == "done" else ""
        return entry.text if entry.status == "done" else ""

    def _translate_bubble(self, entry: Entry) -> None:
        async def translate() -> None:
            await asyncio.sleep(0.6)  # casi siempre el mismo mensaje llega también por el chat
            if self.inline_chat.find_text(entry.original) is not None:
                entry.status = "linked"
                return
            result = await self.translator.translate_incoming(entry.original, "")
            self.events.put(("bubble_result", (entry, result)))

        self.runner.submit(translate())

    def _ev_bubble_result(self, payload) -> None:
        entry, result = payload
        show = result.status in OVERLAY_STATUSES and not _unchanged(result.original, result.translation)
        entry.status = "done" if show else "hidden"
        entry.text = result.translation if show and result.status != "error" else ""
        self._log_result("in", "(burbuja)", result)

    def _ev_started(self, error: BaseException | None) -> None:
        if self.link != "conectando":
            return  # mientras tanto se desconectó (se cerró Roblox o se refrescó)
        if error:
            self.link = "error"
            self._start_error = error
            self._set_status(f"No se pudo conectar: {error}")
            self._append(f"{error}\n", "error")
            self._refresh_header()
            return
        self.link = "conectado"
        self.ready = True
        self._set_status("")
        self._refresh_header()
        self.voice_panel.start()
        if self._redetect_chat:
            self._redetect_chat = False
            if win32.find_roblox_window():
                self._detect_chat(quiet=True)

    # --- voz
    def _ev_voice_status(self, text: str) -> None:
        if self.voice_panel.status is not None:
            self.voice_panel.status.configure(text=text)
        if text:
            self._set_status(text)

    def _ev_voice_devices(self, payload) -> None:
        self.voice_panel.show_devices(*payload)

    def _ev_voice_cable_done(self, _payload) -> None:
        self.voice_panel.cable_done()

    def _ev_voice_ready(self, then) -> None:
        then()

    def _ev_voice_subtitle(self, payload) -> None:
        original, translation, language = payload
        self.voice_panel.show(original, translation, language)
        said = "" if original == "(vos)" else f": {original}"
        self._append(f"🎙 Vos (en voz){said}\n   → {translation}\n", "out")

    def _ev_voice_line(self, line) -> None:
        from .subtitles import speaker_name

        self._append(f"🔊 {speaker_name(line.speaker)} ({line.language.upper()}): {line.original}\n"
                     f"   → {line.translation}\n", "in")

    def _ev_hardware(self, hardware) -> None:
        self.hardware = hardware
        self._refresh_perf_label()

    def _ev_hotkey(self, _payload) -> None:
        self._on_hotkey()

    def _ev_info(self, message: str) -> None:
        self._append(f"{message}\n", "info")

    def _ev_live_reset(self, _payload) -> None:
        self.live.configure(text="Traduciendo...")

    def _ev_delta(self, chunk: str) -> None:
        current = self.live.cget("text")
        self.live.configure(text=("" if current == "Traduciendo..." else current) + chunk)

    def _ev_chat_pending(self, payload: tuple[int, ChatLine]) -> None:
        # El mensaje queda "seleccionado" apenas aparece, en su lugar del chat, hasta que llega la traducción.
        msg_id, line = payload
        if self.inline_mode:
            self.inline_chat.pending(msg_id, line)
            return
        self._chat_rows[msg_id] = self.overlay.show(line.speaker, f"{line.text} …", muted=True)
        self._chat_stream[msg_id] = ""

    def _ev_chat_delta(self, payload: tuple[int, str]) -> None:
        msg_id, chunk = payload
        if self.inline_mode:
            self.inline_chat.delta(msg_id, chunk)
            return
        if msg_id in self._chat_stream:
            self._chat_stream[msg_id] += chunk
            self.overlay.set_text(self._chat_rows.get(msg_id), self._chat_stream[msg_id])

    def _ev_chat(self, payload: tuple[int, ChatLine, TranslationResult]) -> None:
        msg_id, line, result = payload
        self._log_result("in", line.speaker, result)
        show = result.status in OVERLAY_STATUSES and not _unchanged(result.original, result.translation)
        # Si falla, se deja ver el original (sin parche) en vez de taparlo.
        text = result.translation if show and result.status != "error" else None
        if self.inline_mode:
            self.inline_chat.final(msg_id, line, text)
            return
        row = self._chat_rows.pop(msg_id, None)
        self._chat_stream.pop(msg_id, None)
        if show:
            text = result.translation if result.status != "error" else f"⚠ {result.original}"
            if not self.overlay.set_text(row, text):
                self.overlay.show(line.speaker, text)
        else:
            self.overlay.remove(row)  # nada nuevo que mostrar (ya estaba en tu idioma, nombres, etc.)

    def _ev_sim_result(self, payload) -> None:
        (direction, speaker), future = payload
        self.live.configure(text="")
        if future.exception():
            self._append(f"{future.exception()}\n", "error")
            return
        result: TranslationResult = future.result()
        self._log_result(direction, speaker, result)
        if direction == "out":
            self.root.clipboard_clear()
            self.root.clipboard_append(result.translation)
            self._set_status("Traducción copiada al portapapeles.")

    def _ev_capture_test(self, future) -> None:
        if future.exception():
            self._append(f"Error en la captura: {future.exception()}\n", "error")
            return
        path, rows, messages = future.result()
        if path is None:
            self._append("Todavía no encontré el chat: abrí Roblox y tocá «Detectar chat».\n", "error")
            return
        self._append(f"Captura guardada en {path}\n", "info")
        preview = self.inline_chat.preview() if self.inline_mode else None
        if preview is not None:
            # Los parches no salen en capturas de pantalla (a propósito): esta imagen muestra cómo se ven.
            preview_path = path.with_name("vista_traducida.png")
            preview.save(preview_path)
            self._append(f"Vista del chat con las traducciones: {preview_path}\n", "info")
        self._append(f"El OCR leyó {len(rows)} líneas:\n", "info")
        for row in rows:
            self._append(f"   | {row.text}\n", "meta")
        self._append(f"Mensajes reconocidos: {len(messages)}\n", "info")
        for message in messages:
            self._append(f"   {message.speaker}: {message.text}\n", "in")
        if rows and not messages:
            self._append("No se reconoció el formato 'Nombre: mensaje'. Ajustá la región para que abarque el chat.\n", "error")
        for line in getattr(self, "_bubble_report", []):
            self._append(f"{line}\n", "info")

    # ================= helpers =================
    def _log_result(self, direction: str, speaker: str, r: TranslationResult) -> None:
        tag = "in" if direction == "in" else "out"
        arrow = "→" if r.status in ("translated", "adapted", "local", "cache") else "="
        self._append(f"{speaker}: {r.original}\n", tag)
        self._append(f"   {arrow} {r.translation}\n", "error" if r.status == "error" else tag)
        details = [f"{r.source_lang or '?'} → {r.target_lang}", f"{r.total_s:.2f}s"]
        if r.ttft_s is not None:
            details.append(f"primera palabra {r.ttft_s:.2f}s")
        if STATUS_TEXT.get(r.status):
            details.append(STATUS_TEXT[r.status])
        if r.error:
            details.append(r.error)
        self._append(f"   [{' · '.join(details)}]\n", "meta")

    def _append(self, text: str, tag: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text, tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_status(self, text: str) -> None:
        self.status.configure(text=text)


def run_main_window(config: Config) -> None:
    window = BubbleWindow(config)
    window.show()
    window.run()
