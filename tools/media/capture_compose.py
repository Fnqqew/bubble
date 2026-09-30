"""Guarda la barra de escritura real en cada etapa: vacía, con texto, traduciendo y lista."""
import json
import sys
import time
import tkinter as tk

sys.path.insert(0, "src")
sys.path.insert(0, sys.argv[2])
from printwin import print_window, show_quietly

from bubble import win32
from bubble.geometry import Rect
from bubble.ui import motion, overlays

motion.appear = lambda *args, **kwargs: None  # sin animación: permanece invisible (transparente) durante la captura

out = sys.argv[1]
if "pro" in sys.argv[3:]:  # barra con Bubble Pro activo (chip dorado y "✦ PRO")
    from bubble import pro

    pro.set_active(True)
TEXT = "dale, esperame en la torre"
TRANSLATION = "sure, wait for me at the tower"

win32.force_foreground = lambda hwnd: True
root = tk.Tk()
root.withdraw()
bar = overlays.ComposeBar(root, lambda *a: None, lambda *a: None, lambda: None)
bar.win.attributes("-topmost", False)
bar.win.attributes("-alpha", 0.0)
bar.win.focus_force = lambda: None
bar.win.deiconify = lambda: show_quietly(win32.toplevel_hwnd(bar.win))
bar.open(["en", "*", "pt"], Rect(0, 0, 1600, 900), 3, {"*": "Todos los del chat (EN + PT)"})
frames = []


def snap(name):
    for _ in range(4):
        root.update()
        time.sleep(0.03)
    for job in (bar._after, bar._dots):
        if job:
            bar.win.after_cancel(job)
    bar._after = bar._dots = None
    image = print_window(win32.toplevel_hwnd(bar.win))
    image.save(f"{out}/{name}.png")
    frames.append(name)


snap("empty")
for count in range(1, len(TEXT) + 1):
    bar.entry.delete(0, "end")
    bar.entry.insert(0, TEXT[:count])
    bar._update_placeholder()
    bar._show_preview("")
    snap(f"type_{count:02d}")
key = bar.current()
for step in range(3):
    bar._show_preview("", working=True)
    if bar._dots:
        bar.win.after_cancel(bar._dots)
    bar._dots = None
    bar._animate_dots("", step)
    snap(f"working_{step}")
bar.show_preview(key, TRANSLATION, "done")
snap("done")
json.dump(frames, open(f"{out}/frames.json", "w"))
root.destroy()
print(len(frames), "frames")
