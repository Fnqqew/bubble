"""Ventanas que se muestran encima de Roblox: overlay de traducciones, cuadro para escribir y calibración."""

from __future__ import annotations

import functools
import threading
import time
import tkinter as tk
import zlib
from dataclasses import dataclass
from typing import Callable

from .. import win32
from ..geometry import Rect
from ..translate.base import TONE_NAMES, clamp_tone
from ..translate.languages import DISPLAY_NAMES

BG = "#16181c"
FG = "#f2f2f2"
MUTED = "#9aa0a6"
NAME_COLORS = ["#7cc4ff", "#ffb86b", "#8be28b", "#ff8fa3", "#d0a6ff", "#ffe07a"]
FONT = ("Segoe UI", 11)
FONT_BOLD = ("Segoe UI", 11, "bold")


def _name_color(name: str) -> str:
    return NAME_COLORS[zlib.crc32(name.casefold().encode()) % len(NAME_COLORS)]


@dataclass(eq=False)
class _Row:
    frame: tk.Frame
    label: tk.Label
    expires: float


class TranslationOverlay:
    """Lista de traducciones recientes que desaparecen solas. No recibe clics ni foco.

    Solo se ve mientras `visible_when()` es True (Roblox en primer plano): si salís del juego se oculta
    y vuelve a aparecer al volver.
    """

    MAX_LINES = 6
    WIDTH = 420

    def __init__(
        self,
        root: tk.Tk,
        seconds: float,
        anchor: Callable[[], tuple[int, int] | None],
        visible_when: Callable[[], bool] = lambda: True,
    ) -> None:
        self.seconds = seconds
        self.anchor = anchor
        self.visible_when = visible_when
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.9)
        self.win.configure(bg=BG)
        self.body = tk.Frame(self.win, bg=BG, padx=10, pady=6)
        self.body.pack(fill="both", expand=True)
        self._lines: list[_Row] = []
        self._styled = False
        root.after(500, self._tick)

    def show(self, speaker: str, text: str, color: str | None = None, muted: bool = False) -> _Row:
        """Agrega una fila al final (el orden de llegada es el orden del chat) y devuelve su referencia."""
        frame = tk.Frame(self.body, bg=BG)
        if speaker:
            tk.Label(frame, text=f"{speaker}:", font=FONT_BOLD, fg=color or _name_color(speaker), bg=BG).pack(
                side="left", anchor="n"
            )
        label = tk.Label(
            frame, text=text, font=FONT, fg=MUTED if muted else FG, bg=BG, wraplength=self.WIDTH - 40, justify="left"
        )
        label.pack(side="left", anchor="n", padx=(4, 0))
        frame.pack(fill="x", anchor="w", pady=1)
        row = _Row(frame, label, time.monotonic() + self.seconds)
        self._lines.append(row)
        while len(self._lines) > self.MAX_LINES:
            self._lines.pop(0).frame.destroy()
        self._show_window()
        return row

    def set_text(self, row: _Row | None, text: str, muted: bool = False) -> bool:
        """Cambia el texto de una fila (ej. el original por la traducción) sin moverla de lugar."""
        if row is None or row not in self._lines:
            return False
        row.label.configure(text=text, fg=MUTED if muted else FG)
        row.expires = time.monotonic() + self.seconds
        return True

    def remove(self, row: _Row | None) -> None:
        if row is not None and row in self._lines:
            self._lines.remove(row)
            row.frame.destroy()
        if not self._lines:
            self.win.withdraw()

    def _show_window(self) -> None:
        if not self.visible_when():
            self.win.withdraw()
            return
        position = self.anchor()
        if position:
            self.win.geometry(f"+{position[0]}+{position[1]}")
        if self.win.state() == "withdrawn":
            self.win.deiconify()
        self.win.lift()
        if not self._styled:
            self.win.update_idletasks()
            win32.make_overlay(win32.toplevel_hwnd(self.win), click_through=True)
            self._styled = True

    def _tick(self) -> None:
        now = time.monotonic()
        for row in [row for row in self._lines if row.expires <= now]:
            row.frame.destroy()
            self._lines.remove(row)
        if self._lines:
            self._show_window()  # se oculta si saliste de Roblox y reaparece al volver
        else:
            self.win.withdraw()
        self.win.after(250, self._tick)


