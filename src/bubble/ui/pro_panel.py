"""La página «✦ Pro»: Bubble Pro, la voz entendida y hablada en la nube (Deepgram) con tu propia cuenta.

1. Qué cambia de Basic a Pro (lado a lado) y el interruptor (en el juego: Ctrl+P en la barra para escribir).
2. Las voces de Pro: personalidad (alegre, canchera o tranquila) y una prueba.
3. Tu clave: se prueba y se guarda cifrada (cloud/keys.py).
4. Cuánto se usó este mes y cómo se ahorra.
5. «Comparar con mi voz»: lo que entiende tu PC y lo que entiende la nube, y cuánto tarda cada uno.
"""

from __future__ import annotations

import itertools
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

# Basic y Pro, lado a lado: (qué, Basic, Pro)
COMPARISON = (
    ("Entender voces", "Whisper en tu PC", "Nova-3 en la nube: entiende a los que hablan rápido o se pisan"),
    ("Idiomas", "Uno por frase", "Más de 60, y mezclados en la misma frase (\"hagamos pvp\")"),
    ("Voces que hablan por vos", "Las de tu PC", "Naturales y con personalidad (hay acento argentino)"),
    ("Tu voz traducida", "Suena cuando está lista", "Empieza a sonar en ~0,35 s"),
    ("Tu procesador", "Trabaja para la voz", "Queda libre para Roblox"),
    ("Costo", "Gratis", "Por uso: ~0,35 US$ por hora de voz (200 US$ gratis al empezar)"),
)
SAVINGS = ("Solo se manda cuando alguien habla: los silencios no se pagan.",
           "Las voces lejanas (fuera del radio de escucha) y los ruidos no se mandan.",
           "Fuera del juego no se escucha nada.",
           "Una frase que ya se dijo (\"gg\", \"gracias\") no se vuelve a pagar.",
           "Quién habla lo reconoce tu PC, gratis.")
SAMPLE = {"es": "¡Buenísimo! Esperame en la torre, ya voy.", "en": "Nice! Wait for me at the tower, I'm coming.",
          "pt": "Boa! Me espera na torre, já tô indo.", "fr": "Trop bien ! Attends-moi à la tour, j'arrive.",
          "de": "Super! Warte am Turm auf mich, ich komme.", "it": "Grande! Aspettami alla torre, arrivo.",
          "nl": "Top! Wacht bij de toren op me, ik kom eraan.", "ja": "いいね！塔で待ってて、すぐ行くよ。"}


def _gold(label: ttk.Label) -> ttk.Label:
    """Un texto de Pro: siempre dorado, también en Basic (ver app_view.recolor)."""
    label.gold = True
    return label


def _money(value: float, decimals: int = 2) -> str:
    """Número con coma decimal (como se escribe en español)."""
    return f"{value:.{decimals}f}".replace(".", ",")


