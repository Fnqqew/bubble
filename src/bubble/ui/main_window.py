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
from .. import pro
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
from .pro_panel import ProPanel
from .tests_panel import TestsPanel
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
AUTO_CHOICE = "Automático (el último que usaste)"
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
    # "es-PE" (no está en la lista): el de su idioma sin región; "en" (elegido con Tab): el primero de ese idioma.
    language = code.split("-")[0]
    return exact or next((name for known, name in LOCALE_CHOICES if known == language), None) or next(
        (name for known, name in LOCALE_CHOICES if known.split("-")[0] == language), code)


def _debug_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    path = Path(base) / "Bubble" / "debug"
    path.mkdir(parents=True, exist_ok=True)
    return path


DESIGN_SCALE = 1.25  # la ventana se diseñó con Windows al 125 %: 600 × 820


NO_CLAUDE_PROBLEMS = {"sin_claude", "sin_sesion", "gratis"}  # (= system.NO_CLAUDE)


class NoClaudeError(RuntimeError):
    """No hay Claude ni clave de Bubble Pro: no hay con qué traducir."""


def window_geometry(pixels_per_inch: float, work: tuple[int, int, int, int], size: tuple[int, int] = (600, 820),
                    minimum: tuple[int, int] = (540, 620)) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    """(ancho, alto, x, y) y el tamaño mínimo de la ventana, según la escala de Windows y lo libre de la pantalla
    (`work`: izquierda, arriba, derecha, abajo, sin la barra de tareas)."""
    factor = max(0.8, pixels_per_inch / 96) / DESIGN_SCALE
    left, top, right, bottom = work
    free_w, free_h = right - left, bottom - top
    width = min(int(size[0] * factor), free_w - 40)
    height = min(int(size[1] * factor), free_h - int(60 * factor))
    x = left + (free_w - width) // 2
    y = top + max(0, (free_h - height) // 2 - int(20 * factor))
    return (width, height, x, y), (min(int(minimum[0] * factor), width), min(int(minimum[1] * factor), height))


def fit_window(root: tk.Tk, size: tuple[int, int] = (600, 820), minimum: tuple[int, int] = (540, 620)) -> None:
    """Tamaño según la escala de Windows (100 %, 125 %, 150 %…) y lo que entra en tu pantalla, centrada. Antes era
    siempre 600 × 820 píxeles: con escala al 150 % quedaba chica y cortada, y en pantallas bajas (1366 × 768) se salía
    por abajo."""
    import ctypes
    from ctypes import wintypes

    work = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0)  # SPI_GETWORKAREA
    area = (work.left, work.top, work.right, work.bottom)
    if area[2] <= area[0] or area[3] <= area[1]:
        area = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    (width, height, x, y), smallest = window_geometry(root.winfo_fpixels("1i"), area, size, minimum)
    root.minsize(*smallest)
    root.geometry(f"{width}x{height}+{x}+{y}")


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
        self.system_info = None  # tu equipo (ver system.py): se revisa al abrir
        self.system_plan = None
        self._checking_system = False
        self._support = None  # la ventana de Soporte, si está abierta
        self.update_release = None  # la versión nueva, si hay (ver update.py)
        # Sin Claude (no está, sin sesión o cuenta gratuita): Bubble Pro traduce con los créditos de Deepgram y Basic
        # queda bloqueado hasta que conectes Claude (ver cloud/agent.py y no_claude_window.py).
        self.claude_problem = ""
        self.cloud_translation = False
        self._no_claude_window = None
        self._update_window = None
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
        fit_window(self.root)
        if ICON_PATH.exists():
            self.root.iconbitmap(default=str(ICON_PATH))
        apply_theme(self.root, config.appearance.theme)
        # Bubble Pro (la voz en la nube) queda prendido si lo dejaste así y la clave sigue guardada.
        from ..cloud.keys import load_key

        pro.set_active(config.pro.enabled and bool(load_key()))
        self.voice_panel = VoicePanel(self)
        self.tests_panel = TestsPanel(self)
        self.pro_panel = ProPanel(self)
        # Lo que se bloquea o se desbloquea al cambiar de plan (ver app_view.apply_pro_look).
        self.plan_hooks: list = []
        app_view.build(self)
        app_view.apply_pro_look(self)
        self._refresh_hotkey_label()
        self.overlay = TranslationOverlay(
            self.root, config.roblox.overlay_seconds, self._overlay_anchor, self._overlay_visible
        )
        self.compose = ComposeBar(self.root, self._compose_preview, self._compose_submit, self._compose_closed)
        self._compose_multi = False  # la última vez mandaste a "todos los del chat"
        self.compose.on_target = self._use_language  # cambiar el idioma con Tab cambia también el de tu voz
        self.compose.on_toggle_plan = self._toggle_plan_in_game  # Ctrl+P: Basic ↔ Pro sin salir del juego
        self.compose.on_tone = self._remember_tone  # el tono elegido con ↑/↓ queda para la próxima
        from .. import layered
        from .toast import Toast

        layered.animate_with(self.root)  # las traducciones en el juego aparecen suave
        self.toast = Toast(self.root)
        # El micrófono virtual pasa a ser el de Windows ya (Roblox elige su micrófono al abrirse).
        self.voice_panel.early_start()
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
            self.root.after(2500, lambda: self.when_free(self.open_tutorial))
        # Conexión: se conecta al abrir y se desconecta sola si cerrás Roblox (vuelve sola cuando lo abrís).
        self.link = "desconectado"  # "conectando" | "conectado" | "desconectando" | "desconectado" | "error"
        self._closed_by_roblox = False
        self._roblox_seen = False
        self._roblox_gone_since: float | None = None
        self._redetect_chat = False
        self._connect()

    def show(self) -> None:
        """Muestra la ventana ya armada (una sola vez, completa), apareciendo suave."""
        from . import motion

        self.root.update_idletasks()
        self.root.attributes("-alpha", 0.0)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_set()  # que ninguna lista arranque con el texto resaltado
        motion.appear(self.root, rise=0, seconds=0.22)
        from .theme import prepare_gold_soon

        # El dorado de Pro se prepara de a poquito, con la ventana ya abierta: cambiar de plan después es instantáneo.
        self.root.after(1200, lambda: prepare_gold_soon(self.root))
        # ¿Falta algo para que ande todo? Se revisa en segundo plano y, si falta, se instala solo.
        self.root.after(1500, self._check_install)
        # Tu equipo (memoria, micrófonos, cuenta de Claude, internet…): Bubble se adapta y avisa si algo falta.
        self.root.after(3000, self.check_system)
        # ¿Se acaba de actualizar? ¿Hay una versión nueva? (ver update.py)
        self.root.after(2500, self._update_result)
        self.root.after(8000, self.check_update)

    # ================= instalar y desinstalar (ver install.py, uninstall.py, setup_window.py) =================
    def _check_install(self) -> None:
        from .. import install

        def work() -> None:
            try:
                missing = install.missing(only_required=True)
            except Exception:  # noqa: BLE001 - la revisión nunca impide usar Bubble
                log.debug("No se pudo revisar la instalación", exc_info=True)
                return
            if missing:
                self.events.put(("call", lambda: self.when_free(self.open_setup)))

        threading.Thread(target=work, name="bubble-revisar-instalacion", daemon=True).start()

    def open_setup(self) -> None:
        """«Preparar Bubble»: instala solo lo que falta (y muestra lo que ya está)."""
        from .setup_window import SetupWindow

        if getattr(self, "_setup_window", None) is not None and self._setup_window.window.winfo_exists():
            self._setup_window.window.lift()
            return
        self._setup_window = SetupWindow(self.root, lambda action: self.events.put(("call", action)),
                                         on_no_claude=lambda: self.open_no_claude())

    def open_uninstall(self) -> None:
        from .setup_window import UninstallWindow

        UninstallWindow(self.root, self._on_close)

    # ================= soporte, acerca de y tu equipo =================
    def open_support(self) -> None:
        """«Soporte»: un mensaje (título, qué pasó y cómo, imágenes) que le llega al creador (ver support.py)."""
        from ..capture.window_capture import WindowCapture
        from .support_window import SupportWindow

        if self._support is not None and self._support.window.winfo_exists():
            self._support.window.lift()
            return
        self._support = SupportWindow(self.root, grab_roblox=lambda: WindowCapture().whole())

    # ================= el cartel del micrófono (Inicio) =================
    def test_microphone(self) -> None:
        """«Probar mi micrófono»: a Pruebas, y arranca la prueba del micrófono."""
        self.page_var.set("pruebas")
        app_view._show_page(self)
        self.tests_panel._test_mic()

    def hide_mic_tip(self) -> None:
        """Se va el cartel del micrófono (lo cerraste, o la prueba dijo que tu micrófono anda bien) y no vuelve."""
        tip = getattr(self, "mic_tip", None)
        update_state(mic_tip_done=True)
        if tip and tip["outer"].winfo_exists():
            tip["outer"].destroy()
        self.mic_tip = None

    def open_about(self) -> None:
        from .about_window import AboutWindow

        AboutWindow(self.root, self.open_support)

    # ================= las ventanas que se abren solas, de a una =================
    def _dialog_open(self) -> bool:
        """¿Hay abierta alguna de las ventanas que se abren solas (o el tutorial)?"""
        tutorial = getattr(self, "tutorial", None)
        if tutorial is not None:
            try:
                if tutorial.win.winfo_exists():
                    return True
            except tk.TclError:
                pass
        for name in ("_setup_window", "_no_claude_window", "_update_window"):
            dialog = getattr(self, name, None)
            try:
                if dialog is not None and dialog.window.winfo_exists():
                    return True
            except tk.TclError:
                pass
        return False

    def when_free(self, action) -> None:
        """Abre una ventana que aparece sola cuando no haya otra abierta y no estés jugando. Antes, en la primera vez
        se encimaban el tutorial, «Preparar Bubble» y «Bubble necesita Claude». (Las que abrís vos, enseguida.)"""
        if self._in_game():
            self.root.after(30000, lambda: self.when_free(action))
        elif self._dialog_open():
            self.root.after(1500, lambda: self.when_free(action))
        else:
            action()

    # ================= sin Claude (ver cloud/agent.py y no_claude_window.py) =================
    def _ev_claude_access(self, payload) -> None:
        """Al conectarse: si hay Claude o no, y si se traduce con los créditos de Bubble Pro."""
        problem, has_key = payload
        self.claude_problem = problem
        cloud = problem in NO_CLAUDE_PROBLEMS and has_key
        was = self.cloud_translation
        self.cloud_translation = cloud
        if cloud:
            if not pro.active():
                self.set_pro(True)
            if not was:
                self._append("Sin Claude: Bubble Pro traduce con los créditos de Deepgram (~0,075 US$ por minuto con "
                             "mensajes; la conexión se corta sola cuando el chat está quieto). Conectá Claude para que "
                             "la traducción no gaste.\n", "info")
                self._set_status("✦ Traduciendo con Bubble Pro, sin Claude: gasta créditos de Deepgram. Conectá "
                                 "Claude para que no gaste.")
        self.pro_panel.apply_plan()
        self.pro_panel.refresh()

    def open_no_claude(self, reason: str = "") -> None:
        """Cómo seguir sin Claude: Bubble Pro con créditos, o conectar Claude. Nunca en medio de una partida."""
        from .no_claude_window import NoClaudeWindow

        if self._no_claude_window is not None and self._no_claude_window.window.winfo_exists():
            self._no_claude_window.window.lift()
            return
        if self._in_game():
            self.root.after(30000, lambda: self.open_no_claude(reason))
            return
        self._no_claude_window = NoClaudeWindow(self, reason or self.claude_problem or "sin_sesion")

    def use_cloud_translation(self) -> None:
        """Se guardó la clave de Bubble Pro sin tener Claude: se activa Pro y se reconecta traduciendo con él."""
        self.set_pro(True)
        self._refresh()

    def claude_connected(self) -> None:
        """Conectaste Claude: se reconecta traduciendo con tu suscripción (sin gastar créditos); Basic se desbloquea."""
        self._append("Claude conectado: la traducción vuelve a tu suscripción (no gasta créditos).\n", "info")
        self._refresh()

    def _cloud_fatal(self, error) -> None:
        """(hilo de la conexión) Deepgram sin crédito o con la clave mala, traduciendo sin Claude."""
        from ..cloud.errors import NoCredit

        reason = "sin_credito" if isinstance(error, NoCredit) else "clave"

        def show() -> None:
            self._append(f"Bubble Pro: {error}. Sin Claude no se puede traducir.\n", "error")
            self.open_no_claude(reason)

        self.events.put(("call", lambda: self.when_free(show)))

    # ================= actualizaciones (ver update.py y update_window.py) =================
    def check_update(self, force: bool = False) -> None:
        """¿Hay una versión nueva? Al abrir (como mucho cada 12 horas) o con «Buscar actualizaciones» (`force`)."""
        from .. import update

        if force:
            self._set_status("Buscando actualizaciones…")

        def work() -> None:
            try:
                release = update.check(force=force)
            except Exception:  # noqa: BLE001 - sin poder revisar, se sigue como siempre
                log.debug("No se pudo buscar actualizaciones", exc_info=True)
                release = None
            self.events.put(("call", lambda: self._on_update(release, force)))

        threading.Thread(target=work, name="bubble-actualizaciones", daemon=True).start()

    def _on_update(self, release, asked: bool) -> None:
        from .. import __version__, update

        self.update_release = release
        app_view.show_update_link(self, release)
        if release is None:
            if asked:
                self._set_status(f"✓ Estás al día: Bubble {__version__} es la última versión.")
            return
        if asked or update.should_offer(release):
            self._offer_update(release)

    def open_update(self) -> None:
        if self.update_release is not None:
            self._offer_update(self.update_release)

    def _offer_update(self, release) -> None:
        """Pregunta si actualizar, pero nunca en medio de una partida (la ventana le sacaría el foco al juego): espera
        a que Roblox no esté al frente."""
        from .update_window import UpdateWindow

        if self._update_window is not None and self._update_window.window.winfo_exists():
            self._update_window.window.lift()
            return
        if self._in_game() or self._dialog_open():
            self.root.after(30000 if self._in_game() else 3000, lambda: self._offer_update(release))
            return
        self._update_window = UpdateWindow(self.root, release, lambda action: self.events.put(("call", action)),
                                           self._on_close)

    def _update_result(self) -> None:
        """Si Bubble se acaba de actualizar, cómo salió."""
        from .. import update

        result = update.finished()
        if result is None:
            return
        if result.get("ok"):
            self._append(f"Bubble se actualizó a la {result.get('version')}.\n", "info")
            self._set_status(f"✓ ¡Listo! Bubble se actualizó a la {result.get('version')}.")
        else:
            self._append(f"No se pudo actualizar a la {result.get('version')}: {result.get('error')}\n", "error")
            self._set_status("No se pudo actualizar: sigue la versión que tenías (el detalle, en Actividad).")

    def check_system(self, internet: bool | None = None) -> None:
        """Revisa tu equipo en segundo plano, se adapta y avisa si algo impide que Bubble ande (ver system.py).
        `internet`: medirlo ya (None: una vez por día)."""
        from .. import system

        if self._checking_system:
            return
        self._checking_system = True

        def work() -> None:
            try:
                info, plan = system.check(internet)
            except Exception:  # noqa: BLE001 - revisar nunca impide usar Bubble
                log.warning("No se pudo revisar el equipo", exc_info=True)
                info = plan = None
            self.events.put(("call", lambda: self._on_system(info, plan)))

        threading.Thread(target=work, name="bubble-tu-equipo", daemon=True).start()

    def _on_system(self, info, plan) -> None:
        from .. import system

        self._checking_system = False
        first = self.system_info is None
        if info is not None:
            self.system_info, self.system_plan = info, plan
            missing = system.claude_problem(info.claude) in NO_CLAUDE_PROBLEMS
            if self.link in ("conectado", "error") and missing != (self.claude_problem in NO_CLAUDE_PROBLEMS):
                self._refresh()  # conectaste Claude (o se cerró la sesión): se vuelve a conectar como corresponde
            for change in system.adapt(plan):
                log.info("Adaptado a tu equipo: %s", change)
            problems = [item for item in plan.advice if item.level == "problema"]
            if problems and first:
                for problem in problems:
                    self._append(f"Tu equipo: {problem.text}\n", "error")
                self._set_status(f"⚠ {problems[0].text} (más en Pruebas › Tu equipo)")
        card = getattr(self.tests_panel, "equipment", None)
        if card is not None:
            card.show(info, plan)

    # ================= interfaz (ver app_view.py) =================
    def _region_text(self) -> str:
        if not self.saved_region:
            return "Todavía no vi el chat: abrí Roblox y lo busco solo."
        return "Ya sé dónde está el chat. Si cambiás de juego y no lo encuentro, buscalo en Ajustes."

    # ================= arranque y cierre =================
    async def _startup(self) -> None:
        from ..system import sessions_for
        from ..voice.checks import memory_gb

        from .. import system
        from ..cloud.keys import load_key

        # Con poca memoria, menos sesiones de Claude abiertas (cada una ~250 MB): Roblox necesita la suya.
        self.config.claude.pool_size = sessions_for(memory_gb(), self.config.claude.pool_size)
        # ¿Hay Claude? Si no, con la clave de Bubble Pro se traduce con los créditos de Deepgram.
        problem = await asyncio.to_thread(lambda: system.claude_problem(system.claude_status()))
        key = await asyncio.to_thread(load_key) if problem in system.NO_CLAUDE else ""
        self.events.put(("claude_access", (problem, bool(key))))
        if problem in system.NO_CLAUDE and not key:
            raise NoClaudeError("Falta Claude para traducir: activá Bubble Pro (con créditos gratis) o conectá Claude.")
        self.translator = await asyncio.to_thread(build_translator, self.config, key,
                                                  self._cloud_fatal if key else None)
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
        steps = build_steps(win32.describe_binding(self.config.roblox.hotkey), self._detect_chat, self._capture_test,
                            self.voice_panel.fix_windows)
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
        """Que las traducciones salgan en tus capturas y grabaciones: se lee la ventana de Roblox sola (si esta PC lo
        permite, ver capture/window_capture.py) y, si no, se muestran en las capturas con las teclas de Windows (ver
        screenshots.py)."""
        from .. import layered
        from ..capture import screen
        from ..screenshots import ScreenshotKeys

        if self.screenshots:
            self.screenshots.stop()
            self.screenshots = None
        wanted = self.config.appearance.in_screenshots

        def changed(active: bool) -> None:
            layered.set_capturable(active)  # (lo primero: ya no se lee la pantalla con ellas)
            self.events.put(("call", self._refresh_capture_label))

        screen.set_window_capture(wanted, changed)
        if wanted:
            self.screenshots = ScreenshotKeys(win32.roblox_is_foreground)
            self.screenshots.start()
        self._refresh_capture_label()

    def _refresh_capture_label(self) -> None:
        from ..capture import screen

        label = getattr(self, "capture_label", None)
        if label is None:
            return
        if not self.config.appearance.in_screenshots:
            text = "Las traducciones no salen en capturas ni grabaciones."
        elif screen.window_mode():
            text = ("✓ Salen en tus capturas y en las grabaciones de pantalla: Win + Shift + S, Impr Pant, la "
                    "Herramienta Recortes (Grabar), OBS con «Captura de pantalla», Discord. Lo que graba solo el juego "
                    "(el grabador de Roblox, Xbox Game Bar) no puede ver nada de lo que está encima.")
        else:
            text = ("Salen en tus capturas (Impr Pant, Win + Shift + S). En las grabaciones de pantalla, apenas Bubble "
                    "pueda leer la ventana de Roblox por separado: lo prueba solo cuando jugás.")
        label.configure(text=text)

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
        # Solo se lee con Roblox en primer plano (o escribiendo en la barra, que flota sobre el juego): si no, en esa
        # zona de la pantalla hay otra cosa.
        return roblox.resolve_chat_region(self.saved_region, require_foreground=not self.compose.showing)

    def _in_game(self) -> bool:
        """Estás jugando: Roblox al frente, o la barra para escribir abierta encima. Antes, al abrir la barra se
        escondían las traducciones del chat, de las burbujas y los subtítulos (Roblox dejaba de estar al frente)."""
        # (al arrancar, la voz ya pregunta esto antes de que exista la barra: sin la barra, cuenta solo Roblox)
        compose = getattr(self, "compose", None)
        return bool(compose is not None and compose.showing) or win32.roblox_is_foreground()

    def _overlay_visible(self) -> bool:
        return self._in_game()

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
        if self.saved_region and hwnd and roblox.region_outdated(self.saved_region, win32.client_rect(hwnd)):
            log.info("La zona del chat guardada es de un detector anterior (o casi toda la ventana): se busca de nuevo")
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
        if link == "error" and self.claude_problem in NO_CLAUDE_PROBLEMS and not self.cloud_translation:
            app_view.set_state(self, "bad", "Falta Claude para traducir: activá Bubble Pro o conectá Claude.",
                               "Sin Claude")
        elif link == "error":
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
        self._on_calibrated(guess.region.offset(client.left, client.top), client, detected=True)
        self._append(f"Encontré el chat de este juego ({guess.lines} mensajes a la vista).\n", "info")

    def _on_calibrated(self, rect: Rect | None, client: Rect | None, detected: bool = False) -> None:
        if rect is None:
            self._set_status("Listo, no cambié nada.")
            return
        roblox.save_chat_region(rect, client, detected)
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
            if enabled and self.translator is not None:
                self.translator.start_voice()  # el carril rápido (si ya estaba abierto, no hace nada)

        self.runner.submit(apply())
        self._set_status("Traduciendo las burbujas." if enabled else "Pausé la traducción de las burbujas.")

    def _game_area(self) -> Rect | None:
        """Pantalla del juego (para buscar burbujas), solo con Roblox en primer plano (o la barra para escribir)."""
        if not self._in_game():
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
            # La barra abre en lo último que usaste: si fue "todos los del chat", ese va primero.
            targets.insert(0 if self._compose_multi else 1, MULTI)
            labels[MULTI] = "Todos los del chat (" + " + ".join(c.upper() for c in in_chat[:MULTI_MAX]) + ")"
        return targets, labels

    def _use_language(self, target: str) -> None:
        """Mandaste (o dijiste) algo en este idioma: la próxima vez la barra y tu voz arrancan en ese. Antes, en
        "automático", se elegía de nuevo cada vez según el chat y cambiaba solo."""
        self._compose_multi = target == MULTI
        if self.translator is None:
            return
        if target == MULTI:
            # "Todos los del chat": tu voz no puede hablar en varios a la vez; usa el principal del chat.
            languages = self.translator.chat_languages()
            if languages:
                self.translator.remember_target(languages[0])
            return
        self.translator.remember_target(target)
        configured = self.config.user.outgoing_language
        if configured != "auto" and configured.split("-")[0] != target.split("-")[0]:
            self._set_outgoing(target.split("-")[0])  # elegiste otro con Tab: queda ese (y prepara su voz)
        else:
            self.voice_panel.language_changed()

    def _set_outgoing(self, code: str) -> None:
        """El idioma en que te leen y te escuchan (el mismo para la barra, tu voz y Ctrl+Enter)."""
        self.config.user.outgoing_language = code
        save_setting("user", "outgoing_language", code)
        if self.translator is not None:
            self.translator.last_target = None if code == "auto" else code.split("-")[0]
        for box in (getattr(self, "out_lang", None), self.voice_panel.lang_box):
            if box is not None:
                box.set(_choice(code))
        self.voice_panel.language_changed()

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
        self._use_language(target)
        if voice:
            self._compose_speak(key)
            return
        if key in self._compose_results:
            self._compose_send(key)  # la vista previa ya estaba lista: se manda al instante
            return
        self._send_when_ready = key
        self.compose.show_preview(key, "", "working")
        if key not in self._compose_running:
            self._compose_running.add(key)
            self.runner.submit(self._compose_translate(key))

    def _compose_speak(self, key: tuple[str, str, int]) -> None:
        """Ctrl+Enter: se pide directamente la versión para decir (sin "vc", "kkkk"…, que la voz leería letra por letra)
        por el carril rápido y se dice. Antes se esperaba primero la traducción del chat y después se pedía esta: dos
        pedidos seguidos."""
        self._send_when_ready = None
        self.compose.close(sent=True)
        if self.roblox_hwnd:
            win32.force_foreground(self.roblox_hwnd)  # volver al juego
        text, target, tone = key
        languages = (self.translator.chat_languages()[:MULTI_MAX] or ["en"]) if target == MULTI else [target]

        async def speak() -> None:
            results = await asyncio.gather(
                *(self.translator.translate_outgoing(text, language, tone=tone, spoken=True) for language in languages),
                return_exceptions=True)
            spoken = [(result.target_lang or language, result.translation)
                      for language, result in zip(languages, results)
                      if not isinstance(result, BaseException) and result.status != "error"
                      and result.translation.strip()]
            if not spoken:
                self.events.put(("status", "No se pudo traducir para decirlo en voz. Probá de nuevo."))
                return
            for _language, said in spoken:
                self.tracker.mark_sent(said)
            self.voice_panel.say(spoken, text)

        self.runner.submit(speak())

    def _compose_send(self, key: tuple[str, str, int]) -> None:
        self._send_when_ready = None
        messages, own, pairs = self._compose_results[key]
        self.compose.close(sent=True)
        if self._send_as_voice:
            self._compose_speak(key)
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
        save_setting("user", "language", self.config.user.language)
        outgoing = _code(self.out_lang.get())
        if outgoing != self.config.user.outgoing_language:
            self._set_outgoing(outgoing)

    def _remember_tone(self, tone: int) -> None:
        """Elegiste el tono en la barra (↑/↓): queda guardado, y Ajustes lo muestra."""
        self.config.user.tone = tone
        save_setting("user", "tone", tone)
        if getattr(self, "tone", None) is not None:
            self.tone.set(TONE_CHOICES[tone - 1])
            self.tone_hint.configure(text=TONE_HINTS[tone])

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
                self._handle(kind, payload)
        except queue.Empty:
            pass
        try:
            for kind, payload in latest.items():
                self._handle(kind, payload)
            if "bubbles" not in latest:
                self.bubbles.animate()  # entre detecciones, la traducción sigue a la burbuja
        finally:
            self.root.after(15, self._drain_events)  # rápido: las burbujas se mueven con la cámara

    def _handle(self, kind: str, payload) -> None:
        """Un evento que falla se anota y no frena a los demás (antes, un error dejaba la ventana sin novedades)."""
        handler = getattr(self, f"_ev_{kind}", None)
        if handler is None:
            return
        try:
            handler(payload)
        except Exception:  # noqa: BLE001
            log.exception("Falló el evento %s", kind)

    def _ev_chat_frame(self, frame) -> None:
        if self.inline_mode:
            self.inline_chat.render(frame, self._in_game())

    def _ev_chat_shift(self, dy: int) -> None:
        if self.inline_mode and self._in_game():
            self.inline_chat.shift(dy)

    def _ev_bubbles(self, payload) -> None:
        area, items = payload
        visible = self._in_game()
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
            # Ya, sin esperar: antes se esperaban 0,6 s por si el mismo mensaje llegaba por el chat. Ahora, si el chat
            # lo pide también, los dos comparten el mismo pedido (ver Translator._inflight): no se gasta dos veces.
            if self.inline_chat.find_text(entry.original) is not None:
                entry.status = "linked"
                return
            result = await self.translator.translate_incoming(entry.original, "", fast=True)
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
            if isinstance(error, NoClaudeError):
                self.when_free(self.open_no_claude)
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

    def _toggle_plan_in_game(self) -> None:
        """Ctrl+P en la barra para escribir: Basic ↔ Pro, con un aviso en el juego."""
        from ..cloud.keys import load_key

        area = None
        hwnd = win32.find_roblox_window()
        if hwnd:
            area = win32.client_rect(hwnd)
        if not load_key():
            self.toast.show("Para Bubble Pro, guardá tu clave en la ventana (✦ Pro)", False, area)
            return
        if self.cloud_translation and pro.active():
            self.toast.show("✦  Basic necesita Claude: conectalo en Bubble", True, area)
            return
        self.set_pro(not pro.active(), in_game=True)
        self.toast.show("✦  Bubble Pro activado" if pro.active() else "Bubble Basic", pro.active(), area)

    def set_pro(self, enabled: bool, reason: str = "", in_game: bool = False) -> None:
        """Prende o apaga Bubble Pro. `reason`: por qué se apagó solo (la clave dejó de andar, se quedó sin saldo).
        `in_game`: desde el juego (Ctrl+P): la ventana de Bubble se recolorea un momento después y sin la transición
        (que abre una ventanita y le podía sacar el foco a la barra para escribir)."""
        from ..cloud.keys import load_key

        if not enabled and not reason and self.cloud_translation:
            # Sin Claude, Pro es lo que traduce: Basic se desbloquea cuando conectes Claude.
            self._set_status("Basic necesita Claude: conectalo (✦ Pro › «Conectar Claude») para poder usarlo.")
            self.pro_panel.refresh()
            return
        enabled = bool(enabled and load_key())
        self.config.pro.enabled = enabled
        save_setting("pro", "enabled", enabled)
        if enabled != pro.active():
            pro.set_active(enabled)
            self.voice_panel.pro_changed()
            if in_game:
                self.root.after(350, lambda: app_view.apply_pro_look(self, animate=True))
            else:
                app_view.apply_pro_look(self, animate=True)
        self.pro_panel.refresh()
        if reason:
            self._set_status(f"Bubble Pro se apagó: {reason}. Sigo con el reconocimiento de tu PC.")
            self._append(f"Bubble Pro se apagó: {reason}.\n", "error")
        elif enabled:
            self._set_status("✦ Bubble Pro activado: las voces se entienden en la nube.")
        else:
            self._set_status("Bubble Pro apagado: las voces se entienden con tu PC.")

    def _ev_call(self, action) -> None:
        action()  # algo para hacer en el hilo de la ventana (página Pruebas)

    def _ev_voice_notice(self, text: str) -> None:
        self.voice_panel.notice(text)

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
