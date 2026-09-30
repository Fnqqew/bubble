"""Ventana «Preparar Bubble» (la real) en cada etapa de la instalación, para la guía (docs/INSTALACION.md). Es
transparente y no toma el foco, de modo que nunca captura lo que hay detrás en la pantalla. No instala nada.

Uso: shot_install.py <carpeta> tools/media  →  <carpeta>/setup_<etapa>.png y setup.json (posición de los botones)
"""
import ctypes
import json
import sys
import time
import tkinter as tk

sys.path.insert(0, "src")
sys.path.insert(0, sys.argv[2])
from printwin import print_window, show_quietly  # noqa: E402

from bubble import install  # noqa: E402
from bubble.ui import motion, setup_window, theme, widgets  # noqa: E402

out = sys.argv[1]
motion.appear = lambda *args, **kwargs: None


def present(window, _root):
    window.update_idletasks()
    window.attributes("-alpha", 0.0)
    window.geometry("+0+0")
    theme.title_bar(window)
    show_quietly(ctypes.windll.user32.GetParent(window.winfo_id()))


widgets.present = present
root = tk.Tk()
root.withdraw()
root.attributes("-alpha", 0.0)
root.iconbitmap(default="src/bubble/assets/bubble.ico")
theme.apply_theme(root, "oscuro")
steps = [install.Step(s.key, s.title, s.detail, lambda: False, None, s.required, s.action, s.after)
         for s in install.steps()]
window = setup_window.SetupWindow(root, lambda action: root.after(0, action), steps, auto=False)
hwnd = ctypes.windll.user32.GetParent(window.window.winfo_id())
boxes = {}


def snap(name):
    for _ in range(12):
        root.update()
        time.sleep(0.02)
    print_window(hwnd).save(f"{out}/setup_{name}.png")
    frame_left = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(frame_left))
    for child in window.window.winfo_children()[0].winfo_children():
        for widget in child.winfo_children():
            if widget.winfo_class() == "TButton" and widget.winfo_ismapped():
                x = widget.winfo_rootx() - frame_left.left
                y = widget.winfo_rooty() - frame_left.top
                boxes.setdefault(name, {})[str(widget.cget("text"))] = [x, y, x + widget.winfo_width(),
                                                                        y + widget.winfo_height()]
    print(name)


def state(done=(), working="", progress=0.0, label="", manual=None):
    for step in steps:
        key = step.key
        if key in done:
            window._set(key, "done", step.detail)
        elif key == working:
            window._set(key, "working")
        elif manual and key in manual:
            window._set(key, "manual", manual[key])
        else:
            window._set(key, "pending", step.detail)
    window.bar.configure(mode="determinate", value=progress)
    window.status.configure(text=label)


state(done=["windows", "voz"], working="whisper", progress=0.42, label="Reconocimiento de voz «small» (1 de 2)")
snap("instalando")
state(done=["windows", "voz", "whisper", "voces"], working="tts", progress=0.78,
      label="Voz en inglés (femenina) · 38 de 61 MB")
snap("voces")
state(done=["windows", "voz", "whisper", "voces", "tts", "claude"], progress=1.0,
      label="Falta lo que necesita tu permiso (con su botón).",
      manual={"sesion": "Se abrió Claude Code: iniciá sesión con tu cuenta de Claude (Pro o Max)."})
snap("sesion")
state(done=[s.key for s in steps], progress=1.0, label="✓ Todo listo.")
window.close_button.configure(text="Listo")
snap("listo")
json.dump(boxes, open(f"{out}/setup.json", "w"), indent=1)
root.destroy()