class ProPanel:
    def __init__(self, app: BubbleWindow) -> None:
        self.app = app
        self.config = app.config.pro
        self.enabled_var = tk.BooleanVar(value=self.config.enabled)
        self.diarize_var = tk.BooleanVar(value=self.config.diarize)
        self.voices_var = tk.BooleanVar(value=self.config.voices)
        self.personality_var = tk.StringVar(value=self.config.personality)
        self.key_var = tk.StringVar()
        self.built = False
        self._busy = False
        self._locked: list[tuple[ttk.Frame, ttk.Label]] = []  # tarjetas de Pro y su cartelito

    def build_page(self, page) -> None:
        from ..cloud.speak import PERSONALITIES

        colors = widgets.palette()
        gold = pro.gold()
        hero = widgets.card(page)
        _gold(ttk.Label(hero, text="✦ Bubble Pro", font="SunValleySubtitleFont", foreground=gold)).pack(anchor="w")
        widgets.muted(hero, "La mejor experiencia: entiende a todos (aunque hablen rápido o mezclen idiomas) y habla por "
                            "vos con voces naturales. La traducción sigue con tu suscripción de Claude.",
                      pady=(2, 10))
        row = ttk.Frame(hero)
        row.pack(fill="x")
        self.switch = ttk.Checkbutton(row, text="Bubble Pro", variable=self.enabled_var, command=self._toggle,
                                      style="Switch.TCheckbutton")
        self.switch.pack(side="left")
        self.plan_label = ttk.Label(row, text="", font="SunValleyCaptionFont", foreground=colors["muted"])
        self.plan_label.pack(side="right")
        widgets.muted(hero, "En el juego: Ctrl+P en la barra para escribir cambia entre Basic y Pro al instante.")

        box = widgets.card(page, "Basic y Pro")
        grid = ttk.Frame(box)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1, uniform="plan")
        grid.columnconfigure(2, weight=1, uniform="plan")
        ttk.Label(grid, text="Basic", font="SunValleyBodyStrongFont").grid(row=0, column=1, sticky="w", padx=(10, 0))
        _gold(ttk.Label(grid, text="✦ Pro", font="SunValleyBodyStrongFont", foreground=gold)).grid(
            row=0, column=2, sticky="w", padx=(10, 0))
        for index, (what, basic, cloud) in enumerate(COMPARISON, start=1):
            ttk.Label(grid, text=what, font="SunValleyCaptionFont", foreground=colors["muted"], wraplength=110,
                      justify="left").grid(row=index, column=0, sticky="nw", pady=(8, 0))
            ttk.Label(grid, text=basic, font="SunValleyCaptionFont", wraplength=150, justify="left").grid(
                row=index, column=1, sticky="nw", padx=(10, 0), pady=(8, 0))
            _gold(ttk.Label(grid, text=cloud, font="SunValleyCaptionFont", foreground=gold, wraplength=170,
                            justify="left")).grid(row=index, column=2, sticky="nw", padx=(10, 0), pady=(8, 0))

        box = self._pro_only(page, "Las voces de Pro", "Hablan por vos (tu voz traducida y Ctrl+Enter). En los idiomas "
                                                     "que la nube no tiene (portugués…), la voz de tu PC.")
        row = widgets.label_row(box, "Personalidad")
        widgets.segmented(row, self.personality_var, PERSONALITIES, self._change_personality).pack(side="right")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 2))
        ttk.Checkbutton(row, text="Usar las voces de la nube", variable=self.voices_var, command=self._toggle_voices,
                        style="Switch.TCheckbutton").pack(side="left")
        self.try_button = ttk.Button(row, text="Probar voz Pro", command=self._try_voice)
        self.try_button.pack(side="right")
        self.try_state = widgets.muted(box, "")

        box = widgets.card(page, "Tu clave de Deepgram", "Pro usa tu propia cuenta de Deepgram (el servicio de la "
                                                          "nube): pagás solo lo que usás.")
        widgets.muted(box, "1. Creá tu cuenta (gratis, trae 200 US$ de regalo). 2. En Deepgram: «API Keys» → «Create "
                           "a New API Key» → copiala. 3. Pegala acá. Se guarda cifrada: solo tu usuario de Windows la "
                           "puede leer.")
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

        box = self._pro_only(page, "Gasto y ahorro")
        self.usage = ttk.Label(box, text="", font="SunValleyBodyStrongFont")
        self.usage.pack(anchor="w")
        for line in SAVINGS:
            widgets.muted(box, f"• {line}")
        ttk.Checkbutton(box, text="Quién habla según la nube (+0,12 US$ por hora)", variable=self.diarize_var,
                        command=self._toggle_diarize, style="Switch.TCheckbutton").pack(anchor="w", pady=(10, 0))
        widgets.muted(box, "Más preciso cuando varios hablan a la vez. Sin esto lo hace tu PC, gratis.")

        box = self._pro_only(page, "Comparar con mi voz", "Decí una frase: ves lo que entiende tu PC y lo que entiende la "
                                                       "nube, y cuánto tarda cada uno.")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.compare_button = ttk.Button(row, text="Hablar", command=self._compare, style="Accent.TButton")
        self.compare_button.pack(side="left")
        self.compare_state = ttk.Label(row, text="", foreground=colors["muted"])
        self.compare_state.pack(side="left", padx=12)
        self.compare_result = widgets.muted(box, "")
        self.built = True
        self.app.plan_hooks.append(self.apply_plan)
        self.apply_plan(False)
        self.refresh()

    def _pro_only(self, page, title: str, subtitle: str = ""):
        """Una tarjeta que solo se usa con Pro: en Basic queda difuminada y bloqueada (ver apply_plan)."""
        box = widgets.card(page, title, subtitle)
        note = ttk.Label(box.master, text="", font="SunValleyCaptionFont")
        note.pack(anchor="w", before=box, pady=(0, 2))
        self._locked.append((box, note))
        return box

    def apply_plan(self, animate: bool = True) -> None:
        """En Basic, lo de Pro se ve pero difuminado y no se puede tocar ni probar (así nada anda a medias); en Pro,
        se desbloquea con una animación."""
        on = pro.active()
        colors = widgets.palette()
        for box, note in self._locked:
            widgets.dim(box, not on, animate)
            note.configure(text="✦  Incluido en tu plan Pro" if on else "🔒  Solo en Pro · activalo arriba",
                           foreground=pro.gold() if on else colors["muted"])

    # ------------------------------------------------------------ estado
    def refresh(self) -> None:
        self.enabled_var.set(pro.active())
        if not self.built:
            return  # la página se arma la primera vez que la abrís
        from ..cloud.keys import load_key

        has_key = bool(load_key())
        if has_key and not self.key_var.get():
            self.key_state.configure(text="✓ Tenés una clave guardada.")
        elif not has_key:
            self.key_state.configure(text="Todavía no hay una clave.")
        self.switch.state(["!disabled"] if has_key else ["disabled"])  # (lo demás de Pro: ver apply_plan)
        self.enabled_var.set(pro.active())
        self.plan_label.configure(text="Estás usando Pro" if pro.active() else
                                  ("Estás usando Basic" if has_key else "Primero guardá tu clave (abajo)"))
        minutes, cost = pro.month_usage()
        chars = pro.month_characters()
        letters = f"{chars:,}".replace(",", ".")
        self.usage.configure(text=f"Este mes: {minutes:.0f} min de voz entendida y {letters} letras dichas · ~"
                                  f"{_money(cost)} US$" if minutes >= 0.5 or chars
                             else "Este mes todavía no se usó la nube.")

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

    def _toggle_voices(self) -> None:
        self.config.voices = self.voices_var.get()
        save_setting("pro", "voices", self.config.voices)
        if pro.active():
            self.app.voice_panel.pro_changed()

    def _change_personality(self) -> None:
        self.config.personality = self.personality_var.get()
        save_setting("pro", "personality", self.config.personality)
        if pro.active():
            self.app.voice_panel.pro_changed()
        self._try_voice()

    # ------------------------------------------------------------ probar la voz de Pro
    def _try_voice(self) -> None:
        """Una frase con la voz de Pro (la personalidad elegida), en tus auriculares. Anda aunque estés en Basic."""
        if self._busy:
            return
        self._busy = True
        self.try_state.configure(text="Preparando la voz…")
        voice = self.app.voice_panel
        language = "en"
        if self.app.translator is not None:
            language = self.app.translator.outgoing_target()

        def work() -> None:
            message = ""
            try:
                from ..cloud.keys import load_key
                from ..cloud.speak import CloudVoices, voice_name
                from ..voice import audio as audio_io
                from ..voice.tts import Voices

                voice.voices = voice.voices or Voices()
                cloud = CloudVoices(load_key(), voice.voices, personality=self.config.personality)
                code = language.split("-")[0].lower()
                started = time.perf_counter()
                opened = cloud.stream(SAMPLE.get(code, SAMPLE["en"]), language)
                if opened is None:
                    message = "La nube no tiene voz en ese idioma: se usa la de tu PC."
                else:
                    rate, pieces = opened
                    first = next(pieces, None)
                    waited = time.perf_counter() - started
                    if first is not None:
                        name = (voice_name(language, voice.voices.gender, self.config.personality) or "")
                        message = f"{name.split('-')[2].capitalize()} · empezó a sonar en {_money(waited)} s"
                        audio_io.play_stream(audio_io.monitor_output(), itertools.chain([first], pieces), rate)
            except Exception as exc:  # noqa: BLE001 - se muestra
                message = f"No se pudo probar: {exc}"
            finally:
                def done() -> None:
                    self._busy = False
                    self.try_state.configure(text=message)
                    self.refresh()

                self._ui(done)

        threading.Thread(target=work, name="bubble-pro-voz", daemon=True).start()

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
                local = voice.models[0]
                mine = local.transcribe(audio, language=language, hint=voice.profile.hint, clean=True)
                local_s = time.perf_counter() - started
                started = time.perf_counter()
                try:
                    cloud = DeepgramClip(load_key(), keyterms=voice._my_keyterms).transcribe(audio, language=language)
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
