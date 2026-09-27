"""Aspecto de la ventana: tema oscuro de Windows 11 (Sun Valley) y barra de título oscura."""

from __future__ import annotations

import ctypes
import logging
import tkinter as tk

log = logging.getLogger(__name__)

MUTED = "#9aa0a6"  # textos secundarios
BACKGROUND = "#1c1c1c"
TEXT = "#e8eaed"


def apply_theme(root: tk.Tk) -> bool:
    """Tema oscuro moderno. Si el tema no está instalado, queda el de siempre (y se avisa en el log)."""
    try:
        import sv_ttk

        sv_ttk.set_theme("dark")
    except Exception:  # noqa: BLE001 - es solo el aspecto
        log.info("Tema Sun Valley no disponible: se usa el tema normal")
        return False
    root.update_idletasks()
    dark_title_bar(root)
    return True


def strong_font() -> str | tuple:
    """Letra destacada del tema (la misma familia y tamaño que el resto de la ventana)."""
    import tkinter.font

    return "SunValleyBodyStrongFont" if "SunValleyBodyStrongFont" in tkinter.font.names() else ("Segoe UI", 10, "bold")


def dark_title_bar(window: tk.Misc) -> None:
    """Barra de título oscura (Windows 10 20H1+ / 11), para que combine con la ventana."""
    try:
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 en versiones viejas)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


def scrolled_text(parent: tk.Misc, **options) -> tuple[tk.Frame, tk.Text]:
    """Texto con barra de desplazamiento del tema (la de ScrolledText queda blanca en el tema oscuro)."""
    from tkinter import ttk

    frame = tk.Frame(parent, background=BACKGROUND)
    text = tk.Text(frame, **options)
    bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    style_text(text)
    return frame, text


def style_text(widget: tk.Text) -> None:
    widget.configure(background=BACKGROUND, foreground=TEXT, insertbackground=TEXT, relief="flat",
                     borderwidth=0, highlightthickness=0, padx=10, pady=8)
