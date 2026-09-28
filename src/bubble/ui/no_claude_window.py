"""«Para traducir, Bubble necesita Claude»: si no tenés Claude (o no tiene suscripción), dos caminos.

1. Bubble Pro con los créditos gratis de Deepgram: traduce sin Claude (ver cloud/agent.py). Se avisa cuánto gasta.
2. Conectar Claude (Pro o Max): iniciar sesión, o ver cómo conseguirlo. Con Claude la traducción no gasta créditos.
"""

from __future__ import annotations

import subprocess
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import pro, system
from . import motion, widgets

if TYPE_CHECKING:
    from .main_window import BubbleWindow

INTRO = {
    "sin_claude": "Bubble traduce con Claude, y en esta PC no está Claude Code.",
    "sin_sesion": "Bubble traduce con Claude, y Claude Code no tiene la sesión iniciada.",
    "gratis": "Bubble traduce con Claude Code, y tu cuenta de Claude es la gratuita, que no lo incluye.",
    "sin_credito": "Bubble Pro estaba traduciendo sin Claude y la cuenta de Deepgram se quedó sin crédito.",
    "clave": "Bubble Pro estaba traduciendo sin Claude y Deepgram dejó de aceptar tu clave.",
}


class NoClaudeWindow:
    def __init__(self, app: BubbleWindow, reason: str) -> None:
        self.app, self.reason = app, reason
        self._waiting_login = False
        self.window, body = widgets.dialog(app.root, "Bubble necesita Claude", width=560)
        colors = widgets.palette()
        title = ("Se terminó el crédito de Deepgram" if reason == "sin_credito" else
                 "Para traducir, Bubble necesita Claude")
        ttk.Label(body, text=title, font="SunValleySubtitleFont").pack(anchor="w")
        widgets.muted(body, INTRO.get(reason, INTRO["sin_sesion"]) + " Tenés dos caminos:", wrap=500, pady=(2, 6))

        # ---- 1. Bubble Pro con los créditos
        box = widgets.card(body, pady=(4, 0))
        label = ttk.Label(box, text="✦ Bubble Pro, con créditos gratis", font="SunValleyBodyStrongFont",
                          foreground=pro.gold())
        label.gold = True
        label.pack(anchor="w")
        widgets.muted(box, "Traduce en la nube, sin Claude, con el crédito de regalo de una cuenta nueva de Deepgram "
                           f"({pro.FREE_CREDIT_USD} US$, sin tarjeta). Además entiende y dice las voces en la nube. "
                           "Mientras no conectes Claude, Pro queda activado (Basic necesita Claude).", wrap=500,
                      pady=(2, 8))
        row = ttk.Frame(box)
        row.pack(fill="x")
        ttk.Button(row, text="1. Crear cuenta en Deepgram", command=lambda: webbrowser.open(pro.SIGNUP_URL)).pack(
            side="left")
        widgets.muted(box, "2. En Deepgram: «API Keys» → «Create a New API Key» → copiala.  3. Pegala acá:", wrap=500,
                      pady=(8, 4))
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(row, textvariable=self.key_var, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        self.activate = ttk.Button(row, text="Activar Bubble Pro", style="Accent.TButton", command=self._activate)
        self.activate.pack(side="left", padx=(8, 0))
        self.key_state = widgets.muted(box, "", wrap=500, pady=(4, 0))
        ttk.Label(box, text=pro.NO_CLAUDE_WARNING, font="SunValleyCaptionFont", foreground=colors["warn"], wraplength=500,
                  justify="left").pack(anchor="w", pady=(8, 0))

        # ---- 2. Conectar Claude
        box = widgets.card(body, "Conectá Claude",
                           "Con Claude Pro o Max, Bubble traduce con tu suscripción y no gasta créditos (y podés "
                           "volver a Basic). Si no tenés, Claude Pro alcanza, aunque sea por un mes.")
        row = ttk.Frame(box)
        row.pack(fill="x")
        installed = reason != "sin_claude"
        self.login = ttk.Button(row, text="Iniciar sesión en Claude" if installed else "Instalar Claude Code",
                                command=self._login if installed else self._install)
        self.login.pack(side="left")
        link = ttk.Label(row, text="Ver Claude Pro", font="SunValleyCaptionFont", foreground=colors["accent"],
                         cursor="hand2")
        link.pack(side="left", padx=(12, 0))
        link.bind("<Button-1>", lambda _event: webbrowser.open(system.PRO_URL))
        self.login_state = widgets.muted(box, "", wrap=500, pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Más tarde", command=self._later).pack(side="right")
        self.window.protocol("WM_DELETE_WINDOW", self._later)
        self.window.bind("<Escape>", lambda _event: self._later())
        widgets.present(self.window, app.root)

    # ------------------------------------------------------------ Bubble Pro
    def _activate(self) -> None:
        key = self.key_var.get().strip()
        if not key:
            self.key_state.configure(text="Pegá tu clave de Deepgram (paso 3).", foreground=widgets.palette()["warn"])
            return
        self.activate.state(["disabled"])
        self.key_state.configure(text="Probando la clave…", foreground=widgets.palette()["muted"])

        def work() -> None:
            from ..cloud.deepgram import check_key
            from ..cloud.keys import save_key

            ok, message = check_key(key)
            if ok:
                save_key(key)
            self.app.events.put(("call", lambda: self._activated(ok, message)))

        threading.Thread(target=work, name="bubble-sin-claude-clave", daemon=True).start()

    def _activated(self, ok: bool, message: str) -> None:
        colors = widgets.palette()
        if not ok:
            self.activate.state(["!disabled"])
            self.key_state.configure(text=f"✗ {message}", foreground=colors["bad"])
            return
        self.key_var.set("")
        self.key_state.configure(text="✓ ¡Listo! Bubble Pro activado: ya traduce sin Claude.",
                                 foreground=colors["good"])
        self.app.use_cloud_translation()
        self.window.after(1600, self.close)

    # ------------------------------------------------------------ Claude
    def _install(self) -> None:
        from .. import install

        install._install_claude(lambda *_args: None)
        self.login_state.configure(text="Se abrió el instalador de Claude Code. Cuando termine, tocá «Iniciar sesión "
                                        "en Claude».")
        self.login.configure(text="Iniciar sesión en Claude", command=self._login)

    def _login(self) -> None:
        from .. import install

        try:
            install._login_claude(lambda *_args: None)
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            self.login_state.configure(text=f"No se pudo abrir Claude Code ({exc}).")
            return
        self.login_state.configure(text="Se abrió Claude Code: entrá con tu cuenta en el navegador y después tocá "
                                        "«Listo, revisar».")
        self.login.configure(text="Listo, revisar", command=self._recheck, style="Accent.TButton")

    def _recheck(self) -> None:
        self.login.state(["disabled"])
        self.login_state.configure(text="Revisando tu cuenta de Claude…")

        def work() -> None:
            problem = system.claude_problem(system.claude_status())
            self.app.events.put(("call", lambda: self._rechecked(problem)))

        threading.Thread(target=work, name="bubble-revisar-claude", daemon=True).start()

    def _rechecked(self, problem: str) -> None:
        self.login.state(["!disabled"])
        if problem in system.NO_CLAUDE:
            self.login_state.configure(text=system.CLAUDE_ADVICE[problem][1].split(" Mientras")[0],
                                       foreground=widgets.palette()["warn"])
            return
        self.login_state.configure(text="✓ ¡Claude conectado! La traducción ya no gasta créditos.",
                                   foreground=widgets.palette()["good"])
        self.app.claude_connected()
        self.window.after(1600, self.close)

    def _later(self) -> None:
        self.close()

    def close(self) -> None:
        motion.vanish(self.window, self.window.destroy)