class ComposeBar:
    """Barra para escribir en tu idioma: una sola línea, minimalista, flotando abajo al centro del juego.

    Mientras escribís muestra cómo va a quedar la traducción; con Enter la traduce (si todavía no estaba lista) y la
    manda al chat de Roblox. Tab cambia el idioma (el chip de la izquierda), ↑/↓ el tono (los puntos de la derecha)
    y Esc cierra (lo que escribiste vuelve si la abrís enseguida).
    """

    WIDTH = 640
    PREVIEW_DELAY_MS = 650  # se traduce para la vista previa cuando dejás de escribir un momento
    # Con Tab o ↑/↓ se pide enseguida (antes esperaba lo mismo que al escribir: el idioma nuevo tardaba en aparecer).
    # Un poquito igual, por si pasás por varios seguidos.
    SWITCH_DELAY_MS = 150
    KEEP_DRAFT_S = 120
    BG = "#16171b"
    FIELD = BG  # una sola superficie: sin cajas adentro de cajas
    LINE = "#25272d"
    TEXT = "#f1f3f5"
    HINT = "#5f656e"
    PREVIEW = "#8fb8ff"
    PRO_PREVIEW = "#f5d58a"  # con Bubble Pro, la traducción que vas a mandar se ve dorada
    CHIP_BG = (35, 50, 74)
    CHIP_FG = (159, 198, 255)

    def __init__(
        self,
        root: tk.Tk,
        on_preview: Callable[[str, str, int], None],
        on_submit: Callable[..., None],
        on_close: Callable[[], None],
    ) -> None:
        self.on_preview = on_preview  # pedir la traducción de (texto, idioma, tono) para mostrarla
        self.on_submit = on_submit  # traducir (si hace falta) y enviar: (texto, idioma, tono, voice=en voz)
        self.on_close = on_close  # se cerró sin enviar
        # Cambiaste el idioma con Tab (o con un clic en el chip): es el mismo para tu voz y para chat a voz.
        self.on_target: Callable[[str], None] | None = None
        # Ctrl+P: pasar de Basic a Pro (o al revés) sin salir del juego.
        self.on_toggle_plan: Callable[[], None] | None = None
        # Cambiaste el tono con ↑/↓: queda ese para la próxima (antes volvía al de Ajustes al reabrir la barra).
        self.on_tone: Callable[[int], None] | None = None
        self.targets: list[str] = []
        self.labels: dict[str, str] = {}
        self.index = 0
        self.tone = 3
        self.busy = False  # traduciendo para enviar: no se edita
        # Abierta (se puede leer desde cualquier hilo; `visible` pregunta a Tk y eso solo vale en el de la ventana).
        self.showing = False
        self._after: str | None = None
        self._dots: str | None = None
        self._requested: tuple[str, str, int] | None = None
        self._cycling = False  # ya usaste Tab: se adelanta la traducción del idioma siguiente
        self._draft = ""
        self._draft_at = 0.0
        self._x = self._bottom = 0
        self._images: dict[str, object] = {}  # PhotoImage vivas (Tk no guarda la referencia)
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=self.BG)
        body = tk.Frame(self.win, bg=self.BG, padx=18, pady=14)
        body.pack(fill="both", expand=True)

        field = tk.Frame(body, bg=self.FIELD)
        field.pack(fill="x")
        self.chip = tk.Label(field, bg=self.FIELD, bd=0, cursor="hand2")
        self.chip.pack(side="left", padx=(0, 10))
        self.chip.bind("<Button-1>", self._next_target)
        self.tone_view = tk.Label(field, bg=self.FIELD, bd=0)
        self.tone_view.pack(side="right", padx=(10, 0))
        holder = tk.Frame(field, bg=self.FIELD)
        holder.pack(side="left", fill="x", expand=True)
        self.entry = tk.Entry(holder, font=("Segoe UI", 15), bg=self.FIELD, fg=self.TEXT, insertbackground=self.TEXT,
                              relief="flat", bd=0, highlightthickness=0, disabledbackground=self.FIELD,
                              disabledforeground="#9aa0a6")
        self.entry.pack(fill="x", ipady=2)
        self.placeholder = tk.Label(holder, text="Escribí en tu idioma…", font=("Segoe UI", 15), bg=self.FIELD,
                                    fg=self.HINT)
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())

        self.preview = tk.Label(body, font=("Segoe UI", 12), fg=self.PREVIEW, bg=self.BG, anchor="w", justify="left",
                                wraplength=self.WIDTH - 40)
        tk.Frame(body, bg=self.LINE, height=1).pack(fill="x", pady=(12, 0))
        footer = tk.Frame(body, bg=self.BG)
        footer.pack(fill="x", pady=(8, 0))
        # En qué plan estás: "✦ PRO" dorado o "BASIC". Ctrl+P (o un clic acá) cambia sin salir del juego.
        self.plan = tk.Label(footer, font=("Segoe UI Semibold", 9), bg=self.BG, cursor="hand2")
        self.plan.pack(side="right")
        self.plan.bind("<Button-1>", lambda _e: self._toggle_plan())
        self.hint = tk.Label(footer, font=("Segoe UI", 9), fg=self.HINT, bg=self.BG, anchor="w")
        self.hint.pack(side="left", fill="x", expand=True)

        self.entry.bind("<Return>", self._submit)
        self.entry.bind("<KP_Enter>", self._submit)
        self.entry.bind("<Control-Return>", lambda _e: self._submit(voice=True))  # decirlo en voz
        self.entry.bind("<Escape>", self._cancel)
        self.entry.bind("<Tab>", self._next_target)
        self.entry.bind("<Up>", lambda _e: self._change_tone(+1))
        self.entry.bind("<Down>", lambda _e: self._change_tone(-1))
        self.entry.bind("<Control-p>", lambda _e: self._toggle_plan())
        self.entry.bind("<Control-P>", lambda _e: self._toggle_plan())
        self.entry.bind("<KeyRelease>", self._on_edit)
        self.entry.bind("<FocusOut>", lambda _e: self.win.after(200, self._close_if_left))
        self._styled = False

    @property
    def visible(self) -> bool:
        return self.win.state() != "withdrawn"

    def current(self) -> tuple[str, str, int]:
        return self.entry.get().strip(), self.targets[self.index] if self.targets else "", self.tone

    def open(self, targets: list[str], area: Rect | None, tone: int = 3, labels: dict[str, str] | None = None) -> None:
        self.targets = targets
        self.labels = labels or {}
        self.index = 0
        self.tone = clamp_tone(tone)
        self.busy = False
        self._requested = None
        self._cycling = False
        self.entry.configure(state="normal")
        self.entry.delete(0, "end")
        if self._draft and time.monotonic() - self._draft_at < self.KEEP_DRAFT_S:
            self.entry.insert(0, self._draft)
        self._render_target()
        self._show_preview("")
        self._update_placeholder()
        if area:
            self._x, self._bottom = area.left + (area.width - self.WIDTH) // 2, area.bottom - 90
        else:
            self._x = (self.win.winfo_screenwidth() - self.WIDTH) // 2
            self._bottom = self.win.winfo_screenheight() - 140
        self._fit()
        self.showing = True
        self.win.deiconify()
        self.win.lift()
        from . import motion

        height = self.win.winfo_reqheight()
        motion.appear(self.win, self._x, self._bottom - height, rise=12, seconds=0.18,
                      alive=lambda: self.showing)  # sube desvaneciéndose
        if not self._styled:
            self._styled = True
            _round_corners(self.win)
        self.win.after(10, self._grab_focus)
        if self.entry.get().strip():
            self._schedule_preview()

    def _fit(self) -> None:
        """Alto justo para el contenido; si la traducción ocupa más líneas, la barra crece hacia arriba."""
        self.win.update_idletasks()
        height = self.win.winfo_reqheight()
        self.win.geometry(f"{self.WIDTH}x{height}+{self._x}+{self._bottom - height}")

    def show_preview(self, key: tuple[str, str, int], text: str, state: str) -> None:
        """Traducción de `key` para mostrar: state = "working" (llegando), "done" o "error"."""
        if not self.visible or key != self.current():
            return
        if state == "error":
            self._show_preview(f"⚠  {text}", "#ff8fa3")
        elif state == "working":
            self._show_preview(text, "#6f8fbf", working=True)
        else:
            self._show_preview(text)
            self._prefetch()

    def unlock(self) -> None:
        """Falló la traducción al enviar: se puede corregir y volver a intentar."""
        self.busy = False
        self.entry.configure(state="normal")
        self.entry.focus_set()

    def close(self, sent: bool = False) -> None:
        for job in (self._after, self._dots):
            if job:
                self.win.after_cancel(job)
        self._after = self._dots = None
        text = self.entry.get().strip()
        self._draft, self._draft_at = ("", 0.0) if sent else (text, time.monotonic())
        self.busy = False
        self.showing = False
        if sent:
            self.win.withdraw()  # ya: el mensaje se escribe en el juego enseguida
            return
        from . import motion

        # Se desvanece (si la volvés a abrir en el medio, queda abierta).
        motion.vanish(self.win, self.win.withdraw, alive=lambda: not self.showing)

    def _preview_color(self) -> str:
        from .. import pro

        return self.PRO_PREVIEW if pro.active() else self.PREVIEW

    def _toggle_plan(self) -> str:
        if self.on_toggle_plan and not self.busy:
            self.on_toggle_plan()
            self._render_target()
        return "break"

    # --- interno
    def _grab_focus(self) -> None:
        win32.force_foreground(win32.toplevel_hwnd(self.win))
        self.win.focus_force()
        self.entry.focus_set()
        self.entry.icursor("end")

    def _show_preview(self, text: str, color: str | None = None, working: bool = False) -> None:
        if self._dots:
            self.win.after_cancel(self._dots)
            self._dots = None
        if working:
            self._animate_dots(text, 0)
        else:
            self.preview.configure(text=f"→  {text}" if text and color is None else text,
                                   fg=color or self._preview_color())
        if text or working:
            self.preview.pack(fill="x", pady=(10, 0), after=self.entry.master.master)
        else:
            self.preview.pack_forget()
        self._fit()

    def _animate_dots(self, text: str, step: int) -> None:
        dots = "·" * (step % 3 + 1)
        self.preview.configure(text=f"→  {text}  {dots}" if text else f"traduciendo  {dots}", fg="#6f8fbf")
        self._dots = self.win.after(320, lambda: self._animate_dots(text, step + 1))

    def _update_placeholder(self) -> None:
        if self.entry.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=3, y=0, relheight=1)

    def _render_target(self) -> None:
        code = self.targets[self.index] if self.targets else ""
        text = "TODOS" if code == MULTI_TARGET else code.split("-")[0].upper()
        from .. import pro

        chip_bg, chip_fg = ((70, 57, 27), pro.GOLD_RGB) if pro.active() else (self.CHIP_BG, self.CHIP_FG)
        self._images["chip"] = _chip_image(text, chip_bg, chip_fg, _rgb(self.FIELD))
        self.plan.configure(text="✦ PRO" if pro.active() else "BASIC  ·  Ctrl+P",
                            fg="#%02x%02x%02x" % pro.GOLD_RGB if pro.active() else self.HINT)
        self.chip.configure(image=self._images["chip"])
        self._images["tone"] = _tone_image(self.tone, _rgb(self.FIELD), pro.GOLD_RGB if pro.active() else None)
        self.tone_view.configure(image=self._images["tone"])
        name = self.labels.get(code) or DISPLAY_NAMES.get(code, code)
        self.hint.configure(text=f"Enter  chat en {name.split(' (')[0].lower()}   ·   Ctrl+Enter  en voz   ·   "
                                 f"Tab  idioma   ·   ↑↓  tono: {TONE_NAMES[self.tone].lower()}   ·   Esc")

    def _on_edit(self, event=None) -> None:
        self._update_placeholder()
        if self.busy or (event is not None and event.keysym in ("Return", "KP_Enter", "Escape", "Tab", "Up", "Down")):
            return
        self._schedule_preview()

    def _schedule_preview(self, delay_ms: int | None = None) -> None:
        if self._after:
            self.win.after_cancel(self._after)
        self._after = self.win.after(delay_ms or self.PREVIEW_DELAY_MS, self._request_preview)
        if not self.entry.get().strip():
            self._show_preview("")

    def _request_preview(self) -> None:
        self._after = None
        key = self.current()
        if key[0] and key != self._requested:
            self._requested = key
            self._show_preview("", working=True)
            self.on_preview(*key)

    def _prefetch(self) -> None:
        """Recorriendo idiomas con Tab: la traducción del siguiente se pide ya, así aparece al instante."""
        text, _target, tone = self.current()
        if self._cycling and text and len(self.targets) > 1:
            following = self.targets[(self.index + 1) % len(self.targets)]
            if following != MULTI_TARGET:  # ("todos los del chat" son varias traducciones: solo si llegás)
                self.on_preview(text, following, tone)

    def _next_target(self, _event=None) -> str:
        if self.targets and not self.busy:
            self.index = (self.index + 1) % len(self.targets)
            self._cycling = True
            self._render_target()
            self._schedule_preview(self.SWITCH_DELAY_MS)
            if self.on_target:
                self.on_target(self.targets[self.index])
        return "break"

    def _change_tone(self, delta: int) -> str:
        tone = clamp_tone(self.tone + delta)
        if tone != self.tone and not self.busy:
            self.tone = tone
            self._render_target()
            self._schedule_preview(self.SWITCH_DELAY_MS)
            if self.on_tone:
                self.on_tone(tone)
        return "break"

    def _submit(self, _event=None, voice: bool = False) -> str:
        if self.busy:
            return "break"
        key = self.current()
        if not key[0]:
            self._cancel()
            return "break"
        if self._after:
            self.win.after_cancel(self._after)
            self._after = None
        self.busy = True
        self.entry.configure(state="disabled")
        self.on_submit(*key, voice=voice)
        return "break"

    def _cancel(self, _event=None) -> str:
        self.close()
        self.on_close()
        return "break"

    def _close_if_left(self) -> None:
        # Si hiciste clic en el juego (u otra ventana), la barra se cierra; lo escrito vuelve si la reabrís.
        if self.visible and not self.busy and self.win.focus_displayof() is None:
            self.close()


