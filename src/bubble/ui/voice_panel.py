"""La voz: subtítulos de lo que te dicen y tu voz traducida para los demás (página «Voz» de la ventana).

Todo el audio se procesa en tu PC (Whisper para entender, Piper para hablar); Claude solo traduce el texto. Para que
los demás te escuchen, Bubble habla por un micrófono virtual, como Soundpad (ver voice/bridge.py).
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import win32
from ..config import save_setting
from ..state import load_state, update_state
from . import widgets
from .subtitles import SubtitleView, speaker_name

if TYPE_CHECKING:
    from .main_window import BubbleWindow

MODES = {"boton": "Mientras aprieto un botón", "directo": "Directo, sin botón"}
GENDERS = {"femenina": "Femenina", "masculina": "Masculina"}
SAMPLES = {
    "en": "Hi! This is how I'm going to sound.", "pt": "Oi! É assim que eu vou soar.",
    "es": "¡Hola! Así va a sonar mi voz.", "fr": "Salut ! Voilà comment je vais sonner.",
    "de": "Hallo! So werde ich klingen.", "it": "Ciao! Ecco come suonerò.", "ru": "Привет! Вот так я буду звучать.",
    "hi": "नमस्ते! मेरी आवाज़ ऐसी सुनाई देगी।", "pl": "Cześć! Tak będę brzmieć.", "nl": "Hoi! Zo ga ik klinken.",
    "tr": "Merhaba! Sesim böyle olacak.", "id": "Halo! Beginilah suaraku.", "zh": "你好！我的声音听起来是这样的。",
}
NO_VOICE_PACK = 'Falta instalar la parte de voz: .venv\\Scripts\\python.exe -m pip install -e ".[voz]"'


class VoicePanel:
    def __init__(self, app: BubbleWindow) -> None:
        self.app = app
        self.config = app.config.voice
        self.models = None  # (whisper final, whisper rápido o None, voces conocidas)
        self.voices = None
        self.out = None  # la voz sintética (ver voice/pipelines.py)
        self.bridge = None  # tu micrófono pasando al virtual (voice/bridge.py)
        self.listener = None
        self.speaker = None  # tu voz: con tecla o directa
        self.subtitles = SubtitleView()
        self.board = None  # frases a la vista (ver voice/captions.py)
        self._preparing = False
        self.subtitles_var = tk.BooleanVar(value=self.config.subtitles)
        self.speak_var = tk.BooleanVar(value=self.config.speak)
        self.mode_var = tk.StringVar(value=self.config.speak_mode if self.config.speak_mode in MODES else "boton")
        self.hear_var = tk.BooleanVar(value=self.config.hear_myself)
        self.gender_var = tk.StringVar(value=self.config.gender if self.config.gender in GENDERS else "femenina")
        self.speed_var = tk.DoubleVar(value=self.config.speed)
        self.pass_var = tk.BooleanVar(value=self.config.pass_my_voice)
        self.status = None
        self._tick()

    # ------------------------------------------------------------ página «Voz»
    def build_page(self, page) -> None:
        box = widgets.card(page, "Lo que te dicen")
        widgets.switch_row(box, "listen", "Subtítulos de voz", "Quién habla (Voz 1, Voz 2…) y qué dice, en tu idioma. "
                           "Lo que ya está en tu idioma no se subtitula.", self.subtitles_var, self._toggle_subtitles)

        box = widgets.card(page, "Tu voz para los demás")
        widgets.switch_row(box, "mic", "Traducir mi voz", "Hablás en tu idioma y te escuchan en el suyo.",
                           self.speak_var, self._toggle_speak)
        row = widgets.label_row(box, "Cómo", pady=(12, 2))
        widgets.segmented(row, self.mode_var, MODES, self._change_mode).pack(side="right")
        row = widgets.label_row(box, "Botón para hablar")
        ttk.Button(row, text="Cambiar", command=self._change_key).pack(side="right")
        self.ptt_label = ttk.Label(row, text=win32.describe_binding(self.config.push_to_talk),
                                   font="SunValleyBodyStrongFont")
        self.ptt_label.pack(side="right", padx=10)
        widgets.muted(box, "En modo directo no hace falta botón: cada frase que decís sale traducida. Y en la barra "
                           "para escribir, Ctrl+Enter dice en voz lo que escribiste.")

        box = widgets.card(page, "Cómo suena")
        row = widgets.label_row(box, "Voz")
        widgets.segmented(row, self.gender_var, GENDERS, self._change_voice).pack(side="right")
        row = widgets.label_row(box, "Velocidad")
        self.speed_text = ttk.Label(row, text=self._speed_label(), width=10, anchor="e")
        self.speed_text.pack(side="right")
        ttk.Scale(row, from_=0.8, to=1.3, variable=self.speed_var, command=lambda _v: self._change_speed(),
                  length=170).pack(side="right", padx=8)
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 2))
        ttk.Checkbutton(row, text="Escucharla yo también", variable=self.hear_var, command=self._toggle_hear,
                        style="Switch.TCheckbutton").pack(side="left")
        ttk.Button(row, text="Probar voz", command=self._try_voice).pack(side="right")

        box = widgets.card(page, "Micrófono", "Como Soundpad: Bubble habla por un micrófono virtual que suma tu voz "
                                              "real y la traducida. En Roblox lo elegís una sola vez.")
        row = widgets.label_row(box, "Tu micrófono")
        self.mic_box = ttk.Combobox(row, state="readonly", width=30, values=["Buscando…"])
        self.mic_box.set(self.config.mic or "El predeterminado de Windows")
        self.mic_box.bind("<<ComboboxSelected>>", self._change_mic)
        self.mic_box.pack(side="right")
        row = widgets.label_row(box, "Micrófono virtual")
        self.cable_button = ttk.Button(row, text="Instalar (gratis)", command=self._install_cable)
        self.cable_label = ttk.Label(row, text="Revisando…", foreground=widgets.palette()["muted"])
        self.cable_label.pack(side="right")
        ttk.Checkbutton(box, text="Pasar también mi voz real", variable=self.pass_var, command=self._toggle_pass,
                        style="Switch.TCheckbutton").pack(anchor="w", pady=(10, 0))
        self.cable_help = widgets.muted(box, "")
        self.status = widgets.muted(page, "", pady=(0, 8))
        threading.Thread(target=self._scan_devices, name="bubble-dispositivos", daemon=True).start()

    def _speed_label(self) -> str:
        speed = self.speed_var.get()
        return "Normal" if abs(speed - 1.0) < 0.04 else ("Más lenta" if speed < 1 else "Más rápida")

    def _scan_devices(self) -> None:
        try:
            from ..voice import bridge

            mics, cable = bridge.microphones(), bridge.cable_input() is not None
        except Exception:  # noqa: BLE001 - sin la parte de voz instalada
            mics, cable = [], False
        self.app.events.put(("voice_devices", (mics, cable)))

    def show_devices(self, mics: list[str], cable: bool) -> None:
        """(hilo de la ventana) Micrófonos encontrados y si hay micrófono virtual."""
        self.mic_box.configure(values=["El predeterminado de Windows", *mics])
        colors = widgets.palette()
        if cable:
            self.cable_label.configure(text="Instalado ✓", foreground=colors["good"])
            self.cable_button.pack_forget()
            self.cable_help.configure(text="En Roblox: Configuración → Micrófono → «CABLE Output». Mientras Bubble "
                                           "está abierto, por ahí sale tu voz y la traducida.")
        else:
            self.cable_label.configure(text="No instalado", foreground=colors["warn"])
            self.cable_button.pack(side="right", padx=(0, 10))
            self.cable_help.configure(text="Sin micrófono virtual, tu voz traducida suena solo en tus auriculares "
                                           "(sirve para probar). Instalarlo toma un minuto.")

    # ------------------------------------------------------------ arranque (cuando la app ya tiene Claude listo)
    def start(self) -> None:
        if not (self.config.subtitles or self.config.speak):
            return
        if load_state().get("voice_loading"):
            # La última vez Bubble se cerró mientras cargaba la voz: esta vez no se carga sola, así la ventana abre.
            update_state(voice_loading=False)
            self._set_status("La última vez Bubble se cerró mientras preparaba la voz, así que quedó en pausa. "
                             "Para intentar de nuevo, apagá y prendé el interruptor.")
            return
        self._prepare(self._apply)

    def stop(self) -> None:
        for part in (self.listener, self.speaker, self.bridge):
            if part:
                part.stop()
        if self._preparing:
            update_state(voice_loading=False)  # cerraste Bubble a mitad de la descarga: no fue un error

    def _set_status(self, text: str) -> None:
        self.app.events.put(("voice_status", text))

    def _prepare(self, then) -> None:
        """Carga (y la primera vez descarga) el reconocimiento de voz, sin trabar la ventana."""
        if self.models is not None:
            then()
            return
        if self._preparing:
            return
        self._preparing = True

        def work() -> None:
            try:
                from ..voice.asr import FastWhisper, pick_models
                from ..voice.speakers import SpeakerTracker

                quick, final = pick_models()
                if self.config.model not in ("", "auto"):
                    final = self.config.model
                self._set_status("Preparando la voz… La primera vez se descarga (hasta ~500 MB) y queda en tu PC.")
                update_state(voice_loading=True)  # si el proceso se cae acá, el próximo arranque no la carga sola
                models = (FastWhisper(final, 4), FastWhisper(quick, 2) if quick else None, SpeakerTracker())
                update_state(voice_loading=False)
                self._ensure_out()
                self.models = models
                self._set_status("")
                self.app.events.put(("voice_ready", then))
            except ImportError:
                self._set_status(NO_VOICE_PACK)
            except Exception as exc:  # noqa: BLE001
                update_state(voice_loading=False)
                self._set_status(f"No se pudo preparar la voz: {exc}")
            finally:
                self._preparing = False

        threading.Thread(target=work, name="bubble-voz-prepara", daemon=True).start()

    def _ensure_out(self):
        """La voz sintética y, si hay micrófono virtual, el puente con tu micrófono."""
        if self.out is not None:
            return self.out
        from ..voice import bridge
        from ..voice.pipelines import VoiceOut
        from ..voice.tts import Voices

        self.voices = self.voices or Voices()
        self.voices.gender, self.voices.speed = self.config.gender, self.config.speed
        if bridge.cable_input() is not None:
            self.bridge = bridge.MicBridge(self.config.mic)
            self.bridge.enabled = self.config.pass_my_voice
            self.bridge.start()
        self.out = VoiceOut(self.voices, self.config.hear_myself, bridge=self.bridge)
        self.out.listeners.append(self._playing)
        return self.out

    def _apply(self) -> None:
        """Prende o apaga cada parte según los interruptores."""
        from ..voice.captions import CaptionBoard
        from ..voice.live import LiveListener
        from ..voice.pipelines import DirectVoice, VoiceSpeaker

        final, quick, speakers = self.models
        self._ensure_out()
        if self.board is None:
            self.board = CaptionBoard(self.app.config.user.language, self._translate_heard,
                                      on_translated=lambda line: self.app.events.put(("voice_line", line)))
        if self.subtitles_var.get():
            if self.listener is None:
                self.listener = LiveListener(final, self.board.caption, partial_asr=quick, speakers=speakers,
                                             on_error=lambda msg: self._set_status(msg),
                                             native=self.app.config.user.language)
            self.listener.start()
        elif self.listener:
            self.listener.stop()

        direct = self.config.speak_mode == "directo"
        if self.speaker is not None and isinstance(self.speaker, DirectVoice) != direct:
            self.speaker.stop()  # cambiaste de modo
            self.speaker = None
        if self.speak_var.get():
            if self.speaker is None and direct:
                self.speaker = DirectVoice(final, self.out, self._translate_mine, self.app.config.user.language,
                                           partial_asr=quick, on_event=self._spoke,
                                           target=self.app.translator.outgoing_target)
            elif self.speaker is None:
                self.out.warm_up(self.app.translator.outgoing_target())
                binding = win32.parse_binding(self.config.push_to_talk)
                self.speaker = VoiceSpeaker(final, self.out, self._translate_mine, binding.vk,
                                            self.app.config.user.language, on_event=self._spoke)
            self.speaker.start()
            if not self.out.output.is_cable:
                self._set_status("Tu voz traducida suena en tus auriculares. Para que la escuchen los demás, instalá "
                                 "el micrófono virtual (abajo, en «Micrófono»).")
            else:
                self._set_status("")
        elif self.speaker:
            self.speaker.stop()
        if not self.speak_var.get() and not self.subtitles_var.get():
            self._set_status("")

    # ------------------------------------------------------------ interruptores
    def _toggle_subtitles(self) -> None:
        self.config.subtitles = self.subtitles_var.get()
        save_setting("voice", "subtitles", self.config.subtitles)
        self._toggled(self.config.subtitles)

    def _toggle_speak(self) -> None:
        self.config.speak = self.speak_var.get()
        save_setting("voice", "speak", self.config.speak)
        self._toggled(self.config.speak)

    def _toggle_hear(self) -> None:
        self.config.hear_myself = self.hear_var.get()
        save_setting("voice", "hear_myself", self.config.hear_myself)
        if self.out:
            self.out.hear_myself = self.config.hear_myself

    def _toggle_pass(self) -> None:
        self.config.pass_my_voice = self.pass_var.get()
        save_setting("voice", "pass_my_voice", self.config.pass_my_voice)
        if self.bridge:
            self.bridge.enabled = self.config.pass_my_voice

    def _change_mode(self) -> None:
        self.config.speak_mode = self.mode_var.get()
        save_setting("voice", "speak_mode", self.config.speak_mode)
        if self.speak_var.get():
            self._toggled(True)

    def _change_voice(self) -> None:
        self.config.gender = self.gender_var.get()
        save_setting("voice", "gender", self.config.gender)
        if self.voices:
            self.voices.gender = self.config.gender
        self._try_voice()

    def _change_speed(self) -> None:
        self.config.speed = round(float(self.speed_var.get()), 2)
        self.speed_text.configure(text=self._speed_label())
        save_setting("voice", "speed", self.config.speed)
        if self.voices:
            self.voices.speed = self.config.speed

    def _change_mic(self, _event=None) -> None:
        choice = self.mic_box.get()
        self.config.mic = "" if choice.startswith("El predeterminado") else choice
        save_setting("voice", "mic", self.config.mic)
        if self.bridge:
            self.bridge.stop()
            self.bridge.mic_name = self.config.mic
            threading.Timer(0.3, self.bridge.start).start()

    def _toggled(self, turned_on: bool) -> None:
        if not self.app.ready:
            self._set_status("Se activa apenas Bubble termine de prepararse.")
        elif turned_on:
            self._prepare(self._apply)
        else:
            self._apply_if_ready()

    def _apply_if_ready(self) -> None:
        if self.models is not None:
            self._apply()

    def _change_key(self) -> None:
        from .overlays import HotkeyCaptureDialog

        def done(spec: str | None) -> None:
            if spec:
                self.config.push_to_talk = spec
                save_setting("voice", "push_to_talk", spec)
                self.ptt_label.configure(text=win32.describe_binding(spec))
                if self.speaker is not None and hasattr(self.speaker, "vk"):
                    self.speaker.vk = win32.parse_binding(spec).vk

        HotkeyCaptureDialog(self.app.root, done)

    def _try_voice(self) -> None:
        """Una frase de prueba en tus auriculares (no le llega a Roblox)."""

        def work() -> None:
            try:
                from ..voice import audio as audio_io
                from ..voice.tts import Voices

                self.voices = self.voices or Voices()
                self.voices.gender, self.voices.speed = self.config.gender, self.config.speed
                language = self.app.translator.outgoing_target().split("-")[0] if self.app.ready else "en"
                speech = self.voices.synthesize(SAMPLES.get(language, SAMPLES["en"]), language)
                if speech is None:
                    speech = self.voices.synthesize(SAMPLES["en"], "en")
                if speech is not None:
                    audio_io.play(audio_io.monitor_output(), speech.audio, speech.sample_rate)
            except ImportError:
                self._set_status(NO_VOICE_PACK)
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No se pudo probar la voz: {exc}")

        threading.Thread(target=work, name="bubble-prueba-voz", daemon=True).start()

    def _install_cable(self) -> None:
        self.cable_button.configure(state="disabled", text="Descargando…")

        def work() -> None:
            try:
                from ..voice.bridge import install_cable

                message = install_cable()
            except Exception as exc:  # noqa: BLE001
                message = f"No se pudo instalar: {exc}"
            self._set_status(message)
            self.app.events.put(("voice_cable_done", None))

        threading.Thread(target=work, name="bubble-instalar-cable", daemon=True).start()

    def cable_done(self) -> None:
        self.cable_button.configure(state="normal", text="Instalar (gratis)")
        threading.Thread(target=self._scan_devices, daemon=True).start()

    # ------------------------------------------------------------ lo que te dicen: frase → traducción → subtítulo
    def _translate_heard(self, text: str, language: str, speaker: int, on_piece, on_done) -> None:
        async def translate() -> None:
            try:
                result = await self.app.translator.translate_incoming(text, speaker_name(speaker), on_delta=on_piece)
                if result.status == "same_language":
                    on_done(None, native=True)
                else:
                    on_done(result.translation if result.status != "error" and result.translation.strip() else None)
            except Exception:  # noqa: BLE001 - sin traducción queda el original
                on_done(None)

        self.app.runner.submit(translate())

    # ------------------------------------------------------------ tu voz: texto en tu idioma → (traducción, idioma)
    def _translate_mine(self, text: str) -> tuple[str, str] | None:
        translator = self.app.translator
        target = translator.outgoing_target()
        result = self.app.runner.submit(
            translator.translate_outgoing(text, target, tone=self.app.config.user.tone)).result(timeout=25)
        if result.status == "error" or not result.translation.strip():
            return None
        self.app.tracker.mark_sent(result.translation)
        return result.translation, result.target_lang or target

    def _spoke(self, kind: str, text: str) -> None:
        if kind == "traduccion":
            self.app.events.put(("voice_subtitle", ("(vos)", text, "→")))
        elif kind == "error":
            self._set_status(f"Tu voz: {text}")

    def _playing(self, seconds: float) -> None:
        # Si tu voz traducida suena en tus parlantes (sin micrófono virtual, o porque la querés escuchar), no se tiene
        # que subtitular como si fuera de otro.
        out = self.out
        if out and self.listener and (not out.output.is_cable or out.hear_myself):
            self.listener.muted_until = time.monotonic() + seconds + 0.5

    # ------------------------------------------------------------ escribir y que se diga en voz (Ctrl+Enter)
    def say(self, pairs: list[tuple[str, str]], original: str) -> None:
        """Dice las traducciones (idioma, texto) con la voz sintética, en orden. No bloquea."""

        def work() -> None:
            try:
                out = self._ensure_out()
                if not out.output.is_cable:
                    self._set_status("Se escuchó solo en tus auriculares: para que llegue a Roblox, instalá el "
                                     "micrófono virtual (página «Voz»).")
                for language, text in pairs:
                    self.app.events.put(("voice_subtitle", (original, text, language)))
                    if not out.say(text, language):
                        self._set_status(f"No hay voz sintética para el idioma «{language}».")
            except ImportError:
                self._set_status(NO_VOICE_PACK)
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No se pudo decir en voz: {exc}")

        threading.Thread(target=work, name="bubble-escrito-a-voz", daemon=True).start()

    # ------------------------------------------------------------ subtítulos en pantalla
    def show(self, original: str, translation: str, language: str) -> None:
        """Tu voz traducida: se muestra como una frase tuya."""
        if self.board is None:
            from ..voice.captions import CaptionBoard

            self.board = CaptionBoard(self.app.config.user.language, self._translate_heard,
                                      on_translated=lambda line: self.app.events.put(("voice_line", line)))
        self.board.mine(original, translation, language)

    def _tick(self) -> None:
        visible = win32.roblox_is_foreground()
        area = self.app._game_area() if visible else None
        lines = self.board.visible() if self.board is not None else []
        self.subtitles.update(lines, area, visible)
        self.app.root.after(80, self._tick)
