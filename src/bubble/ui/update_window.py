"""«Hay una versión nueva»: qué trae, y actualizar ahora o más tarde (ver update.py)."""

from __future__ import annotations

import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable

from .. import __version__, update
from . import motion, theme, widgets


class UpdateWindow:
    def __init__(self, root: tk.Misc, release: update.Release, post: Callable[[Callable[[], None]], None],
                 restart: Callable[[], None]) -> None:
        """`post(acción)`: hacer algo en el hilo de la ventana. `restart()`: cerrar Bubble (la actualización termina
        sola y lo vuelve a abrir)."""
        self.release, self.post, self.restart = release, post, restart
        self.kind = update.install_kind()
        self.window, body = widgets.dialog(root, "Actualización de Bubble")
        colors = widgets.palette()
        ttk.Label(body, text="Hay una versión nueva", font="SunValleySubtitleFont").pack(anchor="w")
        widgets.muted(body, f"Bubble {release.version} ya está disponible (tenés la {__version__}). Se instala en "
                            "menos de un minuto y no perdés nada: tu configuración, tu voz y lo descargado quedan "
                            "como están.", pady=(2, 12))
        notes = update.plain_notes(release.notes)
        if notes:
            ttk.Label(body, text="Qué trae", font="SunValleyBodyStrongFont").pack(anchor="w", pady=(0, 4))
            frame, text = theme.scrolled_text(body, height=9, width=58, wrap="word", font=("Segoe UI", 10))
            frame.pack(fill="x")
            text.insert("1.0", notes)
            text.configure(state="disabled")
        link = ttk.Label(body, text="Ver todo en GitHub", font="SunValleyCaptionFont", foreground=colors["accent"],
                         cursor="hand2")
        link.pack(anchor="w", pady=(6, 0))
        link.bind("<Button-1>", lambda _event: webbrowser.open(release.url))
        self.bar = ttk.Progressbar(body, mode="determinate", maximum=1.0, length=460)
        self.status = widgets.muted(body, "", pady=(10, 8))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        if self.kind:
            self.go = ttk.Button(buttons, text="Actualizar ahora", style="Accent.TButton", command=self._update)
        else:  # (instalado de otra forma: se baja a mano)
            self.go = ttk.Button(buttons, text="Bajarla de GitHub", style="Accent.TButton",
                                 command=lambda: (webbrowser.open(release.url), self.close()))
        self.go.pack(side="right")
        self.later = ttk.Button(buttons, text="Más tarde", command=self._later)
        self.later.pack(side="right", padx=(0, 8))
        self.window.protocol("WM_DELETE_WINDOW", self._later)
        self.window.bind("<Escape>", lambda _event: self._later())
        widgets.present(self.window, root)

    def _later(self) -> None:
        if str(self.later.cget("state")) == "disabled":
            return  # (actualizando: ya no se puede cancelar)
        update.remind_later(self.release)
        self.close()

    def _progress(self, label: str, fraction: float) -> None:
        def show() -> None:
            self.status.configure(text=label, foreground=widgets.palette()["muted"])
            if fraction < 0:
                if str(self.bar.cget("mode")) != "indeterminate":
                    self.bar.configure(mode="indeterminate")
                    self.bar.start(12)
            else:
                self.bar.stop()
                self.bar.configure(mode="determinate", value=fraction)

        self.post(show)

    def _update(self) -> None:
        for button in (self.go, self.later):
            button.state(["disabled"])
        self.bar.pack(fill="x", pady=(12, 0), before=self.status)
        self._progress("Preparando…", -1)

        def work() -> None:
            try:
                folder = update.prepare(self.release, self._progress)
            except (update.UpdateError, OSError, ValueError) as exc:
                message = str(exc) if isinstance(exc, update.UpdateError) else f"No se pudo bajar ({exc})."
                self.post(lambda: self._failed(message))
                return
            self.post(lambda: self._ready(folder))

        threading.Thread(target=work, name="bubble-actualizar", daemon=True).start()

    def _failed(self, message: str) -> None:
        self.bar.stop()
        self.bar.pack_forget()
        self.status.configure(text=message, foreground=widgets.palette()["warn"])
        for button in (self.go, self.later):
            button.state(["!disabled"])

    def _ready(self, folder) -> None:
        self.status.configure(text="✓ Listo. Bubble se cierra y vuelve a abrir solo en unos segundos…",
                              foreground=widgets.palette()["good"])

        def go() -> None:
            try:
                update.launch(folder)
            except OSError as exc:
                self._failed(f"No se pudo empezar la actualización ({exc}).")
                return
            self.restart()

        self.window.after(1400, go)

    def close(self) -> None:
        motion.vanish(self.window, self.window.destroy)
