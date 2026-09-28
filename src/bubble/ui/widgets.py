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


class Scrollable(ttk.Frame):
    """Una página con barra de desplazamiento (para Ajustes, que es larga). Se mueve con la ruedita."""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background=palette()["bg"])
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
            self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def recolor(self) -> None:
        self.canvas.configure(background=palette()["bg"])
