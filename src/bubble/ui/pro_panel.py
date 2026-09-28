"""La página «Pro»: Bubble Pro, la voz entendida en la nube (Deepgram) con tu propia cuenta.

1. Creás una cuenta en Deepgram (trae crédito gratis) y pegás tu clave: se prueba y se guarda cifrada (cloud/keys.py).
2. Activás Bubble Pro: las voces del juego y tu voz se entienden en la nube; la ventana se pone dorada.
3. «Comparar con mi voz»: decís una frase y ves lo que entiende tu PC y lo que entiende la nube, y cuánto tardan.
4. Cuánto se usó este mes y cuánto cuesta (aproximado).
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import pro
from ..config import save_setting
from . import widgets

if TYPE_CHECKING:
    from .main_window import BubbleWindow

IMPROVES = (
    "Gente que habla rápido o se pisa: se entiende mucho mejor (modelos grandes en servidores con placas de video).",
    "Tu voz en español, con palabras en inglés en la misma frase (\"hagamos pvp\"), y el idioma de cada palabra.",
    "Quién habla (Voz 1, Voz 2…) más preciso.",
    "El texto aparece mientras hablan (~0,3 s) y la frase terminada enseguida.",
    "Tu procesador queda libre para Roblox (el reconocimiento de tu PC usa casi un núcleo).",
)


def _money(value: float, decimals: int = 2) -> str:
    """Número con coma decimal (como se escribe en español)."""
    return f"{value:.{decimals}f}".replace(".", ",")


class ProPanel:
    def __init__(self, app: BubbleWindow) -> None:
        self.app = app
        self.config = app.config.pro
        self.enabled_var = tk.BooleanVar(value=self.config.enabled)
        self.diarize_var = tk.BooleanVar(value=self.config.diarize)
        self.key_var = tk.StringVar()
        self._busy = False

    def build_page(self, page) -> None:
        colors = widgets.palette()
        box = widgets.card(page, "✦ Bubble Pro", "Las voces del juego y tu voz se entienden en la nube, en vez de en tu "
                                                 "procesador. La traducción sigue con tu suscripción de Claude.")
        for line in IMPROVES:
            widgets.muted(box, f"• {line}")

        box = widgets.card(page, "Cómo se paga", "Con tu propia cuenta de Deepgram (el servicio de la nube). No es una "
                                                 "suscripción: se paga por minuto de voz que se le manda.")
        widgets.muted(box, f"• ~{_money(pro.PRICE_PER_MIN * 60)} US$ por hora de voz (saber quién habla: "
                           f"+{_money(pro.DIARIZE_PER_MIN * 60)} US$). Solo se manda cuando alguien habla: los "
                           "silencios no se pagan.")
        widgets.muted(box, f"• La cuenta nueva trae {pro.FREE_CREDIT_USD} US$ gratis: cientos de horas de partidas.")
        widgets.muted(box, "• Si se queda sin saldo, Bubble vuelve solo al reconocimiento de tu PC y te avisa.")

        box = widgets.card(page, "Tu clave de Deepgram")
        widgets.muted(box, "1. Creá tu cuenta (gratis). 2. En Deepgram: «API Keys» → «Create a New API Key» → copiala. "
                           "3. Pegala acá. Se guarda cifrada: solo tu usuario de Windows la puede leer.")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Crear cuenta en Deepgram", command=lambda: webbrowser.open(pro.SIGNUP_URL)).pack(
            side="left")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 0))
        self.key_entry = ttk.Entry(row, textvariable=self.key_var, show="•")
        self.key_entry.pack(side="left", fill="x", expand=True)
        self.save_button = ttk.Button(row, text="Guardar y probar", command=self._save_key, style="Accent.TButton")
        self.save_button.pack(side="left", padx=(8, 0))
        self.key_state = ttk.Label(box, text="", foreground=colors["muted"])
        self.key_state.pack(anchor="w", pady=(6, 0))

        box = widgets.card(page, "Activar")
        self.switch = ttk.Checkbutton(box, text="Bubble Pro", variable=self.enabled_var, command=self._toggle,
                                      style="Switch.TCheckbutton")
        self.switch.pack(anchor="w")
        ttk.Checkbutton(box, text="Quién habla según la nube (Voz 1, Voz 2…)", variable=self.diarize_var,
                        command=self._toggle_diarize, style="Switch.TCheckbutton").pack(anchor="w", pady=(8, 0))
        self.usage = widgets.muted(box, "")

        box = widgets.card(page, "Comparar con mi voz", "Decí una frase: ves lo que entiende tu PC y lo que entiende la "
                                                       "nube, y cuánto tarda cada uno.")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.compare_button = ttk.Button(row, text="Hablar", command=self._compare, style="Accent.TButton")
        self.compare_button.pack(side="left")
        self.compare_state = ttk.Label(row, text="", foreground=colors["muted"])
        self.compare_state.pack(side="left", padx=12)
        self.compare_result = widgets.muted(box, "")
        self.refresh()

    # ------------------------------------------------------------ estado
    def refresh(self) -> None:
        from ..cloud.keys import load_key

        has_key = bool(load_key())
        if has_key and not self.key_var.get():
            self.key_state.configure(text="✓ Tenés una clave guardada.")
        elif not has_key:
            self.key_state.configure(text="Todavía no hay una clave.")
        self.switch.state(["!disabled"] if has_key else ["disabled"])
        self.enabled_var.set(pro.active())
        minutes, cost = pro.month_usage()
        self.usage.configure(text=f"Este mes: {minutes:.0f} min de voz en la nube (~{_money(cost)} US$)."
                             if minutes >= 0.5 else "Este mes todavía no se usó la nube.")
        self.compare_button.state(["!disabled"] if has_key else ["disabled"])

    def _ui(self, action) -> None:
        self.app.events.put(("call", action))

    def _save_key(self) -> None:
        key = self.key_var.get().strip()
        if self._busy or not key:
            return
        self._busy = True
        self.save_button.state(["disabled"])
        self.key_state.configure(text="Probando la clave…", foreground=widgets.palette()["muted"])

        def work() -> None:
            from ..cloud.deepgram import check_key
            from ..cloud.keys import save_key

            ok, message = check_key(key)
            if ok:
                save_key(key)

            def show() -> None:
                self._busy = False
                self.save_button.state(["!disabled"])
                colors = widgets.palette()
                self.key_state.configure(text=("✓ " if ok else "✗ ") + message +
                                         (" Guardada. Ya podés activar Bubble Pro." if ok else ""),
                                         foreground=colors["good"] if ok else colors["bad"])
                if ok:
                    self.key_var.set("")
                self.refresh()

            self._ui(show)

        threading.Thread(target=work, name="bubble-pro-clave", daemon=True).start()

    def _toggle(self) -> None:
        self.app.set_pro(self.enabled_var.get())

    def _toggle_diarize(self) -> None:
        self.config.diarize = self.diarize_var.get()
        save_setting("pro", "diarize", self.config.diarize)
        if pro.active():
            self.app.voice_panel.pro_changed()

    # ------------------------------------------------------------ comparar
    def _compare(self) -> None:
        if self._busy:
            return
        voice = self.app.voice_panel
        self._busy = True
        self.compare_button.state(["disabled"])
        self.compare_state.configure(text="Te escucho… decí una frase y hacé una pausa.")
        self.compare_result.configure(text="")

        def work() -> None:
            from ..cloud.deepgram import CloudError, DeepgramClip
            from ..cloud.keys import load_key
            from ..voice.checks import record_phrase

            try:
                language = self.app.config.user.language
                audio = record_phrase(voice._my_microphone(), max_s=12, quiet_s=0.9, wait_s=6)
                if not len(audio):
                    self._ui(lambda: self.compare_state.configure(text="No te escuché. Probá de nuevo."))
                    return
                self._ui(lambda: self.compare_state.configure(text="Comparando…"))
                started = time.perf_counter()
                mine = voice.models[0].transcribe(audio, language=language, hint=voice.profile.hint, clean=True)
                local_s = time.perf_counter() - started
                started = time.perf_counter()
                try:
                    cloud = DeepgramClip(load_key()).transcribe(audio, language=language)
                    cloud_text = cloud.text if cloud else "(nada)"
                except CloudError as exc:
                    cloud_text = f"(no respondió: {exc})"
                cloud_s = time.perf_counter() - started
                text = (f"Tu PC ({_money(local_s, 1)} s): «{mine.text if mine else '(nada)'}»\n"
                        f"La nube ({_money(cloud_s, 1)} s): «{cloud_text}»")
                self._ui(lambda: (self.compare_result.configure(text=text), self.compare_state.configure(text="")))
            except Exception as exc:  # noqa: BLE001 - se muestra
                message = f"No se pudo probar: {exc}"
                self._ui(lambda: self.compare_state.configure(text=message))
            finally:
                def done() -> None:
                    self._busy = False
                    self.compare_button.state(["!disabled"])
                    self.refresh()

                self._ui(done)

        voice._prepare(lambda: threading.Thread(target=work, name="bubble-pro-comparar", daemon=True).start())
