"""Abrir Bubble: primero se muestra un cartel con el logo (aparece al instante) y, cuando la ventana está completa, se
muestra la ventana.

Cargar todo (OCR, Claude, las piezas de la interfaz) toma un momento; el cartel evita que se vea la ventana armándose
por partes.
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
        """Indica qué está haciendo la aplicación, para que no parezca bloqueada mientras se prepara."""
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


# Símbolos de la interfaz que no están en la fuente de Windows (Segoe UI).
SYMBOLS = "→│✦✓›●↑↓✗⚠🔒✕↔▾↻🎙🔊○◐▶‹！。、"
FONTS = ("TkDefaultFont", "SunValleyCaptionFont", "SunValleyBodyStrongFont", "SunValleySubtitleFont",
         "SunValleyBodyFont", "SunValleyTitleFont")


def warm_symbols(root: tk.Misc) -> None:
    """La primera vez que se dibuja un símbolo ausente de la fuente, Windows busca en todas las fuentes cuál lo tiene:
    el candado 🔒 tardaba ~0,5 s y bloqueaba la página que lo mostraba al abrirla. Por eso se buscan acá, una sola
    vez, mientras se muestra el cartel «Abriendo…».
    """
    import tkinter.font as tkfont

    for name in FONTS:  # cada fuente busca por su cuenta: solo las que usa la ventana, no las 25
        try:
            tkfont.nametofont(name, root).measure(SYMBOLS)
        except tk.TclError:
            pass


def preload() -> None:
    """Carga lo más pesado mientras se muestra el cartel: hacerlo con la ventana abierta la congelaba en cada carga
    (conexión con Claude, 2 s; lectura del chat, 0,7 s; voz, ~0,3 s por parte). Mientras Python carga un módulo, la
    ventana no puede dibujarse.
    """
    import importlib

    # dxcam no se precarga: al importarse inicializa la placa de video con COM y quedaría atado a este hilo.
    # (bubble.voice antes que faster_whisper: le da el módulo vacío en lugar de PyAV)
    for name in ("claude_agent_sdk", "onnxruntime", "bubble.voice", "faster_whisper", "websockets.asyncio.client"):
        try:
            importlib.import_module(name)
        except Exception:  # noqa: BLE001 - lo que no esté (la parte de voz sin instalar) se carga cuando haga falta
            pass
    try:  # y los modelos chicos de voz (detección de habla, identificación), compartidos
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

    i18n.use(i18n.choose(config.user.ui_language))  # idioma de la ventana, antes de crearla
    i18n.install()
    root = tk.Tk()
    root.withdraw()
    splash = Splash(root)
    loading = threading.Thread(target=preload, name="bubble-precarga", daemon=True)
    loading.start()
    from .main_window import BubbleWindow  # la carga pesada ocurre mientras se muestra el cartel

    splash.status("Preparando la ventana…")
    window = BubbleWindow(config, root=root)
    splash.status("Preparando la conexión y la voz…")
    warm_symbols(root)

    def ready() -> None:
        # Mientras Tk acomoda y dibuja la ventana se libera a Python: la precarga continúa en paralelo y casi siempre ya
        # terminó. Recién entonces se cierra el cartel y aparece la ventana, completa y sin bloqueos.
        loading.join(timeout=15)
        splash.close()

    window.show(ready=ready)
    window.run()
