"""Tutorial de primer uso: se muestra una sola vez; después se abre solo desde el botón "Tutorial"."""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

BG = "#ffffff"
TEXT = "#1f2328"
MUTED = "#6b7280"
ACCENT = "#2f6fed"
ACCENT_HOVER = "#1f56c9"
DOT_OFF = "#d6dae1"
WIDTH, HEIGHT = 680, 470


@dataclass(frozen=True)
class Step:
    title: str
    body: str
    # Botón opcional para hacer el paso en el momento (ej. "Calibrar ahora").
    action_label: str = ""
    action: Callable[[], None] | None = None


def build_steps(hotkey: str, calibrate: Callable[[], None], capture_test: Callable[[], None]) -> list[Step]:
    return [
        Step(
            "¡Bienvenido a Bubble!",
            "Bubble traduce en tiempo real el chat de Roblox:\n\n"
            "• Lo que escriben los demás aparece traducido al lado del chat.\n"
            "• Lo que escribís vos se envía traducido.\n\n"
            "Usa tu suscripción de Claude, así que no hace falta configurar claves. "
            "Mirá la barra de abajo de la ventana: cuando diga «Listo», ya está conectado.",
        ),
        Step(
            "Tu idioma y el de ellos",
            "«Tu idioma» ya viene con el de tu Windows, incluida tu variante (por ejemplo, Argentina). "
            "Todo lo que te llega se traduce a cómo hablás vos, con tu jerga.\n\n"
            "«Enviar en» en «auto» elige solo el idioma que más se usa en el chat.",
        ),
        Step(
            "¿Qué tan informal querés sonar?",
            "En «Tono al enviar» elegís del 1 al 5:\n\n"
            "1 · Neutro / formal: claro y sin jerga, el que menos confusiones genera.\n"
            "3 · Casual: relajado, con jerga muy conocida.\n"
            "5 · Jerga nativa: como escribe un gamer de ese país.\n\n"
            "Solo cambia cómo se dice, nunca qué se dice.",
        ),
        Step(
            "Abrí Roblox",
            "Entrá a cualquier juego. En el panel de Bubble tiene que aparecer «Roblox detectado».\n\n"
            "Roblox tiene que quedar visible (no minimizado ni tapado por otra ventana), "
            "porque Bubble lee el chat de la pantalla.",
        ),
        Step(
            "Apagá la traducción de Roblox",
            "Roblox tiene su propia traducción automática del chat. Apagala: si no, Bubble lee mensajes ya "
            "traducidos por Roblox (con menos precisión) y se pierde la jerga original.\n\n"
            "1. Dentro del juego apretá Esc (o tocá el menú arriba a la izquierda).\n"
            "2. Entrá a la pestaña «Configuración» (Settings).\n"
            "3. Desactivá «Traducción automática del chat» (Automatic Chat Translation).\n\n"
            "Se hace una vez, pero revisalo si ves mensajes traducidos por Roblox: a veces vuelve a activarse.",
        ),
        Step(
            "Bubble encuentra el chat solo",
            "Cada juego puede poner el chat en otro lugar. Con Roblox abierto y un par de mensajes a la vista, Bubble "
            "lo busca solo. Si cambiás de juego y el chat está en otro lado, tocá «Detectar chat».\n\n"
            "Si no lo encuentra, tocá «a mano…» y arrastrá un rectángulo sobre los mensajes. Queda guardado aunque "
            "muevas la ventana de Roblox.",
            "Detectar ahora",
            calibrate,
        ),
        Step(
            "Comprobá que lo lee bien",
            "«Probar captura» muestra en el registro lo que leyó y qué mensajes reconoció.\n\n"
            "Si no reconoce nada, tocá «Detectar chat» de nuevo o marcalo «a mano…» un poco más grande.",
            "Probar captura ahora",
            capture_test,
        ),
        Step(
            "Leé el chat traducido",
            "Cada mensaje en otro idioma se traduce encima de sí mismo: el original queda borroso debajo y el "
            "nombre del jugador sigue visible. Mientras se traduce vas a ver «• • •» en su lugar.\n\n"
            "Las burbujas de texto sobre la cabeza de los jugadores también se traducen (se puede apagar).\n\n"
            "Si cerrás el chat de Roblox, deja de traducir; al abrirlo, sigue donde estaba.",
        ),
        Step(
            f"Escribí en tu idioma con {hotkey}",
            f"En el juego, apretá {hotkey} y se abre una barra para escribir. Escribí como hablás vos: mientras "
            "escribís ves cómo va a quedar la traducción.\n\n"
            "• Enter: lo traduce y lo manda al chat de Roblox (Bubble solo abre el chat, escribe y envía).\n"
            "• Tab cambia el idioma  ·  ↑ ↓ cambian el tono  ·  Esc cierra.\n\n"
            "Si tu atajo lleva Shift (como «°»), apretá la misma tecla sola: el Shift le llega a Roblox y "
            "mueve la cámara (Shift Lock).\n"
            "¿Preferís otra tecla o un botón del mouse? Tocá «Cambiar…» al lado del atajo.",
        ),
        Step(
            "Nuevo: la voz (beta)",
            "En la sección «Voz · beta»:\n\n"
            "• Subtítulos: lo que te dicen por el chat de voz aparece traducido abajo, en el centro del juego.\n"
            "• Tu voz: mantené apretado el botón lateral del mouse, hablá y soltalo. Los demás te escuchan en su "
            "idioma.\n\n"
            "Para que te escuchen hace falta el micrófono virtual VB-Audio Virtual Cable (gratis) y, en Roblox, "
            "elegir «CABLE Output» como micrófono. Todo el audio se procesa en tu PC.",
        ),
        Step(
            "Últimos consejos",
            "• Poné tu nombre de Roblox en la configuración ([roblox] username) para que tus propios "
            "mensajes no se traduzcan.\n"
            "• Si «/» no abre el chat con tu teclado, cambiá open_chat_key.\n"
            "• La configuración está en %APPDATA%\\Bubble\\config.toml.\n"
            "• Podés volver a ver esta guía con el botón «Tutorial».",
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
        self.win = tk.Toplevel(root, bg=BG)
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
        header = tk.Frame(self.win, bg=BG)
        header.pack(fill="x", padx=24, pady=(20, 4))
        if self._icon:
            tk.Label(header, image=self._icon, bg=BG).pack(side="left", padx=(0, 12))
        # El contador se empaqueta antes que los títulos para que un título largo no lo empuje afuera.
        self.counter = tk.Label(header, font=("Segoe UI", 9), fg=MUTED, bg=BG)
        self.counter.pack(side="right", anchor="n")
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", fill="x", expand=True)
        tk.Label(titles, text="Tutorial de Bubble", font=("Segoe UI", 10), fg=MUTED, bg=BG).pack(anchor="w")
        self.title = tk.Label(
            titles, font=("Segoe UI", 17, "bold"), fg=TEXT, bg=BG, anchor="w", justify="left", wraplength=WIDTH - 200
        )
        self.title.pack(anchor="w", fill="x")

        self.body = tk.Label(
            self.win, font=("Segoe UI", 11), fg=TEXT, bg=BG, justify="left", anchor="nw", wraplength=WIDTH - 80
        )
        self.body.pack(fill="both", expand=True, padx=24, pady=(10, 6))

        self.action_button = self._button(self.win, "", self._run_action, primary=False)

        self.dots = tk.Canvas(self.win, height=14, bg=BG, highlightthickness=0)
        self.dots.pack(fill="x", padx=24, pady=(4, 8))

        bottom = tk.Frame(self.win, bg=BG)
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
        colors = (ACCENT, "#ffffff", ACCENT_HOVER) if primary else ("#eef1f6", TEXT, "#dfe4ec")
        button = tk.Button(
            parent, text=text, command=command, font=("Segoe UI", 10, "bold" if primary else "normal"),
            bg=colors[0], fg=colors[1], activebackground=colors[2], activeforeground=colors[1],
            relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
        )
        button.bind("<Enter>", lambda _e: button.configure(bg=colors[2]))
        button.bind("<Leave>", lambda _e: button.configure(bg=colors[0]))
        return button

    def _link(self, parent, text: str, command) -> tk.Label:
        link = tk.Label(parent, text=text, font=("Segoe UI", 9, "underline"), fg=MUTED, bg=BG, cursor="hand2")
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
            color = ACCENT if i == self.index else DOT_OFF
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
