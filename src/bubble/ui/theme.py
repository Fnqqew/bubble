"""Aspecto de la ventana: tema de Windows 11 (Sun Valley), oscuro o claro, con la barra de título a juego."""

from __future__ import annotations

import ctypes
import logging
import tkinter as tk

log = logging.getLogger(__name__)

MUTED = "#9aa0a6"  # textos secundarios (se mantiene por compatibilidad; ver widgets.palette())
BACKGROUND = "#1c1c1c"
TEXT = "#e8eaed"
_current = "oscuro"


def current() -> str:
    return _current


def apply_theme(root: tk.Tk, name: str = "oscuro") -> bool:
    """Aplica el tema moderno ("oscuro" o "claro"). Si no está instalado, se mantiene el tema predeterminado."""
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
    """Ajusta la barra de título al tema, oscura o clara (Windows 10 20H1+ / 11)."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1 if _current == "oscuro" else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20; 19 en versiones anteriores)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001
        pass


dark_title_bar = title_bar  # nombre anterior, por compatibilidad

GOLD_HUE = 42 / 360  # tono del dorado de Bubble Pro
# Imagen del tema -> (copia original, copia dorada). Se preparan una sola vez, en segundo plano tras abrir la ventana,
# de modo que cambiar de plan solo copia píxeles. Regenerar cada imagen como PNG en cada cambio bloqueaba la interfaz
# unos 0,15 s.
_pairs: dict[str, tuple[tk.PhotoImage, tk.PhotoImage]] = {}
_checked: set[str] = set()  # imágenes ya revisadas (con azul o sin él)
_state = {"root": None, "gold": False}


def _theme_images(root: tk.Misc) -> list[str]:
    try:
        names = [str(name) for name in root.tk.call("image", "names")]
    except tk.TclError:
        return []
    # excluye los íconos de Tk, las imágenes de Bubble y sus copias; solo quedan las del tema
    return [name for name in names if not name.startswith(("::", "pyimage", "bubble_gold"))]


def _golden(data: bytes) -> bytes | None:
    """Devuelve la misma imagen (PNG) con los azules convertidos a dorado, o None si no contiene azul."""
    import io

    import numpy as np
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    alpha = image.getchannel("A") if image.mode == "RGBA" else None
    hsv = np.array(image.convert("RGB").convert("HSV"))
    hue, saturation = hsv[..., 0].astype(np.float32) / 255, hsv[..., 1].astype(np.float32) / 255
    blue = (hue > 0.5) & (hue < 0.7) & (saturation > 0.15)
    if alpha is not None:
        blue &= np.array(alpha) > 0
    if not blue.any():
        return None
    hsv[..., 0][blue] = round(GOLD_HUE * 255)
    tinted = Image.fromarray(hsv, "HSV").convert("RGB")
    if alpha is not None:
        tinted.putalpha(alpha)
    out = io.BytesIO()
    tinted.save(out, "PNG")
    return out.getvalue()


def _reset_if_new(root: tk.Misc) -> None:
    top = root.winfo_toplevel()
    if _state["root"] is not top:
        _pairs.clear()
        _checked.clear()
        _state["root"], _state["gold"] = top, False


def _prepare_one(root: tk.Misc, name: str) -> None:
    import base64

    _checked.add(name)
    try:
        golden = _golden(bytes(root.tk.call(name, "data", "-format", "png")))
        if golden is None:
            return
        counter = len(_pairs)
        original = tk.PhotoImage(master=root, name=f"bubble_gold_o{counter}")
        root.tk.call(original.name, "copy", name, "-compositingrule", "set")
        gold = tk.PhotoImage(master=root, name=f"bubble_gold_g{counter}", format="png",
                             data=base64.b64encode(golden).decode("ascii"))
        _pairs[name] = (original, gold)
        if _state["gold"]:
            root.tk.call(name, "copy", gold.name, "-compositingrule", "set")
    except (tk.TclError, OSError, ValueError):
        log.debug("No se pudo preparar el dorado de %s", name, exc_info=True)


def prepare_gold(root: tk.Misc, budget_s: float | None = None) -> bool:
    """Prepara la versión dorada de las imágenes pendientes (todas, o las que entren en `budget_s`). Devuelve True si
    terminó.
    """
    import time

    _reset_if_new(root)
    start = time.perf_counter()
    for name in _theme_images(root):
        if name in _checked:
            continue
        _prepare_one(root, name)
        if budget_s is not None and time.perf_counter() - start > budget_s:
            return False
    return True


def prepare_gold_soon(root: tk.Misc) -> None:
    """Procesa de a tandas cortas para no bloquear la ventana: ~8 ms cada 40 ms."""
    if not prepare_gold(root, budget_s=0.008):
        root.after(40, lambda: prepare_gold_soon(root))


def tint_accent(root: tk.Misc, gold: bool) -> int:
    """Con Bubble Pro, los botones destacados, interruptores, casillas y barras del tema pasan de azul a dorado; sin
    Pro, vuelven al azul. El tema dibuja todo con imágenes (las de ambos temas se cargan juntas). Devuelve la
    cantidad de imágenes modificadas (0 si ya estaban en ese estado).
    """
    _reset_if_new(root)
    if _state["gold"] == gold:
        prepare_gold(root)  # completa las imágenes que falten
        return 0
    _state["gold"] = gold
    prepare_gold(root)
    for name, (original, golden) in _pairs.items():
        try:
            root.tk.call(name, "copy", (golden if gold else original).name, "-compositingrule", "set")
        except tk.TclError:
            log.debug("No se pudo cambiar el color de %s", name, exc_info=True)
    return len(_pairs)


def strong_font() -> str | tuple:
    """Fuente destacada del tema (misma familia y tamaño que el resto de la ventana)."""
    import tkinter.font

    return "SunValleyBodyStrongFont" if "SunValleyBodyStrongFont" in tkinter.font.names() else ("Segoe UI", 10, "bold")


def scrolled_text(parent: tk.Misc, **options) -> tuple[tk.Frame, tk.Text]:
    """Texto con la barra de desplazamiento del tema (la de ScrolledText queda blanca en el tema oscuro)."""
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
