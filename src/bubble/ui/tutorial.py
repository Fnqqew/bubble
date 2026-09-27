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


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    # Botón opcional para hacer el paso en el momento (ej. "Calibrar ahora").
    action_label: str = ""
    action: Callable[[], None] | None = None


def build_steps(hotkey: str, calibrate: Callable[[], None], capture_test: Callable[[], None] | None = None,
                fix_windows: Callable[[], None] | None = None) -> list[Step]:
    return [
        Step(
            "¡Hola! Soy Bubble",
            "Traduzco Roblox mientras jugás: el chat, lo que dicen sobre la cabeza de los jugadores y hasta la voz.\n\n"
            "Uso tu suscripción de Claude, así que no tenés que configurar nada. Cuando arriba diga «Listo», "
            "estamos.",
        ),
        Step(
            "Contame cómo hablás",
            "En Inicio, «Hablo» ya viene con el idioma de tu Windows, con tu variante (argentino, mexicano…). "
            "Todo lo que te llegue te lo cuento así, con tu jerga.",
        ),
        Step(
            "Apagá la traducción de Roblox",
            "Roblox trae su propia traducción automática del chat. Apagala: si no, leo mensajes ya traducidos por "
            "Roblox y se pierde lo que dijeron de verdad.\n\n"
            "1. En el juego apretá Esc.\n"
            "2. Entrá a «Configuración» (Settings).\n"
            "3. Desactivá «Traducción automática del chat».",
        ),
        Step(
            "Abrí un juego",
            "Entrá a cualquier juego y dejalo a la vista (leo el chat de la pantalla). Apenas haya un par de "
            "mensajes, lo encuentro solo.\n\n"
            "Si en algún juego no lo encuentro, en Ajustes tenés «Buscar el chat» o «Marcarlo a mano».",
            "Buscar el chat ahora",
            calibrate,
        ),
        Step(
            "Leé en tu idioma",
            "Cada mensaje en otro idioma aparece traducido encima del original, y el nombre de quien lo escribió "
            "queda a la vista. Lo que ya está en tu idioma no lo toco.\n\n"
            "Las burbujas sobre la cabeza de los jugadores también se traducen, y siguen a la cámara.",
        ),
        Step(
            f"Escribí con {hotkey}",
            f"En el juego apretá {hotkey}: se abre una barra. Escribí como hablás y mirá cómo va a quedar.\n\n"
            "• Enter lo manda traducido al chat.\n"
            "• Ctrl+Enter lo dice en voz.\n"
            "• Tab cambia el idioma, ↑ ↓ el tono y Esc cierra.\n\n"
            "Si tu tecla lleva Shift (como «°»), apretala sola: el Shift mueve la cámara en Roblox.",
        ),
        Step(
            "La voz",
            "En la página «Voz»:\n\n"
            "• Subtítulos: ves quién habla (Voz 1, Voz 2…) y qué dice, en tu idioma.\n"
            "• Tu voz para los demás: hablás en tu idioma y te escuchan en el suyo, con un botón o en modo directo. "
            "«Te escuchan en» es el mismo idioma que elegís en la barra para escribir (Tab).\n\n"
            "En «Pruebas» probás tu micrófono y tu voz traducida sin jugar, y le enseñás tu forma de hablar.\n\n"
            "Para que te escuchen, Bubble habla por un micrófono virtual (como Soundpad). Mirá el paso siguiente.",
        ),
        Step(
            "El micrófono virtual",
            "Windows no deja que un programa hable por tu micrófono: hace falta un micrófono virtual gratis, "
            "VB-Audio Virtual Cable (el driver que se descarga). Funciona como Soundpad: Bubble le pasa tu voz real y "
            "le suma la traducida.\n\n"
            "1. Voz → Micrófono → «Instalar (gratis)». Aceptá el permiso y tocá «Install Driver». Si lo pide, "
            "reiniciá la PC.\n"
            "2. Abrí Bubble: si el instalador te cambió el micrófono o los parlantes de Windows, los vuelvo a poner "
            "como estaban solo.\n"
            "3. Listo: mientras Bubble está abierto, el micrófono virtual es tu micrófono de Windows y Bubble le pasa "
            "tu voz; al cerrar Bubble vuelve el tuyo.\n\n"
            "Abrí Bubble antes que Roblox: Roblox elige su micrófono al abrirse y así toma el de Bubble solo. Si "
            "Roblox ya estaba abierto, te aviso: elegí «CABLE Output» en Roblox (Esc → Configuración → Dispositivo de "
            "entrada) o volvé a abrirlo. En Roblox tenés que estar desmuteado.\n\n"
            "¿Algo quedó raro? «Arreglar Windows» deja todo como estaba.",
            "Arreglar Windows" if fix_windows else "",
            fix_windows,
        ),
        Step(
            "Hacelo tuyo",
            "En Ajustes elegís el tema (oscuro o claro), el color y el tamaño de las traducciones, dónde van los "
            "subtítulos y qué tan informal querés sonar al escribir.\n\n"
            "Podés volver a ver esta guía cuando quieras, desde Ajustes → «Ver el tutorial». ¡A jugar!",
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
