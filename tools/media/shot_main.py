"""Captura la ventana principal real de Bubble con ejemplos, sin Claude, sin atajos y sin robar el foco."""
import sys
import time
import tkinter as tk

sys.path.insert(0, "src")
sys.path.insert(0, sys.argv[2])
from printwin import print_window, show_quietly

from bubble import roblox, win32
from bubble.config import Config
from bubble.translate.base import TranslationResult
from bubble.ui import main_window as mw

_tk_init = tk.Tk.__init__


def quiet_tk(self, *a, **k):
    _tk_init(self, *a, **k)
    self.withdraw()
    self.attributes("-alpha", 0.0)


tk.Tk.__init__ = quiet_tk
roblox.load_chat_region = lambda: {"relative": True, "x": 8, "y": 60, "w": 468, "h": 232}


async def no_startup(self):
    return None


mw.BubbleWindow._startup = no_startup
mw.BubbleWindow._start_hotkey = lambda self: self._refresh_hotkey_label()
mw.BubbleWindow._poll_roblox = lambda self: None
mw.BubbleWindow._sync_shortcut = lambda self: None
mw.BubbleWindow.open_tutorial = lambda self: None
mw.BubbleWindow._drain_events = lambda self: None

config = Config()
config.user.language = "es-AR"
app = mw.BubbleWindow(config)
root = app.root
root.geometry("1000x870+0+0")
app.roblox_status.configure(text="Roblox abierto · leyendo el chat")
app.perf_label.configure(text="Tu PC: AMD Ryzen 5 (12 hilos) · AMD Radeon Graphics · captura por GPU · modo media"
                              " · lee chat cada 0.12 s, burbujas cada 0.30 s")
app.voice_panel.status.configure(text="Voz lista · reconocimiento en tu PC (Whisper) · voces Piper · Claude solo traduce el texto")
app.live.configure(text="")
app.log.configure(state="normal")
app.log.delete("1.0", "end")
app.log.configure(state="disabled")
app._append("Chat encontrado automáticamente (9 mensajes a la vista).\n", "info")
samples = [
    ("in", "lucas_br", "vlw mano, tmj 🤝", "gracias hermano, estamos 🤝", "pt", 1.42, 0.61, "translated"),
    ("in", "xXShadowXx", "ngl this obby is mid", "posta, este obby está re meh", "en", 1.18, 0.55, "translated"),
    ("in", "Ana_luz", "mdr jsp comment on saute là", "jaja ni idea cómo se salta ahí", "fr", 1.63, 0.70, "translated"),
    ("out", "Vos", "dale, esperame en la torre", "sure, wait for me at the tower", "es", 1.31, 0.58, "translated"),
]
for direction, speaker, original, translation, src, total, ttft, status in samples:
    target = "en" if direction == "out" else "es-AR"
    app._log_result(direction, speaker, TranslationResult(original, translation, src, target, "claude", status,
                                                          ttft, total))
app._append("🔊 oi, alguém quer ir comigo no boss?\n   → che, ¿alguien quiere venir conmigo al jefe?\n", "in")
app._set_status("Listo · traduciendo con tu suscripción de Claude")

root.update_idletasks()
app.log.yview_moveto(0)
hwnd = win32.toplevel_hwnd(root)
show_quietly(hwnd)
for _ in range(10):
    root.update()
    time.sleep(0.05)
print_window(hwnd).save(sys.argv[1])
root.destroy()
print("ok")
