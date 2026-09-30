"""Tutorial de primer uso: se muestra una sola vez; después se abre solo desde el botón "Tutorial"."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

WIDTH, HEIGHT = 640, 440


def _colors() -> dict[str, str]:
    """Los colores del tema elegido (oscuro o claro), para que combine con la ventana."""
    from .widgets import palette

    colors = palette()
    dark = colors["bg"] == "#1c1c1c"
    return {"bg": colors["bg"], "text": colors["text"], "muted": colors["muted"], "accent": colors["accent"],
            "accent_hover": "#3d8fe6" if dark else "#004a8f", "dot_off": "#3a3d42" if dark else "#d6dae1",
            "button": "#2d2f33" if dark else "#eef1f6", "button_hover": "#3a3d42" if dark else "#dfe4ec",
            "on_accent": "#0b1a2a" if dark else "#ffffff"}


def _mix(color_a: str, color_b: str, amount: float) -> str:
    from .widgets import mix

    return mix(color_a, color_b, amount)


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    # Botón opcional para hacer el paso en el momento (ej. "Calibrar ahora").
    action_label: str = ""
    action: Callable[[], None] | None = None
    # Un cartel destacado (título, texto), para lo más importante: se ve de color, debajo del texto.
    highlight: tuple[str, str] | None = None


def build_steps(hotkey: str, calibrate: Callable[[], None], capture_test: Callable[[], None] | None = None,
                fix_windows: Callable[[], None] | None = None) -> list[Step]:
    return [
        Step(
            "¡Hola! Soy Bubble",
            "Te traduzco Roblox mientras jugás: el chat, lo que dicen arriba de la cabeza y la voz. "
            "Uso tu cuenta de Claude. Cuando arriba diga «Listo», arrancamos.",
            highlight=("Antes que nada: el micrófono",
                       "Te entiendo tan bien como te escucho. Con un micrófono de auriculares o uno USB cerca de la "
                       "boca, todo sale mucho mejor que con el de la notebook. Podés probarlo en Inicio."),
        ),
        Step(
            "Tu idioma",
            "En Inicio, en «Hablo», ya está el idioma de tu Windows. Si sos de Argentina, México o España, "
            "elegí tu país: te traduzco todo como hablás vos.",
        ),
        Step(
            "Apagá la traducción de Roblox",
            "Roblox traduce el chat por su cuenta, y si lo deja prendido yo leo mensajes que ya vienen cambiados.\n\n"
            "1. En el juego, apretá Esc.\n"
            "2. Andá a «Configuración».\n"
            "3. Apagá «Traducción automática del chat».",
        ),
        Step(
            "Entrá a un juego",
            "Abrí cualquier juego y dejalo a la vista. Cuando aparezcan un par de mensajes, encuentro el chat solo.\n\n"
            "Si en algún juego no lo encuentro, en Ajustes podés buscarlo o marcarlo a mano.",
            "Buscar el chat ahora",
            calibrate,
        ),
        Step(
            "Leé en tu idioma",
            "Los mensajes en otro idioma aparecen traducidos arriba del original, con el nombre de quien los "
            "escribió. Lo que ya está en tu idioma lo dejo como está.\n\n"
            "Las burbujas que salen sobre la cabeza de los jugadores también se traducen.",
        ),
        Step(
            f"Escribí con {hotkey}",
            f"En el juego apretá {hotkey} y se abre una barra. Escribí como hablás y vas viendo cómo queda.\n\n"
            "• Enter lo manda al chat, ya traducido.\n"
            "• Ctrl+Enter lo dice en voz.\n"
            "• Tab cambia el idioma, ↑ ↓ el tono y Ctrl+G la voz (mujer u hombre).\n\n"
            "Si tu tecla usa Shift (como «°»), apretala sola: en Roblox el Shift mueve la cámara.",
        ),
        Step(
            "La voz",
            "En la página «Voz» tenés dos cosas:\n\n"
            "• Subtítulos: ves qué dice cada uno, en tu idioma.\n"
            "• Tu voz para los demás: hablás en tu idioma y te escuchan en el suyo, con un botón o sin tocar nada.\n\n"
            "Para que los demás te escuchen necesito un micrófono virtual. Te cuento en el paso que sigue.",
        ),
        Step(
            "El micrófono virtual",
            "Windows no deja que un programa hable por tu micrófono, así que uso uno virtual y gratis "
            "(VB-Audio Virtual Cable). Es como Soundpad: le paso tu voz de verdad y le sumo la traducida.\n\n"
            "1. En Voz, tocá «Instalar (gratis)». Aceptá el permiso y tocá «Install Driver».\n"
            "2. Si Windows te pide reiniciar, reiniciá.\n"
            "3. Listo. Mientras Bubble está abierto, los demás te escuchan por ahí. Cuando lo cerrás, vuelve tu "
            "micrófono de siempre.\n\n"
            "Abrí Bubble antes que Roblox, así Roblox lo agarra solo. Y acordate de tener el micrófono prendido en "
            "Roblox.",
            "Arreglar Windows" if fix_windows else "",
            fix_windows,
        ),
        Step(
            "Hacelo a tu gusto",
            "En Ajustes cambiás los colores, el tamaño de las traducciones, dónde van los subtítulos y qué tan "
            "informal querés sonar.\n\n"
            "Este tutorial lo volvés a ver cuando quieras desde Ajustes. ¡A jugar!",
        ),
    ]


class TutorialWindow:
    def __init__(
        self,
        root: tk.Tk,
        steps: list[Step],
        on_close: Callable[[str], None],
        icon_png: Path | None = None,
    ) -> None:
        self.steps = steps
        self.on_close = on_close
        self.index = 0
        self.c = _colors()
        self.win = tk.Toplevel(root, bg=self.c["bg"])
        self.win.title("Tutorial de Bubble")
        self.win.resizable(False, False)
        self.win.transient(root)
        self.win.protocol("WM_DELETE_WINDOW", lambda: self._close("cerrado"))
        self.win.bind("<Right>", lambda _e: self.next())
        self.win.bind("<Left>", lambda _e: self.previous())
        self.win.bind("<Escape>", lambda _e: self._close("saltado"))
        self._icon = None
        if icon_png and icon_png.exists():
            try:
                self._icon = tk.PhotoImage(file=str(icon_png)).subsample(4)  # 256 -> 64 px
            except tk.TclError:
                self._icon = None
        self._build()
        self._place(root)
        self.show(0)

    # ---------- interfaz ----------
    def _build(self) -> None:
        header = tk.Frame(self.win, bg=self.c["bg"])
        header.pack(fill="x", padx=24, pady=(20, 4))
        if self._icon:
            tk.Label(header, image=self._icon, bg=self.c["bg"]).pack(side="left", padx=(0, 12))
        # El contador se empaqueta antes que los títulos para que un título largo no lo empuje afuera.
        self.counter = tk.Label(header, font=("Segoe UI", 9), fg=self.c["muted"], bg=self.c["bg"])
        self.counter.pack(side="right", anchor="n")
        titles = tk.Frame(header, bg=self.c["bg"])
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="Tutorial de Bubble", font=("Segoe UI", 10), fg=self.c["muted"], bg=self.c["bg"]).pack(anchor="w")
        self.title = tk.Label(
            titles, font=("Segoe UI", 17, "bold"), fg=self.c["text"], bg=self.c["bg"], anchor="w", justify="left", wraplength=WIDTH - 200
        )
        self.title.pack(anchor="w", fill="x")

        self.body = tk.Label(
            self.win, font=("Segoe UI", 11), fg=self.c["text"], bg=self.c["bg"], justify="left", anchor="nw", wraplength=WIDTH - 80
        )
        self.body.pack(fill="both", expand=True, padx=24, pady=(10, 6))

        # El cartel destacado (lo más importante del paso): fondo de color, una barra del acento y el ícono.
        tint = _mix(self.c["accent"], self.c["bg"], 0.84)
        self.callout = tk.Frame(self.win, bg=tint, highlightthickness=1,
                                highlightbackground=_mix(self.c["accent"], self.c["bg"], 0.45))
        tk.Frame(self.callout, bg=self.c["accent"], width=4).pack(side="left", fill="y")
        inner = tk.Frame(self.callout, bg=tint)
        inner.pack(side="left", fill="both", expand=True, padx=(12, 12), pady=(10, 10))
        top = tk.Frame(inner, bg=tint)
        top.pack(fill="x")
        from .widgets import ICONS, icon_font

        tk.Label(top, text=ICONS["mic"], font=icon_font(), fg=self.c["accent"], bg=tint).pack(side="left",
                                                                                          padx=(0, 8))
        self.callout_title = tk.Label(top, font=("Segoe UI Semibold", 12), fg=self.c["text"], bg=tint, anchor="w")
        self.callout_title.pack(side="left")
        self.callout_body = tk.Label(inner, font=("Segoe UI", 10), fg=self.c["text"], bg=tint, justify="left",
                                     anchor="w", wraplength=WIDTH - 110)
        self.callout_body.pack(anchor="w", pady=(4, 0))

        self.action_button = self._button(self.win, "", self._run_action, primary=False)

        self.dots = tk.Canvas(self.win, height=14, bg=self.c["bg"], highlightthickness=0)
        self.dots.pack(fill="x", padx=24, pady=(4, 8))

        bottom = tk.Frame(self.win, bg=self.c["bg"])
        bottom.pack(fill="x", padx=24, pady=(0, 18))
        self._link(bottom, "No mostrar más", lambda: self._close("no_mostrar")).pack(side="left")
        self._link(bottom, "Saltar tutorial", lambda: self._close("saltado")).pack(side="left", padx=(16, 0))
        self.next_button = self._button(bottom, "Siguiente ›", self.next, primary=True)
        self.next_button.pack(side="right")
        self.skip_button = self._button(bottom, "Saltar paso", self.next, primary=False)
        self.skip_button.pack(side="right", padx=(0, 8))
        self.back_button = self._button(bottom, "‹ Anterior", self.previous, primary=False)
        self.back_button.pack(side="right", padx=(0, 8))

    def _button(self, parent, text: str, command, primary: bool) -> tk.Button:
        c = self.c
        colors = ((c["accent"], c["on_accent"], c["accent_hover"]) if primary
                  else (c["button"], c["text"], c["button_hover"]))
        button = tk.Button(
            parent, text=text, command=command, font=("Segoe UI", 10, "bold" if primary else "normal"),
            bg=colors[0], fg=colors[1], activebackground=colors[2], activeforeground=colors[1],
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=colors[2]))
        button.bind("<Leave>", lambda _e: button.configure(bg=colors[0]))
        return button

    def _link(self, parent, text: str, command) -> tk.Label:
        link = tk.Label(parent, text=text, font=("Segoe UI", 9, "underline"), fg=self.c["muted"], bg=self.c["bg"], cursor="hand2")
        link.bind("<Button-1>", lambda _e: command())
        return link

    def _place(self, root: tk.Tk) -> None:
        self.win.update_idletasks()
        width, height = WIDTH, HEIGHT
        x = root.winfo_rootx() + (root.winfo_width() - width) // 2
        y = root.winfo_rooty() + (root.winfo_height() - height) // 3
        self.win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        self.win.lift()
        self.win.focus_force()

    def _draw_dots(self) -> None:
        self.dots.delete("all")
        total = len(self.steps)
        spacing = 16
        start = (WIDTH - 48 - spacing * (total - 1)) // 2
        for i in range(total):
            x = start + i * spacing
            color = self.c["accent"] if i == self.index else self.c["dot_off"]
            radius = 5 if i == self.index else 4
            self.dots.create_oval(x - radius, 7 - radius, x + radius, 7 + radius, fill=color, outline="")

    # ---------- navegación ----------
    def show(self, index: int) -> None:
        self.index = max(0, min(index, len(self.steps) - 1))
        step = self.steps[self.index]
        last = self.index == len(self.steps) - 1
        self.title.configure(text=step.title)
        self.body.configure(text=step.body)
        if step.highlight:
            self.callout_title.configure(text=step.highlight[0])
            self.callout_body.configure(text=step.highlight[1])
            self.callout.pack(fill="x", padx=24, pady=(0, 10), before=self.dots)  # (el botón del paso, después)
        else:
            self.callout.pack_forget()
        self.counter.configure(text=f"Paso {self.index + 1} de {len(self.steps)}")
        if step.action:
            self.action_button.configure(text=step.action_label)
            self.action_button.pack(anchor="w", padx=24, pady=(0, 6), before=self.dots)
        else:
            self.action_button.pack_forget()
        self.next_button.configure(text="¡Listo!" if last else "Siguiente ›")
        # "Saltar paso" solo tiene sentido en los pasos con una acción para hacer.
        if step.action and not last:
            self.skip_button.pack(side="right", padx=(0, 8), after=self.next_button)
        else:
            self.skip_button.pack_forget()
        self.back_button.configure(state="normal" if self.index else "disabled")
        self._draw_dots()

    def next(self) -> None:
        if self.index == len(self.steps) - 1:
            self._close("completado")
        else:
            self.show(self.index + 1)

    def previous(self) -> None:
        self.show(self.index - 1)

    def _run_action(self) -> None:
        step = self.steps[self.index]
        if step.action:
            step.action()
            self.next()

    def lift(self) -> None:
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def _close(self, reason: str) -> None:
        self.win.destroy()
        self.on_close(reason)
