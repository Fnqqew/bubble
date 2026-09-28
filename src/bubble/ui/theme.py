"""Aspecto de la ventana: tema de Windows 11 (Sun Valley), oscuro o claro, con la barra de título a juego."""

from __future__ import annotations

import ctypes
import logging
import tkinter as tk

log = logging.getLogger(__name__)

MUTED = "#9aa0a6"  # textos secundarios (compatibilidad: las piezas nuevas usan widgets.palette())
BACKGROUND = "#1c1c1c"
TEXT = "#e8eaed"
_current = "oscuro"


def current() -> str:
    return _current


def apply_theme(root: tk.Tk, name: str = "oscuro") -> bool:
    """Tema moderno ("oscuro" o "claro"). Si el tema no está instalado, queda el de siempre."""
    global _current
    _current = "claro" if name == "claro" else "oscuro"
    try:
        import sv_ttk

        sv_ttk.set_theme("light" if _current == "claro" else "dark")
    except Exception:  # noqa: BLE001 - es solo el aspecto
        log.info("Tema Sun Valley no disponible: se usa el tema normal")
        return False
    title_bar(root)
    return True


def title_bar(window: tk.Misc) -> None:
    """Barra de título oscura o clara (Windows 10 20H1+ / 11), para que combine con la ventana."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1 if _current == "oscuro" else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 en versiones viejas)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


dark_title_bar = title_bar  # nombre viejo

GOLD_HUE = 42 / 360  # el tono del dorado de Bubble Pro
_originals: dict[str, bytes] = {}  # imágenes del tema antes de pintarlas de dorado (para volver)


def tint_accent(root: tk.Misc, gold: bool) -> int:
    """Con Bubble Pro, los botones destacados, interruptores, casillas y barras del tema pasan del azul al dorado; sin
    Pro, vuelven al azul. El tema dibuja todo con imágenes (las de los dos temas se cargan juntas): se cambia el tono
    de los píxeles azules de cada una. Devuelve cuántas imágenes cambió."""
    import io

    import numpy as np
    from PIL import Image

    changed = 0
    try:
        names = [str(name) for name in root.tk.call("image", "names")]
    except tk.TclError:
        return 0
    for name in names:
        if name.startswith("::") or name.startswith("pyimage"):
            continue  # íconos de Tk y las imágenes de Bubble (no son del tema)
        try:
            if not gold:
                if name in _originals:
                    _put_png(root, name, _originals.pop(name))
                    changed += 1
                continue
            if name in _originals:
                continue  # ya está dorada
            data = bytes(root.tk.call(name, "data", "-format", "png"))
            image = Image.open(io.BytesIO(data))
            alpha = image.getchannel("A") if image.mode == "RGBA" else None
            hsv = np.array(image.convert("RGB").convert("HSV"))
            hue, saturation = hsv[..., 0].astype(np.float32) / 255, hsv[..., 1].astype(np.float32) / 255
            blue = (hue > 0.5) & (hue < 0.7) & (saturation > 0.15)
            if alpha is not None:
                blue &= np.array(alpha) > 0
            if not blue.any():
                continue
            hsv[..., 0][blue] = round(GOLD_HUE * 255)
            tinted = Image.fromarray(hsv, "HSV").convert("RGB")
            if alpha is not None:
                tinted.putalpha(alpha)
            out = io.BytesIO()
            tinted.save(out, "PNG")
            _originals[name] = data
            _put_png(root, name, out.getvalue())
            changed += 1
        except (tk.TclError, OSError, ValueError):
            log.debug("No se pudo cambiar el color de %s", name, exc_info=True)
    return changed


def _put_png(root: tk.Misc, name: str, data: bytes) -> None:
    root.tk.call(name, "blank")
    root.tk.call(name, "put", data, "-format", "png")


def strong_font() -> str | tuple:
    """Letra destacada del tema (la misma familia y tamaño que el resto de la ventana)."""
    import tkinter.font

    return "SunValleyBodyStrongFont" if "SunValleyBodyStrongFont" in tkinter.font.names() else ("Segoe UI", 10, "bold")


def scrolled_text(parent: tk.Misc, **options) -> tuple[tk.Frame, tk.Text]:
    """Texto con barra de desplazamiento del tema (la de ScrolledText queda blanca en el tema oscuro)."""
    from tkinter import ttk

    frame = ttk.Frame(parent)
    text = tk.Text(frame, **options)
    bar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=bar.set)
    bar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)
    style_text(text)
    return frame, text


def style_text(widget: tk.Text) -> None:
    from .widgets import palette

    colors = palette()
    widget.configure(background=colors["card"], foreground=colors["text"], insertbackground=colors["text"],
                     relief="flat", borderwidth=0, highlightthickness=0, padx=14, pady=12)
