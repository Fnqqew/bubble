"""Abrir Bubble: primero un cartelito con el logo (aparece al instante) y, cuando la ventana está entera, la ventana.

Cargar todo (OCR, Claude, las piezas de la interfaz) toma un momento; antes se veía la ventana armarse por partes.
"""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path

LOGO = Path(__file__).resolve().parent.parent / "assets" / "bubble.png"


class Splash:
    def __init__(self, root: tk.Tk) -> None:
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        background = "#1c1c1c"
        self.window.configure(background=background)
        width, height = 300, 190
        x = (self.window.winfo_screenwidth() - width) // 2
        y = (self.window.winfo_screenheight() - height) // 2
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        try:
            from PIL import Image, ImageTk

            self._logo = ImageTk.PhotoImage(Image.open(LOGO).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS))
            tk.Label(self.window, image=self._logo, background=background).pack(pady=(34, 10))
        except Exception:  # noqa: BLE001 - sin logo, igual se abre
            pass
        tk.Label(self.window, text="Bubble", font=("Segoe UI Semibold", 16), foreground="#f3f3f3",
                 background=background).pack()
        self._status = tk.Label(self.window, text="Abriendo…", font=("Segoe UI", 9), foreground="#9aa0a6",
                                background=background)
        self._status.pack(pady=(2, 0))
        self._round()
        self.window.update()

    def status(self, text: str) -> None:
        """En qué anda (así no parece colgado mientras prepara todo)."""
        try:
            self._status.configure(text=text)
            self.window.update_idletasks()
        except tk.TclError:
            pass

    def _round(self) -> None:
        import ctypes

        try:
            self.window.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.window.winfo_id()) or self.window.winfo_id()
            corner = ctypes.c_int(2)  # esquinas redondeadas de Windows 11
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        self.window.destroy()


# Los símbolos de la interfaz: no están en la letra de Windows (Segoe UI).
SYMBOLS = "→│✦✓›●↑↓✗⚠🔒✕↔▾↻🎙🔊○◐▶‹！。、"
FONTS = ("TkDefaultFont", "SunValleyCaptionFont", "SunValleyBodyStrongFont", "SunValleySubtitleFont",
         "SunValleyBodyFont", "SunValleyTitleFont")


def warm_symbols(root: tk.Misc) -> None:
    """La primera vez que se dibuja un símbolo que la letra no tiene, Windows busca en todas las letras cuál lo tiene:
    el candado 🔒 tardaba ~0,5 s y la página que lo mostraba se trababa al abrirla. Se buscan acá, una sola vez, mientras
    se ve el cartel de «Abriendo…»."""
    import tkinter.font as tkfont

    for name in FONTS:  # (cada letra busca por su cuenta: las que usa la ventana, no las 25 que hay)
        try:
            tkfont.nametofont(name, root).measure(SYMBOLS)
        except tk.TclError:
            pass


def preload() -> None:
    """Lo más pesado de cargar, mientras se ve el cartel: después, con la ventana abierta, cada una de estas cargas la
    congelaba (la conexión con Claude, 2 s; leer el chat, 0,7 s; la voz, ~0,3 s cada parte). Mientras Python carga un
    módulo, la ventana no puede dibujarse."""
    import importlib

    # (dxcam no: al cargarse prepara la placa de video con COM, y quedaría atado a este hilo)
    for name in ("claude_agent_sdk", "scipy.ndimage", "onnxruntime", "faster_whisper", "websockets.asyncio.client"):
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - lo que no esté (la parte de voz sin instalar) se carga cuando haga falta
            pass
    try:  # y los modelos chicos de la voz (¿hay alguien hablando? ¿quién?), que se comparten
        from ..voice import speakers, vad
        from ..voice.models import models_dir

        vad.session()
        model = models_dir() / "speaker" / speakers.MODEL_NAME
        if model.exists():
            speakers.session(model)
    except Exception:  # noqa: BLE001 - se crean cuando hagan falta
        pass


def launch(config) -> None:
    from .. import win32

    win32.enable_dpi_awareness()
    from .. import i18n

    i18n.use(i18n.choose(config.user.ui_language))  # la ventana, en tu idioma (antes de armarla)
    i18n.install()
    root = tk.Tk()
    root.withdraw()
    splash = Splash(root)
    loading = threading.Thread(target=preload, name="bubble-precarga", daemon=True)
    loading.start()
    from .main_window import BubbleWindow  # lo pesado se carga mientras se ve el cartel

    splash.status("Armando la ventana…")
    window = BubbleWindow(config, root=root)
    splash.status("Preparando la conexión y la voz…")
    warm_symbols(root)

    def ready() -> None:
        # Mientras Tk acomoda y dibuja la ventana suelta a Python: la precarga siguió en paralelo y casi siempre ya
        # terminó. Recién ahí se va el cartel y aparece la ventana, entera y sin trabarse.
        loading.join(timeout=15)
        splash.close()

    window.show(ready=ready)
    window.run()
