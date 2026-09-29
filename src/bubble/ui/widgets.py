"""Piezas de la ventana: tarjetas, filas con interruptor, títulos e íconos (tema Sun Valley de Windows 11)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

ICON_FONT = ("Segoe Fluent Icons", 15)
# Íconos de Segoe Fluent Icons (Windows 11; en Windows 10 los mismos códigos están en Segoe MDL2 Assets).
ICONS = {
    "chat": "", "bubbles": "", "listen": "", "mic": "", "globe": "",
    "keyboard": "", "settings": "", "home": "", "history": "", "info": "",
    "check": "", "warning": "", "palette": "", "people": "", "speed": "",
    "play": "", "download": "", "roblox": "",
}

PALETTES = {
    "oscuro": {"bg": "#1c1c1c", "card": "#2b2b2b", "text": "#f3f3f3", "muted": "#a0a4ab", "faint": "#6d7178",
               "good": "#6ccb5f", "warn": "#f7b955", "bad": "#ff99a4", "accent": "#57a9ff",
               "in": "#8fc7ff", "out": "#8be28b", "meta": "#80848c", "info": "#f7c86a", "error": "#ff9aa2"},
    "claro": {"bg": "#fafafa", "card": "#ffffff", "text": "#1a1a1a", "muted": "#5f6368", "faint": "#9aa0a6",
              "good": "#0f7b0f", "warn": "#9d5d00", "bad": "#c42b1c", "accent": "#005fb8",
              "in": "#0b5cad", "out": "#107c10", "meta": "#80868b", "info": "#8a5300", "error": "#c42b1c"},
}


def palette() -> dict[str, str]:
    """Los colores del tema. Con Bubble Pro el acento es dorado: se nota que estás usando el Pro."""
    from .. import pro
    from . import theme

    colors = PALETTES.get(theme.current(), PALETTES["oscuro"])
    if pro.active():
        colors = {**colors, "accent": pro.gold(), "pro": pro.gold()}
    return colors


def icon_font() -> tuple:
    import tkinter.font

    families = set(tkinter.font.families())
    name = "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"
    return name, ICON_FONT[1]


def card(parent, title: str = "", subtitle: str = "", pady: tuple = (0, 6)) -> ttk.Frame:
    """Una sección: título, explicación chiquita y lo que va adentro. Sin cajas (minimalista): separa el aire y una
    línea finita. Devuelve el marco de adentro."""
    outer = ttk.Frame(parent)
    outer.pack(fill="x", pady=pady)
    if len(parent.winfo_children()) > 1:
        ttk.Separator(outer).pack(fill="x", pady=(4, 14))
    if title:
        ttk.Label(outer, text=title, font="SunValleyBodyStrongFont").pack(anchor="w")
    if subtitle:
        ttk.Label(outer, text=subtitle, font="SunValleyCaptionFont", foreground=palette()["muted"],
                  wraplength=460, justify="left").pack(anchor="w", pady=(2, 0))
    inner = ttk.Frame(outer)
    inner.pack(fill="x", pady=(8 if title or subtitle else 0, 6))
    return inner


def switch_row(parent, icon: str, title: str, description: str, variable: tk.BooleanVar,
               command: Callable[[], None], pady: tuple = (4, 4)) -> ttk.Frame:
    """Fila: ícono, título, explicación chiquita y un interruptor a la derecha."""
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=pady)
    ttk.Label(row, text=ICONS.get(icon, icon), font=icon_font(), foreground=palette()["accent"],
              width=2).pack(side="left", padx=(0, 12))
    ttk.Checkbutton(row, variable=variable, command=command, style="Switch.TCheckbutton").pack(side="right")
    texts = ttk.Frame(row)
    texts.pack(side="left", fill="x", expand=True)
    ttk.Label(texts, text=title, font="SunValleyBodyStrongFont").pack(anchor="w")
    ttk.Label(texts, text=description, font="SunValleyCaptionFont", foreground=palette()["muted"],
              wraplength=330, justify="left").pack(anchor="w")
    return row


def label_row(parent, text: str, pady: tuple = (6, 2)) -> ttk.Frame:
    """Fila con un rótulo a la izquierda; lo que se agregue después va a la derecha."""
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=pady)
    ttk.Label(row, text=text).pack(side="left")
    return row


def muted(parent, text: str = "", wrap: int = 440, **pack) -> ttk.Label:
    label = ttk.Label(parent, text=text, font="SunValleyCaptionFont", foreground=palette()["muted"],
                      wraplength=wrap, justify="left")
    label.pack(anchor="w", **(pack or {"pady": (4, 0)}))
    return label


def segmented(parent, variable: tk.StringVar, options: dict[str, str], command: Callable[[], None]) -> ttk.Frame:
    """Botones que se eligen de a uno (como un interruptor de varias opciones)."""
    box = ttk.Frame(parent)
    for value, text in options.items():
        ttk.Radiobutton(box, text=text, value=value, variable=variable, command=command,
                        style="Toggle.TButton").pack(side="left", padx=(0, 6))
    return box


def dialog(root: tk.Misc, title: str, width: int = 520) -> tuple[tk.Toplevel, ttk.Frame]:
    """Una ventana secundaria del tema (escondida hasta `present`) y su cuerpo con márgenes."""
    window = tk.Toplevel(root)
    window.withdraw()
    window.title(title)
    window.resizable(False, False)
    window.configure(background=palette()["bg"])
    if isinstance(root, tk.Tk) and root.winfo_viewable():
        window.transient(root)
    body = ttk.Frame(window, padding=(26, 22, 26, 18))
    body.pack(fill="both", expand=True)
    body.configure(width=width)
    return window, body


def present(window: tk.Toplevel, root: tk.Misc) -> None:
    """Muestra la ventana centrada sobre Bubble (o en la pantalla), apareciendo suave."""
    from . import motion, theme

    window.update_idletasks()
    width, height = window.winfo_reqwidth(), window.winfo_reqheight()
    if root.winfo_viewable():
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 3
    else:
        x, y = (window.winfo_screenwidth() - width) // 2, (window.winfo_screenheight() - height) // 3
    window.geometry(f"+{max(0, x)}+{max(0, y)}")
    theme.title_bar(window)
    window.deiconify()
    motion.appear(window, rise=0, seconds=0.2)


DIM = 0.7  # qué tan difuminado queda un texto bloqueado (0 = igual, 1 = invisible)
CONTROLS = (ttk.Button, ttk.Checkbutton, ttk.Radiobutton, ttk.Entry, ttk.Combobox, ttk.Scale, ttk.Spinbox)


def mix(color_a: str, color_b: str, amount: float) -> str:
    """Un color entre `color_a` (0) y `color_b` (1)."""
    a = [int(color_a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(color_b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def _hex(widget: tk.Misc, color: str) -> str:
    if color.startswith("#") and len(color) == 7:
        return color
    red, green, blue = widget.winfo_rgb(color or palette()["text"])
    return f"#{red // 256:02x}{green // 256:02x}{blue // 256:02x}"


def dim(container: tk.Misc, dimmed: bool, animate: bool = True, only: list | None = None, reason: str = "") -> None:
    """Bloquea (o desbloquea) todo lo de adentro: los textos se difuminan suave hacia el fondo y los controles no se
    pueden tocar. Sirve para lo que no corresponde en este momento (lo de Pro en Basic, lo de Basic en Pro, lo de tu
    voz si no está prendida…): se ve que existe, pero no se puede usar ni probar por error. `only`: solo esas partes
    del contenedor (por ejemplo, una columna de una tabla).

    Cada bloqueo tiene su motivo (`reason`; si no, el contenedor): algo queda difuminado mientras quede al menos uno,
    y al salir el último vuelve exactamente a como estaba. Antes, dos bloqueos encimados (tu voz apagada y el modo
    directo, sobre el mismo botón) se pisaban: sacar uno podía dejar un botón bloqueado para siempre, o un texto
    normal con su control bloqueado."""
    from . import motion

    reason = reason or f"contenedor-{id(container)}"
    reasons = container.__dict__.setdefault("dim_reasons", set())
    container.dimmed = bool(reasons)
    if (reason in reasons) == dimmed:
        return
    (reasons.add if dimmed else reasons.discard)(reason)
    container.dimmed = bool(reasons)
    background = palette()["bg"]
    labels, controls = [], []

    def collect(widget: tk.Misc) -> None:
        for child in widget.winfo_children():
            if isinstance(child, ttk.Label):
                labels.append(child)
            elif isinstance(child, CONTROLS):
                controls.append(child)
            collect(child)

    if only is None:
        collect(container)
    else:
        for widget in only:
            if isinstance(widget, ttk.Label):
                labels.append(widget)
            elif isinstance(widget, CONTROLS):
                controls.append(widget)
            collect(widget)

    def lock(widget: tk.Misc) -> bool | None:
        """Suma o saca este motivo. Devuelve True si recién se bloqueó, False si recién se liberó, None si no cambió."""
        locks = widget.__dict__.setdefault("dim_locks", set())
        before = bool(locks)
        (locks.add if dimmed else locks.discard)(reason)
        return None if bool(locks) == before else bool(locks)

    for control in controls:
        changed = lock(control)
        if changed is True:
            control.locked_before = control.instate(["disabled"])  # (si ya estaba bloqueado por otra cosa, sigue)
            control.state(["disabled"])
        elif changed is False and not getattr(control, "locked_before", False):
            control.state(["!disabled"])
    moves = []
    for label in labels:
        changed = lock(label)
        current = _hex(label, str(label.cget("foreground")))
        if changed is True:
            label.dim_color = getattr(label, "dim_color", None) or current
            moves.append((label, current, mix(label.dim_color, background, DIM)))
        elif changed is False and getattr(label, "dim_color", None):
            original = label.dim_color
            del label.dim_color
            moves.append((label, current, original))

    def step(progress: float) -> None:
        for label, start, end in moves:
            label.configure(foreground=mix(start, end, progress))

    if animate and moves:
        motion.animate(container, 0.22, step)
    else:
        step(1.0)


def set_enabled(control: ttk.Widget, enabled: bool) -> None:
    """Habilita o bloquea un control sin pisar un difuminado (ver dim): si está difuminado, queda bloqueado y se
    habilita (o no) recién cuando se libere. Antes, al terminar una prueba se habilitaban todos sus botones, aunque
    estuvieran en una tarjeta difuminada."""
    if control.__dict__.get("dim_locks"):
        control.locked_before = not enabled
        return
    control.state(["!disabled"] if enabled else ["disabled"])


WHEEL_PX = 64  # píxeles por "clic" de la ruedita
GLIDE = 0.4  # en cada paso se recorre esta parte de lo que falta (se frena suave al llegar)


class Scrollable(ttk.Frame):
    """Una página con barra de desplazamiento (para Ajustes, que es larga). Se mueve con la ruedita, suave y de a
    píxeles (también con touchpad: antes los movimientos chicos no la movían y los grandes la hacían saltar)."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._goal: float | None = None  # hasta dónde se está desplazando (píxeles desde arriba)
        self._gliding = None
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=palette()["bg"],
                                yscrollincrement=1)
        bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=(0, 0, 14, 0))
        self._window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=bar.set)
        self.bar = bar
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body.bind("<Configure>", lambda _e: self._fit())
        self.canvas.bind("<Configure>", lambda e: (self.canvas.itemconfigure(self._window, width=e.width),
                                                   self._fit()))
        self.bind_all("<MouseWheel>", self._wheel, add="+")

    def _fit(self) -> None:
        """La barra aparece solo si el contenido no entra."""
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        overflow = self.body.winfo_reqheight() > self.canvas.winfo_height() + 2
        if overflow and not self.bar.winfo_ismapped():
            self.bar.pack(side="right", fill="y", before=self.canvas)
        elif not overflow and self.bar.winfo_ismapped():
            self.bar.pack_forget()
            self.canvas.yview_moveto(0)

    def _wheel(self, event) -> None:
        if not self.winfo_ismapped() or not self.bar.winfo_ismapped():
            return
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            widget = widget.master
        if widget is self:
            self.scroll_by(-event.delta / 120 * WHEEL_PX)

    def scroll_by(self, pixels: float) -> None:
        """Desplaza la página, deslizándose (si ya se estaba moviendo, sigue desde adonde iba)."""
        top = self.canvas.canvasy(0)
        bottom = max(0.0, self.body.winfo_reqheight() - self.canvas.winfo_height())
        start = self._goal if self._goal is not None else top
        self._goal = min(max(0.0, start + pixels), bottom)
        if self._gliding is None:
            self._glide()

    def _glide(self) -> None:
        top = self.canvas.canvasy(0)
        remaining = (self._goal if self._goal is not None else top) - top
        if abs(remaining) < 1:
            self._gliding = self._goal = None
            return
        step = int(remaining * GLIDE) or (1 if remaining > 0 else -1)
        self.canvas.yview_scroll(step, "units")
        if self.canvas.canvasy(0) == top:  # llegó al borde
            self._gliding = self._goal = None
            return
        self._gliding = self.after(10, self._glide)

    def recolor(self) -> None:
        self.canvas.configure(background=palette()["bg"])

    def scroll_to_top(self) -> None:
        if self._gliding is not None:
            self.after_cancel(self._gliding)
        self._gliding = self._goal = None
        self.canvas.yview_moveto(0)
