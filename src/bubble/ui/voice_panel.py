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
from ..state import load_state, update_state
from .subtitles import SubtitleView, speaker_name
from .theme import MUTED, strong_font

if TYPE_CHECKING:
    from .main_window import BubbleWindow

MODES = {"boton": "mientras mantengo apretado", "directo": "directo (cada frase que digo)"}
CABLE_HELP = ("Para que los demás escuchen tu voz traducida hace falta un micrófono virtual: instalá VB-Audio "
              "Virtual Cable (gratis, vb-audio.com/Cable), reiniciá Bubble y en Roblox elegí «CABLE Output» como "
              "micrófono. Mientras tanto, tu voz traducida se escucha por tus parlantes (para probar).")


class VoicePanel:
    def __init__(self, app: BubbleWindow, parent) -> None:
        self.app = app
        self.config = app.config.voice
        self.models = None  # (whisper final, whisper rápido o None, voces conocidas)
        self.voices = None
        self.out = None  # la voz sintética (ver voice/pipelines.py)
        self.listener = None
        self.speaker = None  # tu voz: con tecla o directa
        self.subtitles = SubtitleView()
        self.board = None  # frases a la vista (ver voice/captions.py)
        self._preparing = False

        frame = ttk.LabelFrame(parent, text="Voz · beta")
        frame.pack(fill="x", padx=8, pady=4)
        self.subtitles_var = tk.BooleanVar(value=self.config.subtitles)
        ttk.Checkbutton(frame, text="Subtítulos de lo que te dicen por voz", variable=self.subtitles_var,
                        command=self._toggle_subtitles).grid(row=0, column=0, sticky="w", padx=6, pady=(4, 2))
        speak_row = ttk.Frame(frame)
        speak_row.grid(row=1, column=0, sticky="w", padx=6, pady=2)
        self.speak_var = tk.BooleanVar(value=self.config.speak)
        ttk.Checkbutton(speak_row, text="Traducir mi voz", variable=self.speak_var,
                        command=self._toggle_speak).pack(side="left")
        self.mode = ttk.Combobox(speak_row, values=list(MODES.values()), state="readonly", width=29)
        self.mode.set(MODES.get(self.config.speak_mode, MODES["boton"]))
        self.mode.bind("<<ComboboxSelected>>", self._change_mode)
        self.mode.pack(side="left", padx=(8, 0))
        self.hear_var = tk.BooleanVar(value=self.config.hear_myself)
        ttk.Checkbutton(speak_row, text="Escucharla yo también", variable=self.hear_var,
                        command=self._toggle_hear).pack(side="left", padx=(14, 0))
        key_row = ttk.Frame(frame)
        key_row.grid(row=2, column=0, sticky="w", padx=6, pady=2)
        ttk.Label(key_row, text="Botón para hablar:").pack(side="left")
        self.ptt_label = ttk.Label(key_row, text=win32.describe_binding(self.config.push_to_talk),
                                   font=strong_font())
        self.ptt_label.pack(side="left", padx=(6, 8))
        ttk.Button(key_row, text="Cambiar…", command=self._change_key).pack(side="left")
        ttk.Label(key_row, text="También podés escribir y que se diga en voz: en la barra, Ctrl+Enter.",
                  foreground=MUTED).pack(side="left", padx=10)
        self.status = ttk.Label(frame, text="", foreground=MUTED, wraplength=760, justify="left")
        self.status.grid(row=3, column=0, sticky="w", padx=6, pady=(2, 6))
        self._tick()

    # --- arranque (cuando la app ya tiene Claude listo)
    def start(self) -> None:
        if not (self.config.subtitles or self.config.speak):
            return
        if load_state().get("voice_loading"):
            # La última vez Bubble se cerró mientras cargaba la voz: esta vez no se carga sola, así la ventana abre.
            update_state(voice_loading=False)
            self._set_status("La última vez Bubble se cerró mientras preparaba la voz, así que quedó en pausa. "
                             "Para intentar de nuevo, desmarcá y volvé a marcar la casilla.")
            return
        self._prepare(self._apply)

    def stop(self) -> None:
        for part in (self.listener, self.speaker):
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
            from ..voice.asr import FastWhisper, pick_models
            from ..voice.speakers import SpeakerTracker
            from ..voice.tts import Voices

            try:
                quick, final = pick_models()
                if self.config.model not in ("", "auto"):
                    final = self.config.model
                self._set_status(f"Preparando el reconocimiento de voz ({final}). La primera vez se descarga "
                                 "(hasta ~500 MB) y queda en tu PC…")
                update_state(voice_loading=True)  # si el proceso se cae acá, el próximo arranque no la carga sola
                models = (FastWhisper(final, 4), FastWhisper(quick, 2) if quick else None, SpeakerTracker())
                update_state(voice_loading=False)
                self.voices = Voices()
                self.models = models
                self._set_status("")
                self.app.events.put(("voice_ready", then))
            except ImportError:
                self._set_status('Falta instalar la parte de voz: .venv\\Scripts\\python.exe -m pip install -e ".[voz]"')
            except Exception as exc:  # noqa: BLE001
                update_state(voice_loading=False)
                self._set_status(f"No se pudo preparar la voz: {exc}")
            finally:
                self._preparing = False

        threading.Thread(target=work, name="bubble-voz-prepara", daemon=True).start()

    def _apply(self) -> None:
        """Prende o apaga cada parte según las casillas."""
        from ..voice.captions import CaptionBoard
        from ..voice.live import LiveListener
        from ..voice.pipelines import VoiceSpeaker

        from ..voice.pipelines import DirectVoice, VoiceOut

        final, quick, speakers = self.models
        if self.out is None:
            self.out = VoiceOut(self.voices, self.config.hear_myself)
            self.out.listeners.append(self._playing)
        if self.board is None:
            self.board = CaptionBoard(self.app.config.user.language, self._translate_heard,
                                      on_translated=lambda line: self.app.events.put(("voice_line", line)))
        if self.subtitles_var.get():
            if self.listener is None:
                self.listener = LiveListener(final, self.board.caption, partial_asr=quick, speakers=speakers,
                                             on_error=lambda msg: self._set_status(msg))
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
            output = self.out.output
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

    def _toggle_hear(self) -> None:
        self.config.hear_myself = self.hear_var.get()
        save_setting("voice", "hear_myself", self.config.hear_myself)
        if self.out:
            self.out.hear_myself = self.config.hear_myself

    def _change_mode(self, _event=None) -> None:
        chosen = next(key for key, label in MODES.items() if label == self.mode.get())
        self.config.speak_mode = chosen
        save_setting("voice", "speak_mode", chosen)
        if self.speak_var.get():
            self._toggled(True)

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

    # --- lo que te dicen: frase → traducción (llega de a pedazos) → subtítulo
    def _translate_heard(self, text: str, language: str, speaker: int, on_piece, on_done) -> None:
        async def translate() -> None:
            try:
                result = await self.app.translator.translate_incoming(text, speaker_name(speaker), on_delta=on_piece)
                on_done(result.translation if result.status != "error" and result.translation.strip() else None)
            except Exception:  # noqa: BLE001 - sin traducción queda el original
                on_done(None)

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
        # Si tu voz traducida suena en tus parlantes (sin micrófono virtual, o porque la querés escuchar), no se tiene
        # que subtitular como si fuera de otro.
        out = self.out
        if out and self.listener and (not out.output.is_cable or out.hear_myself):
            self.listener.muted_until = time.monotonic() + seconds + 0.5

    # --- escribir y que se diga en voz (la barra, Ctrl+Enter)
    def say(self, pairs: list[tuple[str, str]], original: str) -> None:
        """Dice las traducciones (idioma, texto) con la voz sintética, en orden. No bloquea."""

        def work() -> None:
            try:
                from ..voice.pipelines import VoiceOut
                from ..voice.tts import Voices

                if self.out is None:
                    self.voices = self.voices or Voices()
                    self.out = VoiceOut(self.voices, self.config.hear_myself)
                    self.out.listeners.append(self._playing)
                if not self.out.output.is_cable:
                    self._set_status(CABLE_HELP)
                for language, text in pairs:
                    self.app.events.put(("voice_subtitle", (original, text, language)))
                    if not self.out.say(text, language):
                        self._set_status(f"No hay voz sintética para el idioma «{language}».")
            except ImportError:
                self._set_status('Falta instalar la parte de voz: .venv\\Scripts\\python.exe -m pip install -e ".[voz]"')
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"No se pudo decir en voz: {exc}")

        threading.Thread(target=work, name="bubble-escrito-a-voz", daemon=True).start()

    # --- subtítulos en pantalla
    def show(self, original: str, translation: str, language: str) -> None:
        """Tu voz traducida: se muestra como una frase tuya."""
        if self.board is not None:
            self.board.mine(original, translation, language)

    def _tick(self) -> None:
        visible = win32.roblox_is_foreground()
        area = self.app._game_area() if visible else None
        lines = self.board.visible() if self.board is not None else []
        self.subtitles.update(lines, area, visible)
        self.app.root.after(80, self._tick)
