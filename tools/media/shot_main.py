"""Captura páginas de la ventana (transparente y sin tomar el foco: nunca sale lo que hay detrás).

Uso: shot_main.py inicio,voz,ajustes <carpeta> [oscuro|claro]  →  <carpeta>/page_<tema>_<página>.png
"""
import sys
import time
import tkinter as tk

sys.path.insert(0, "src")
sys.path.insert(0, sys.argv[2])
from printwin import print_window, show_quietly  # noqa: E402

from bubble import win32  # noqa: E402
from bubble.config import load_config  # noqa: E402
from bubble.translate.base import TranslationResult  # noqa: E402
from bubble.ui import app_view  # noqa: E402
from bubble.ui import main_window as mw  # noqa: E402

theme_name = sys.argv[3] if len(sys.argv) > 3 else "oscuro"


async def no_startup(self):
    return None


mw.BubbleWindow._startup = no_startup
mw.BubbleWindow._start_hotkey = lambda self: self._refresh_hotkey_label()
mw.BubbleWindow._sync_shortcut = lambda self: None
mw.BubbleWindow.open_tutorial = lambda self: None
mw.BubbleWindow._drain_events = lambda self: None

config = load_config()
config.appearance.theme = theme_name
if "pro" in sys.argv[4:]:  # con Bubble Pro activo (dorado): una clave de mentira, no se conecta a nada
    import bubble.cloud.keys

    bubble.cloud.keys.load_key = lambda: "clave-de-prueba"
    config.pro.enabled = True
    theme_name += "_pro"
root = tk.Tk()
root.withdraw()
root.attributes("-alpha", 0.0)
app = mw.BubbleWindow(config, root=root)
root.geometry("600x740+0+0")
app.ready = True
app._refresh_header(True)
app._set_status("")
app.roblox_status.configure(text="Roblox está abierto.", foreground=app_view.widgets.palette()["good"])
app.voice_panel.show_devices(["Microphone (Realtek(R) Audio)"], False)
for direction, speaker, original, translation, src in [
    ("in", "lucas_br", "vlw mano, tmj", "gracias hermano, estamos", "pt"),
    ("in", "xXShadowXx", "ngl this obby is mid", "posta, este obby está re meh", "en"),
    ("out", "Vos", "dale, esperame en la torre", "sure, wait for me at the tower", "es"),
]:
    app._log_result(direction, speaker, TranslationResult(original, translation, src, "es-AR", "claude",
                                                          "translated", 0.6, 1.4))
app.perf_label.configure(text="AMD Ryzen 5 (12 hilos) · captura por GPU · modo media")
hwnd = win32.toplevel_hwnd(root)
root.deiconify = lambda: None
show_quietly(hwnd)
for page in sys.argv[1].split(","):
    app.page_var.set(page)
    app_view._show_page(app)
    for _ in range(8):
        root.update()
        time.sleep(0.05)
    print_window(hwnd).save(f"{sys.argv[2]}/page_{theme_name}_{page}.png")
root.destroy()
print("ok")
