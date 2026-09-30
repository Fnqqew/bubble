"""«Tu equipo» (en Pruebas): lo que Bubble detectó de tu PC al abrirse, cómo se adaptó y qué conviene revisar."""

from __future__ import annotations

from tkinter import ttk
from typing import TYPE_CHECKING

from . import widgets

if TYPE_CHECKING:
    from ..system import Plan, System
    from .main_window import BubbleWindow

LEVELS = {"ok": ("✓", "good"), "aviso": ("●", "warn"), "problema": ("✗", "bad")}


class EquipmentCard:
    def __init__(self, app: BubbleWindow, page) -> None:
        self.app = app
        box = widgets.card(page, "Tu equipo", "Cada vez que abrís Bubble reviso tu PC y me acomodo: uso más o "
                                              "menos memoria, elijo cómo leer la pantalla y cómo arrancar la "
                                              "voz según tu internet.")
        self.table = ttk.Frame(box)
        self.table.pack(fill="x")
        self.table.columnconfigure(1, weight=1)
        self.advice = ttk.Frame(box)
        self.advice.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        self.buttons = [ttk.Button(row, text="Revisar de nuevo", command=lambda: self._check(False)),
                        ttk.Button(row, text="Medir internet", command=lambda: self._check(True))]
        for index, button in enumerate(self.buttons):
            button.pack(side="left", padx=(0 if index == 0 else 8, 0))
        self.state = ttk.Label(row, text="", font="SunValleyCaptionFont", foreground=widgets.palette()["muted"])
        self.state.pack(side="left", padx=12)
        if app.system_info is not None:
            self.show(app.system_info, app.system_plan)
        else:
            self._busy("Revisando tu equipo…")

    def _busy(self, text: str) -> None:
        self.state.configure(text=text)
        for button in self.buttons:
            button.state(["disabled"])

    def _check(self, internet: bool) -> None:
        self._busy("Midiendo internet… (unos segundos)" if internet else "Revisando tu equipo…")
        self.app.check_system(internet=internet)

    def show(self, info: System | None, plan: Plan | None) -> None:
        from ..system import summary_lines

        for button in self.buttons:
            button.state(["!disabled"])
        if info is None or plan is None:
            self.state.configure(text="No se pudo revisar (mirá el registro de errores).")
            return
        self.state.configure(text="")
        colors = widgets.palette()
        for child in self.table.winfo_children() + self.advice.winfo_children():
            child.destroy()
        for index, (name, value) in enumerate(summary_lines(info)):
            ttk.Label(self.table, text=name, font="SunValleyCaptionFont", foreground=colors["muted"]).grid(
                row=index, column=0, sticky="nw", padx=(0, 14), pady=1)
            ttk.Label(self.table, text=value, font="SunValleyCaptionFont", wraplength=330, justify="left").grid(
                row=index, column=1, sticky="w", pady=1)
        for item in plan.advice:
            symbol, color = LEVELS.get(item.level, ("•", "muted"))
            row = ttk.Frame(self.advice)
            row.pack(fill="x", pady=2)
            ttk.Label(row, text=symbol, width=2, font="SunValleyBodyStrongFont", foreground=colors[color]).pack(
                side="left", anchor="n")
            texts = ttk.Frame(row)
            texts.pack(side="left", fill="x")
            ttk.Label(texts, text=item.text, font="SunValleyCaptionFont", wraplength=420, justify="left").pack(
                anchor="w")
            if item.action == "claude":  # sin Claude: Bubble Pro con créditos o conectar Claude
                link = ttk.Label(texts, text="Cómo seguir →", font="SunValleyCaptionFont", foreground=colors["accent"],
                                 cursor="hand2")
                link.pack(anchor="w", pady=(2, 0))
                link.bind("<Button-1>", lambda _event: self.app.open_no_claude())