MULTI_TARGET = "*"  # "todos los idiomas del chat" (ver main_window.MULTI)


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


@functools.lru_cache(maxsize=4)
def _chip_font(size: int):
    """Segoe UI Semibold (leerla del disco en cada Tab tardaba)."""
    from PIL import ImageFont

    try:
        return ImageFont.truetype("seguisb.ttf", size)
    except OSError:
        return ImageFont.load_default(size)


def _chip_image(text: str, background: tuple, foreground: tuple, surface: tuple):
    """Chip redondeado con el idioma (dibujado suave, a 3x, y achicado)."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 3
    font = _chip_font(12 * scale)
    width = int(font.getlength(text)) + 22 * scale
    height = 26 * scale
    image = Image.new("RGB", (width, height), surface)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, width - 1, height - 1), height // 2, fill=background)
    draw.text((width / 2, height / 2), text, font=font, fill=foreground, anchor="mm")
    return ImageTk.PhotoImage(image.resize((width // scale, height // scale), Image.Resampling.LANCZOS))


def _tone_image(tone: int, surface: tuple, color: tuple | None = None):
    """Cinco puntos: cuántos llenos = qué tan informal (1 neutro … 5 jerga)."""
    from PIL import Image, ImageDraw, ImageTk

    scale = 3
    size, gap = 7 * scale, 5 * scale
    image = Image.new("RGB", (5 * size + 4 * gap, size), surface)
    draw = ImageDraw.Draw(image)
    for index in range(5):
        x = index * (size + gap)
        fill = (color or (143, 184, 255)) if index < tone else (52, 55, 63)
        draw.ellipse((x, 0, x + size - 1, size - 1), fill=fill)
    return ImageTk.PhotoImage(image.resize((image.width // scale, image.height // scale), Image.Resampling.LANCZOS))


def _round_corners(window: tk.Misc) -> None:
    """Esquinas redondeadas y borde sutil de Windows 11 (en Windows 10 queda recta, igual se ve bien)."""
    import ctypes

    try:
        hwnd = win32.toplevel_hwnd(window)
        round_corners = ctypes.c_int(2)  # DWMWCP_ROUND
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(round_corners), 4)
        border = ctypes.c_uint(0x003A3530)  # COLORREF 0x00BBGGRR: #30353a
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), 4)
    except Exception:  # noqa: BLE001 - es solo el aspecto
        pass


class HotkeyCaptureDialog:
    """Ventanita que espera la tecla o botón del mouse que el usuario quiere usar como atajo."""

    def __init__(self, root: tk.Tk, on_done: Callable[[str | None], None]) -> None:
        self.on_done = on_done
        self.cancel = threading.Event()
        self.result: list[str | None] = []
        self.win = tk.Toplevel(root, bg="#ffffff")
        self.win.title("Elegí el atajo")
        self.win.resizable(False, False)
        self.win.transient(root)
        self.win.attributes("-topmost", True)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)
        tk.Label(
            self.win, text="Apretá la tecla o el botón del mouse\nque quieras usar para escribir en Roblox",
            font=("Segoe UI", 13, "bold"), fg="#1f2328", bg="#ffffff", justify="center",
        ).pack(padx=28, pady=(22, 8))
        tk.Label(
            self.win,
            text="Sirve cualquier tecla (también con Ctrl, Alt o Shift), la rueda del mouse\n"
                 "o sus botones laterales. El clic izquierdo y el derecho no se pueden usar.\n\nEsc para cancelar.",
            font=("Segoe UI", 10), fg="#6b7280", bg="#ffffff", justify="center",
        ).pack(padx=28, pady=(0, 20))
        self.win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - self.win.winfo_width()) // 2
        y = root.winfo_rooty() + (root.winfo_height() - self.win.winfo_height()) // 3
        self.win.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.win.focus_force()
        self.win.grab_set()  # las teclas no llegan a la ventana principal mientras elegís
        threading.Thread(target=self._capture, name="bubble-capture", daemon=True).start()
        self.win.after(50, self._poll)

    def _capture(self) -> None:
        self.result.append(win32.capture_binding(self.cancel))

    def _poll(self) -> None:
        if self.result:
            self._finish(self.result[0])
        elif self.win.winfo_exists():
            self.win.after(50, self._poll)

    def _cancel(self) -> None:
        self.cancel.set()
        self._finish(None)

    def _finish(self, spec: str | None) -> None:
        if not self.win.winfo_exists():
            return
        self.cancel.set()
        self.win.grab_release()
        self.win.destroy()
        self.on_done(spec)


class CalibrationOverlay:
    """Pantalla semitransparente para marcar con el mouse dónde está el chat de Roblox."""

    def __init__(self, root: tk.Tk, area: Rect, on_done: Callable[[Rect | None], None]) -> None:
        self.area = area
        self.on_done = on_done
        self.start: tuple[int, int] | None = None
        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.attributes("-alpha", 0.4)
        self.win.geometry(f"{area.width}x{area.height}+{area.left}+{area.top}")
        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.create_text(
            area.width // 2, area.height // 2, fill="white", font=("Segoe UI", 20, "bold"),
            text="Arrastrá un rectángulo sobre el chat de Roblox\n(incluí varias líneas de mensajes)\n\nEsc para cancelar",
            justify="center",
        )
        self.rect_id = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.win.bind("<Escape>", lambda _e: self._finish(None))
        self.win.after(10, self._grab_focus)

    def _grab_focus(self) -> None:
        win32.force_foreground(win32.toplevel_hwnd(self.win))
        self.win.focus_force()

    def _press(self, event) -> None:
        self.start = (event.x, event.y)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#4af", width=3)

    def _drag(self, event) -> None:
        if self.start and self.rect_id:
            self.canvas.coords(self.rect_id, *self.start, event.x, event.y)

    def _release(self, event) -> None:
        if not self.start:
            return
        rect = Rect.from_points(*self.start, event.x, event.y)
        if rect.width < 40 or rect.height < 30:
            return  # demasiado chico: probablemente un clic
        self._finish(rect.offset(self.area.left, self.area.top))

    def _finish(self, rect: Rect | None) -> None:
        self.win.destroy()
        self.on_done(rect)
