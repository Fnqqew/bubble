"""Ventanas para instalar lo que falta («Preparar Bubble») y para desinstalar todo («Desinstalar Bubble»)."""

from __future__ import annotations

import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from .. import install, uninstall
from . import motion, widgets

ICONS = {"pending": "○", "working": "◐", "done": "✓", "error": "!", "manual": "→"}


class SetupWindow:
    """Lo que falta, con su estado. Lo automático se instala solo al abrirse; lo que necesita permiso tiene botón."""

    def __init__(self, root: tk.Misc, post: Callable[[Callable[[], None]], None], steps: list[install.Step] | None = None,
                 auto: bool = True, on_no_claude: Callable[[], None] | None = None) -> None:
        """`on_no_claude()`: «¿No tenés Claude?» (cómo seguir: Bubble Pro con créditos o conectar Claude)."""
        self.root = root
        self.post = post  # hacer algo en el hilo de la ventana
        self.steps = steps if steps is not None else install.steps()
        self.window, body = widgets.dialog(root, "Preparar Bubble")
        colors = widgets.palette()
        ttk.Label(body, text="Preparar Bubble", font="SunValleySubtitleFont").pack(anchor="w")
        widgets.muted(body, "Bubble instala solo lo que le falta. La primera vez tarda unos minutos (se descarga): podés "
                            "seguir usando la PC.", pady=(2, 12))
        self.rows: dict[str, tuple[ttk.Label, ttk.Label]] = {}
        self.buttons: dict[str, tuple[ttk.Button, ttk.Frame]] = {}  # lo que necesita tu permiso (se va al estar listo)
        for step in self.steps:
            row = ttk.Frame(body)
            row.pack(fill="x", pady=(6, 0))
            icon = ttk.Label(row, text=ICONS["pending"], width=2, font="SunValleyBodyStrongFont",
                             foreground=colors["muted"])
            icon.pack(side="left", anchor="n")
            texts = ttk.Frame(row)
            texts.pack(side="left", fill="x", expand=True)
            ttk.Label(texts, text=step.title, font="SunValleyBodyStrongFont").pack(anchor="w")
            detail = ttk.Label(texts, text=step.detail, font="SunValleyCaptionFont", foreground=colors["muted"],
                               wraplength=250 if step.action else 380, justify="left")
            detail.pack(anchor="w")
            if step.action:
                button = ttk.Button(row, text=step.action, command=lambda s=step: self._manual(s))
                button.pack(side="right")
                self.buttons[step.key] = (button, texts)
            self.rows[step.key] = (icon, detail)
            if step.key == "sesion" and on_no_claude is not None:
                link = ttk.Label(body, text="¿No tenés Claude o suscripción? Mirá cómo seguir →",
                                 font="SunValleyCaptionFont", foreground=colors["accent"], cursor="hand2")
                link.pack(anchor="w", padx=(28, 0), pady=(2, 0))
                link.bind("<Button-1>", lambda _event: on_no_claude())
        self.bar = ttk.Progressbar(body, mode="determinate", maximum=1.0, length=460)
        self.bar.pack(fill="x", pady=(18, 4))
        self.status = widgets.muted(body, "", pady=(0, 10))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        self.close_button = ttk.Button(buttons, text="Seguir después", command=self.close)
        self.close_button.pack(side="right")
        self._busy = False
        widgets.present(self.window, root)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._check_all()
        if auto:
            self.start()

    # ------------------------------------------------------------ estado
    def _set(self, key: str, state: str, text: str | None = None) -> None:
        colors = widgets.palette()
        icon, detail = self.rows[key]
        color = {"done": colors["good"], "error": colors["bad"], "working": colors["accent"]}.get(state, colors["muted"])
        icon.configure(text=ICONS[state], foreground=color)
        if text is not None:
            detail.configure(text=text)
        if key in self.buttons:  # ya está: sin el botón de instalarlo (confundía)
            button, texts = self.buttons[key]
            if state == "done":
                button.pack_forget()
            elif not button.winfo_manager():
                button.pack(side="right", before=texts)

    def _check_all(self) -> None:
        def work() -> None:
            for step in self.steps:
                try:
                    ready = step.check()
                except Exception:  # noqa: BLE001
                    ready = False
                if ready:
                    self.post(lambda key=step.key: self._set(key, "done"))

        threading.Thread(target=work, name="bubble-revisar", daemon=True).start()

    def _progress(self, label: str, fraction: float) -> None:
        def show() -> None:
            self.status.configure(text=label)
            if fraction < 0:
                if str(self.bar.cget("mode")) != "indeterminate":
                    self.bar.configure(mode="indeterminate")
                    self.bar.start(12)
            else:
                if str(self.bar.cget("mode")) != "determinate":
                    self.bar.stop()
                    self.bar.configure(mode="determinate")
                self.bar.configure(value=fraction)

        self.post(show)

    # ------------------------------------------------------------ instalar
    def start(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.close_button.configure(text="Seguir después")

        def work() -> None:
            failed: set[str] = set()
            installed = 0
            for step in self.steps:
                if not install.automatic(step):
                    continue
                try:
                    if step.check():
                        continue
                except Exception:  # noqa: BLE001
                    pass
                if any(key in failed for key in step.after):
                    self.post(lambda key=step.key: self._set(key, "error", "Falta lo anterior."))
                    failed.add(step.key)
                    continue
                self.post(lambda key=step.key: self._set(key, "working"))
                try:
                    message = step.run(self._progress)
                    installed += 1
                    self.post(lambda key=step.key, text=message: self._set(key, "done", text))
                except Exception as exc:  # noqa: BLE001 - se muestra y se sigue con lo demás
                    failed.add(step.key)
                    self.post(lambda key=step.key, text=f"No se pudo: {exc}": self._set(key, "error", text))
            self.post(lambda: self._finished(failed, installed))

        threading.Thread(target=work, name="bubble-instalar", daemon=True).start()

    def _finished(self, failed: set[str], installed: int) -> None:
        self._busy = False
        self.bar.stop()
        self.bar.configure(mode="determinate", value=1.0 if not failed else 0.0)
        if failed:
            self.status.configure(text="Algo no se pudo instalar. Revisá tu conexión y probá de nuevo.")
            self.close_button.configure(text="Cerrar")
        else:
            self.status.configure(text="✓ Todo listo." if installed or not self._manual_pending() else
                                  "Falta lo que necesita tu permiso (con su botón).")
            self.close_button.configure(text="Listo")

    def _manual_pending(self) -> bool:
        return any(str(self.rows[s.key][0].cget("text")) != ICONS["done"] for s in self.steps if s.action)

    def _manual(self, step: install.Step) -> None:
        self._set(step.key, "working")

        def work() -> None:
            try:
                message = step.run(self._progress)
                self.post(lambda: self._set(step.key, "manual", message))
            except Exception as exc:  # noqa: BLE001
                error = f"No se pudo: {exc}"  # (`exc` deja de existir al salir del except: la lambda corre después)
                self.post(lambda: self._set(step.key, "error", error))

        threading.Thread(target=work, name="bubble-instalar-manual", daemon=True).start()

    def close(self) -> None:
        motion.vanish(self.window, self.window.destroy)


class UninstallWindow:
    """Qué se borra (elegible), confirmación y listo. `on_done`: cerrar Bubble."""

    def __init__(self, root: tk.Misc, on_done: Callable[[], None]) -> None:
        self.root = root
        self.on_done = on_done
        self.window, body = widgets.dialog(root, "Desinstalar Bubble")
        ttk.Label(body, text="Desinstalar Bubble", font="SunValleySubtitleFont").pack(anchor="w")
        widgets.muted(body, "Elegí qué borrar. Antes, Windows vuelve a usar tu micrófono y tu parlante de verdad.",
                      pady=(2, 12))
        self.vars: dict[str, tk.BooleanVar] = {}
        for part in uninstall.parts():
            var = tk.BooleanVar(value=part.selected and part.available)
            self.vars[part.key] = var
            check = ttk.Checkbutton(body, text=part.title, variable=var)
            check.pack(anchor="w", pady=(8, 0))
            if not part.available:
                check.state(["disabled"])
            widgets.muted(body, part.detail, wrap=440, pady=(0, 0), padx=(28, 0))
        self.status = widgets.muted(body, "", pady=(14, 8))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        self.go = ttk.Button(buttons, text="Desinstalar", style="Accent.TButton", command=self._confirm)
        self.go.pack(side="right")
        self.cancel = ttk.Button(buttons, text="Cancelar", command=lambda: motion.vanish(self.window,
                                                                                      self.window.destroy))
        self.cancel.pack(side="right", padx=(0, 8))
        self._confirming = False
        widgets.present(self.window, root)

    def _confirm(self) -> None:
        chosen = {key for key, var in self.vars.items() if var.get()}
        if not chosen:
            self.status.configure(text="No elegiste nada para borrar.")
            return
        if not self._confirming:
            self._confirming = True  # un segundo toque confirma (no se borra nada por un clic de más)
            self.go.configure(text="Sí, desinstalar")
            self.status.configure(text="¿Seguro? Esto no se puede deshacer. Tocá «Sí, desinstalar».",
                                  foreground=widgets.palette()["warn"])
            return
        self.go.state(["disabled"])
        self.cancel.state(["disabled"])
        self.status.configure(text="Desinstalando…", foreground=widgets.palette()["muted"])

        result: list[str] = []

        def work() -> None:
            try:
                done = uninstall.run(chosen)
                result.append("\n".join(["✓ Bubble se desinstaló.", *done, "Se cierra en unos segundos."]))
            except Exception as exc:  # noqa: BLE001
                result.append(f"No se pudo terminar: {exc}")

        def wait() -> None:  # (la ventana se toca solo desde su hilo)
            if result:
                self._finished(result[0])
            else:
                self.window.after(100, wait)

        threading.Thread(target=work, name="bubble-desinstalar", daemon=True).start()
        wait()

    def _finished(self, text: str) -> None:
        self.status.configure(text=text)
        self.window.after(3500, self.on_done)
