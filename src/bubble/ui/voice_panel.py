"""Sección «Voz · beta» de la ventana principal: subtítulos de lo que te dicen y tu voz traducida.

Todo el audio se procesa en tu PC (Whisper para entender, Piper para hablar); Claude solo traduce el texto.
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import win32
from ..config import save_setting
from .subtitles import SubtitleView
from .theme import MUTED, strong_font

if TYPE_CHECKING:
    from .main_window import BubbleWindow

CABLE_HELP = ("Para que los demás escuchen tu voz traducida hace falta un micrófono virtual: instalá VB-Audio "
              "Virtual Cable (gratis, vb-audio.com/Cable), reiniciá Bubble y en Roblox elegí «CABLE Output» como "
              "micrófono. Mientras tanto, tu voz traducida se escucha por tus parlantes (para probar).")


class VoicePanel:
    def __init__(self, app: BubbleWindow, parent) -> None:
        self.app = app
        self.config = app.config.voice
        self.transcriber = None
        self.voices = None
        self.listener = None
        self.speaker = None
        self.subtitles = SubtitleView()
        self._preparing = False

        frame = ttk.LabelFrame(parent, text="Voz · beta")
        frame.pack(fill="x", padx=8, pady=4)
        self.subtitles_var = tk.BooleanVar(value=self.config.subtitles)
        ttk.Checkbutton(frame, text="Subtítulos de lo que te dicen por voz", variable=self.subtitles_var,
                        command=self._toggle_subtitles).grid(row=0, column=0, sticky="w", padx=6, pady=(4, 2))
        speak_row = ttk.Frame(frame)
        speak_row.grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.speak_var = tk.BooleanVar(value=self.config.speak)
        ttk.Checkbutton(speak_row, text="Traducir mi voz: mantené apretado", variable=self.speak_var,
                        command=self._toggle_speak).pack(side="left")
        self.ptt_label = ttk.Label(speak_row, text=win32.describe_binding(self.config.push_to_talk),
                                   font=strong_font())
        self.ptt_label.pack(side="left", padx=(6, 8))
        ttk.Button(speak_row, text="Cambiar…", command=self._change_key).pack(side="left")
        ttk.Label(speak_row, text="hablá y soltalo.", foreground=MUTED).pack(side="left", padx=6)
        self.status = ttk.Label(frame, text="", foreground=MUTED, wraplength=760, justify="left")
        self.status.grid(row=2, column=0, sticky="w", padx=6, pady=(2, 6))
        self._tick()

    # --- arranque (cuando la app ya tiene Claude listo)
    def start(self) -> None:
        if self.config.subtitles or self.config.speak:
            self._prepare(self._apply)

    def stop(self) -> None:
        for part in (self.listener, self.speaker):
            if part:
                part.stop()

    def _set_status(self, text: str) -> None:
        self.app.events.put(("voice_status", text))

    def _prepare(self, then) -> None:
        """Carga (y la primera vez descarga) el reconocimiento de voz, sin trabar la ventana."""
        if self.transcriber is not None and self.transcriber.loaded:
            then()
            return
        if self._preparing:
            return
        self._preparing = True

        def work() -> None:
            from ..voice.stt import Transcriber
            from ..voice.tts import Voices

            try:
                if self.transcriber is None:
                    self.transcriber = Transcriber(self.config.model)
                    self.voices = Voices()
                self._set_status(f"Preparando el reconocimiento de voz ({self.transcriber.model_name}). La primera vez "
                                 "se descarga (~150 MB) y queda en tu PC…")
                self.transcriber.load()
                self._set_status("")
                self.app.events.put(("voice_ready", then))
            except ImportError:
                self._set_status('Falta instalar la parte de voz: .venv\\Scripts\\python.exe -m pip install -e ".[voz]"')
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No se pudo preparar la voz: {exc}")
            finally:
                self._preparing = False

        threading.Thread(target=work, name="bubble-voz-prepara", daemon=True).start()

    def _apply(self) -> None:
        """Prende o apaga cada parte según las casillas."""
        from ..voice.pipelines import VoiceListener, VoiceSpeaker

        if self.subtitles_var.get():
            if self.listener is None:
                self.listener = VoiceListener(self.transcriber, self._heard,
                                              on_error=lambda msg: self._set_status(msg))
            self.listener.start()
        elif self.listener:
            self.listener.stop()

        if self.speak_var.get():
            if self.speaker is None:
                binding = win32.parse_binding(self.config.push_to_talk)
                self.speaker = VoiceSpeaker(self.transcriber, self.voices, self._translate_mine, binding.vk,
                                            self.app.config.user.language, on_event=self._spoke,
                                            on_playing=self._playing)
            self.speaker.start()
            output = self.speaker.output
            self._set_status(f"Tu voz traducida sale por «{output.name}»: en Roblox elegí «CABLE Output» como micrófono."
                             if output.is_cable else CABLE_HELP)
        elif self.speaker:
            self.speaker.stop()
        if not self.speak_var.get() and not self.subtitles_var.get():
            self._set_status("")

    # --- casillas
    def _toggle_subtitles(self) -> None:
        self.config.subtitles = self.subtitles_var.get()
        save_setting("voice", "subtitles", self.config.subtitles)
        self._toggled(self.config.subtitles)

    def _toggle_speak(self) -> None:
        self.config.speak = self.speak_var.get()
        save_setting("voice", "speak", self.config.speak)
        self._toggled(self.config.speak)

    def _toggled(self, turned_on: bool) -> None:
        if not self.app.ready:
            self._set_status("Se activa apenas Bubble termine de conectarse con Claude.")
        elif turned_on:
            self._prepare(self._apply)
        else:
            self._apply_if_ready()

    def _apply_if_ready(self) -> None:
        if self.transcriber is not None and self.transcriber.loaded:
            self._apply()

    def _change_key(self) -> None:
        from .overlays import HotkeyCaptureDialog

        def done(spec: str | None) -> None:
            if spec:
                self.config.push_to_talk = spec
                save_setting("voice", "push_to_talk", spec)
                self.ptt_label.configure(text=win32.describe_binding(spec))
                if self.speaker:
                    self.speaker.vk = win32.parse_binding(spec).vk

        HotkeyCaptureDialog(self.app.root, done)

    # --- lo que te dicen: frase → traducción → subtítulo
    def _heard(self, transcript) -> None:
        mine = self.app.config.user.language.split("-")[0]
        if transcript.language == mine and transcript.probability > 0.7:
            return  # ya está en tu idioma: no hace falta subtitularlo

        async def translate() -> None:
            result = await self.app.translator.translate_incoming(transcript.text, "Voz")
            if result.status != "error" and result.translation.strip():
                self.app.events.put(("voice_subtitle", (transcript.text, result.translation, transcript.language)))

        self.app.runner.submit(translate())

    # --- tu voz: texto en tu idioma → (traducción, idioma)
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
        # Por los parlantes (sin micrófono virtual), tu voz traducida no se tiene que subtitular como si fuera de otro.
        if self.speaker and not self.speaker.output.is_cable and self.listener:
            self.listener.muted_until = time.monotonic() + seconds + 0.5

    # --- subtítulos en pantalla
    def show(self, original: str, translation: str, language: str) -> None:
        self.subtitles.add(original, translation, language)

    def _tick(self) -> None:
        visible = win32.roblox_is_foreground()
        area = self.app._game_area() if visible else None
        self.subtitles.update(area, visible)
        self.app.root.after(120, self._tick)
