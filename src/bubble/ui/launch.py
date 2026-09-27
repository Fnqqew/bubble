"""Abrir Bubble: primero un cartelito con el logo (aparece al instante) y, cuando la ventana está entera, la ventana.

Cargar todo (OCR, Claude, las piezas de la interfaz) toma un momento; antes se veía la ventana armarse por partes.
"""

from __future__ import annotations

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
        tk.Label(self.window, text="Abriendo…", font=("Segoe UI", 9), foreground="#9aa0a6",
                 background=background).pack(pady=(2, 0))
        self._round()
        self.window.update()

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


def launch(config) -> None:
    from .. import win32

    win32.enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    splash = Splash(root)
    from .main_window import BubbleWindow  # lo pesado se carga mientras se ve el cartel

    window = BubbleWindow(config, root=root)
    splash.close()
    window.show()
    window.run()
