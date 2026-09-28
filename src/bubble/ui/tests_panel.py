"""La página «Pruebas»: probar todo sin jugar, ver cuánto tarda cada paso y enseñarle a Bubble cómo hablás.

- Tu micrófono: leés una frase y te dice si va a andar bien, normal o mal para traducir (y qué cambiar).
- Tu voz traducida: hablás y ves qué entendió, cómo lo dijiste (pregunta, grito…), cómo lo tradujo y cuánto tardó cada
  paso. Si algo salió mal, lo corregís y lo guardás: Bubble aprende tus palabras y cómo querés sonar.
- Chat a voz: escribís como en la barra del juego y escuchás cómo lo dice.
- Lo que te dicen: una voz sintética dice una frase en inglés, como si fuera otro jugador, y ves el subtítulo.
- Tu PC: cómo va a andar Bubble en esta computadora.
- Entrenar tu voz (opcional): leés unas frases y contestás unas preguntas con tus palabras; aprende tu vocabulario,
  tus expresiones, cómo preguntás y cómo gritás (ver ui/training_window.py).
- Lo que aprendió: cuánto sabe de tu voz, y un botón para borrarlo.

Todo suena solo en tus auriculares: nada le llega a Roblox.
"""

from __future__ import annotations

import logging
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

from . import widgets

if TYPE_CHECKING:
    from .main_window import BubbleWindow

log = logging.getLogger(__name__)
HOW = {"question": "pregunta", "shout": "gritando", "exclaim": "exclamando", "soft": "bajito"}
RATING_TEXT = {"bien": ("✓ Bien", "good"), "normal": ("● Normal", "warn"), "mal": ("✗ Mal", "bad"),
               "excelente": ("✓ Excelente", "good"), "lenta": ("✗ Lenta", "bad")}
THEM_SAMPLE = "Yo, does anyone wanna trade? I've got a legendary pet!"


def _seconds(value: float) -> str:
    return f"{value:.1f} s".replace(".", ",")


def describe_how(intonation: str) -> str:
    marks = [HOW[mark] for mark in intonation.split("+") if mark in HOW] if intonation else []
    return ", ".join(marks) if marks else "normal"


class TestsPanel:
    def __init__(self, app: BubbleWindow) -> None:
        self.app = app
        self.voice = app.voice_panel
        self._busy = False
        self._buttons: list[ttk.Button] = []
        self._last_speech = None  # la última voz traducida (para volver a escucharla)
        self._mine_target = ""
        self._chat_target = ""
        self._training = None  # la ventana del entrenamiento, si está abierta

    # ------------------------------------------------------------ armado
    def build_page(self, page) -> None:
        from ..voice.checks import sentence_for

        colors = widgets.palette()
        my_language = self.app.config.user.language

        box = widgets.card(page, "Tu micrófono", "Leé la frase en voz alta, como cuando jugás. Te digo si tu micrófono "
                                                 "va a andar bien para traducir tu voz.")
        ttk.Label(box, text=f"«{sentence_for(my_language)}»", font="SunValleyBodyStrongFont", wraplength=440,
                  justify="left").pack(anchor="w")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(10, 0))
        self._button(row, "Probar micrófono", self._test_mic, accent=True).pack(side="left")
        self.mic_rating = ttk.Label(row, text="", font="SunValleyBodyStrongFont")
        self.mic_rating.pack(side="left", padx=12)
        self.mic_details = widgets.muted(box, "")

        box = widgets.card(page, "Tu voz traducida", "Tocá «Hablar», decí algo como en el juego y hacé una pausa. Ves "
                                                     "qué entendió, cómo lo tradujo y cuánto tardó cada paso.")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self._button(row, "Hablar", self._test_my_voice, accent=True).pack(side="left")
        self.mine_state = ttk.Label(row, text="", foreground=colors["muted"])
        self.mine_state.pack(side="left", padx=12)
        self.mine_heard, self.mine_said = tk.StringVar(), tk.StringVar()
        self._field(box, "Entendí", self.mine_heard)
        self._field(box, "Traducción", self.mine_said)
        self.mine_info = widgets.muted(box, "")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        self._button(row, "▶ Escuchar", self._play_last).pack(side="left")
        self._button(row, "✓ Guardar (aprende esto)", self._approve_mine).pack(side="left", padx=8)
        widgets.muted(box, "Si entendió o tradujo algo mal, corregilo arriba y guardalo: aprende tus palabras y cómo "
                           "querés sonar. Las frases que ya dijiste salen al instante la próxima vez.")

        box = widgets.card(page, "Chat a voz", "Como Ctrl+Enter en la barra del juego: escribís y lo dice en voz.")
        self.chat_text = tk.StringVar(value="dale, esperame en la torre que ya voy")
        entry = ttk.Entry(box, textvariable=self.chat_text)
        entry.pack(fill="x")
        entry.bind("<Return>", lambda _e: self._test_chat_voice())
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 0))
        self._button(row, "Traducir y decir", self._test_chat_voice, accent=True).pack(side="left")
        self._button(row, "✓ Guardar", self._approve_chat).pack(side="left", padx=8)
        self.chat_said = tk.StringVar()
        self._field(box, "Dice", self.chat_said)
        self.chat_info = widgets.muted(box, "")

        box = widgets.card(page, "Lo que te dicen", "Una voz sintética dice esta frase como si fuera otro jugador: "
                                                    "ves qué entendió y el subtítulo en tu idioma.")
        self.them_text = tk.StringVar(value=THEM_SAMPLE)
        ttk.Entry(box, textvariable=self.them_text).pack(fill="x")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(8, 0))
        self._button(row, "Probar", self._test_them, accent=True).pack(side="left")
        self.them_info = widgets.muted(box, "")

        box = widgets.card(page, "Tu PC", "Cómo va a andar Bubble en esta computadora (se mide de verdad).")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self._button(row, "Medir", self._test_pc, accent=True).pack(side="left")
        self.pc_rating = ttk.Label(row, text="", font="SunValleyBodyStrongFont")
        self.pc_rating.pack(side="left", padx=12)
        self.pc_info = widgets.muted(box, "")

        box = widgets.card(page, "Entrenar tu voz (opcional)", "Leés unas frases de juego y contestás unas preguntas "
                                                               "con tus palabras (unos 5 minutos). Aprende tu "
                                                               "vocabulario y tus expresiones, cómo preguntás y cómo "
                                                               "gritás. Cortás cuando quieras y seguís otro día.")
        row = ttk.Frame(box)
        row.pack(fill="x")
        self.train_button = ttk.Button(row, text="Empezar", command=self._train, style="Accent.TButton")
        self.train_button.pack(side="left")
        self.train_info = ttk.Label(row, text="", foreground=colors["muted"])
        self.train_info.pack(side="left", padx=12)

        box = widgets.card(page, "Lo que aprendió", "Todo queda en tu PC.")
        self.learned = widgets.muted(box, "")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="Borrar lo aprendido", command=self._forget).pack(side="left")
        self.refresh_learned()

    def _button(self, parent, text: str, command: Callable[[], None], accent: bool = False) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command, style="Accent.TButton" if accent else "TButton")
        self._buttons.append(button)
        return button

    @staticmethod
    def _field(parent, label: str, variable: tk.StringVar) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(8, 0))
        ttk.Label(row, text=label, width=11).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)

    # ------------------------------------------------------------ utilidades
    def _ui(self, action: Callable[[], None]) -> None:
        self.app.events.put(("call", action))

    def _run(self, work: Callable[[], None], needs_claude: bool = False, needs_models: bool = True) -> None:
        """Hace `work` en otro hilo (con los modelos de voz cargados), de a una prueba por vez."""
        if self._busy:
            return
        if needs_claude and not (self.app.ready and self.app.translator is not None):
            self.app._set_status("Esperá a que Bubble se conecte con Claude (arriba a la derecha).")
            return
        if needs_claude:
            self.voice._open_voice_lane()  # el carril rápido de la voz (si ya estaba abierto, no hace nada)
        self._busy = True
        for button in self._buttons:
            button.state(["disabled"])

        def wrapped() -> None:
            try:
                work()
            except ImportError:
                from .voice_panel import NO_VOICE_PACK

                self._ui(lambda: self.app._set_status(NO_VOICE_PACK))
            except Exception as exc:  # noqa: BLE001 - se muestra
                log.exception("Falló una prueba")
                message = f"La prueba falló: {exc}"  # `exc` deja de existir al salir del except
                self._ui(lambda: self.app._set_status(message))
            finally:
                self._ui(self._done)

        def start() -> None:
            threading.Thread(target=wrapped, name="bubble-prueba", daemon=True).start()

        if needs_models:
            self.voice._prepare(start)
        else:
            start()

    def _done(self) -> None:
        self._busy = False
        for button in self._buttons:
            button.state(["!disabled"])
        self.refresh_learned()

    def _voices(self):
        from ..voice.tts import Voices

        if self.voice.voices is None:
            self.voice.voices = Voices()
        self.voice.voices.gender, self.voice.voices.speed = self.voice.config.gender, self.voice.config.speed
        return self.voice.voices

    def _play(self, speech) -> None:
        from ..voice import audio as audio_io

        if speech is not None:
            audio_io.play(audio_io.monitor_output(), speech.audio, speech.sample_rate)

    def _play_last(self) -> None:
        if self._last_speech is not None and not self._busy:
            threading.Thread(target=self._play, args=(self._last_speech,), daemon=True).start()

    def _translate(self, coro):
        return self.app.runner.submit(coro).result(timeout=30)

    # ------------------------------------------------------------ micrófono
    def _test_mic(self) -> None:
        from ..voice.checks import analyze_mic, record_phrase, sentence_for

        language = self.app.config.user.language

        def work() -> None:
            final = self.voice.my_asr()
            self._ui(lambda: (self.mic_rating.configure(text="Te escucho… leé la frase", foreground=""),
                              self.mic_details.configure(text="")))
            audio = record_phrase(self.voice._my_microphone(), max_s=12, quiet_s=1.0, wait_s=6)
            self._ui(lambda: self.mic_rating.configure(text="Analizando…"))
            from ..voice.vad import StreamingVad

            probs = StreamingVad().feed(audio) if len(audio) else None
            heard = final.transcribe(audio, language=language, hint=self.voice.profile.hint) if len(audio) else None
            report = analyze_mic(audio, heard.text if heard else "", sentence_for(language), probs)
            text, color = RATING_TEXT[report.rating]
            details = (f"Tu voz: {report.voice_db:.0f} dB · ruido: {report.noise_db:.0f} dB · se entendió el "
                       f"{report.accuracy:.0%} de las palabras\nEntendí: «{report.heard or '(nada)'}»")
            if report.tips:
                details += "\n\n" + "\n".join(f"• {tip}" for tip in report.tips)
            self._ui(lambda: (self.mic_rating.configure(text=text, foreground=widgets.palette()[color]),
                              self.mic_details.configure(text=details)))

        self._run(work)

    # ------------------------------------------------------------ tu voz traducida
    def _test_my_voice(self) -> None:
        from ..voice.checks import record_phrase
        from ..voice.speech import melody

        language = self.app.config.user.language
        profile = self.voice.profile

        def work() -> None:
            final = self.voice.my_asr()
            translator = self.app.translator
            target = translator.outgoing_target()
            self._mine_target = target
            self._ui(lambda: (self.mine_state.configure(text=f"Te escucho… (te van a escuchar en {target.upper()})"),
                              self.mine_heard.set(""), self.mine_said.set(""), self.mine_info.configure(text="")))
            audio = record_phrase(self.voice._my_microphone(), max_s=15, quiet_s=0.8, wait_s=6)
            self._ui(lambda: self.mine_state.configure(text="Traduciendo…"))
            started = time.perf_counter()
            heard = final.transcribe(audio, language=language, hint=profile.hint, retry_beam=3) if len(audio) else None
            understand = time.perf_counter() - started
            if heard is None:
                self._ui(lambda: self.mine_state.configure(text="No te escuché. Probá de nuevo, un poco más fuerte."))
                return
            tune = melody(audio)
            how = profile.intonation(tune)
            profile.learn_melody(tune)
            started = time.perf_counter()
            saved = profile.saved(heard.text, target)
            if saved:
                translation, source = saved, "guardada: al instante"
            else:
                result = self._translate(translator.translate_outgoing(
                    heard.text, target, tone=self.app.config.user.tone, spoken=True, from_speech=True,
                    intonation=how))
                translation, source = result.translation, "Claude"
                if result.status == "error" or not translation.strip():
                    self._ui(lambda: self.mine_state.configure(text=f"No se pudo traducir: {result.error}"))
                    return
            translate = time.perf_counter() - started
            started = time.perf_counter()
            speech = self._voices().synthesize(translation, target, style=how)
            voice = time.perf_counter() - started
            self._last_speech = speech
            profile.note_times({"entender": understand, "traducir": translate, "voz": voice})
            info = (f"Cómo lo dijiste: {describe_how(how)} · traducción: {source}\n"
                    f"Entender {_seconds(understand)} · traducir {_seconds(translate)} · voz {_seconds(voice)} → "
                    f"{_seconds(understand + translate + voice)} desde que terminás de hablar (más la pausa)")
            self._ui(lambda: (self.mine_heard.set(heard.text), self.mine_said.set(translation),
                              self.mine_info.configure(text=info), self.mine_state.configure(text="")))
            self._play(speech)

        self._run(work, needs_claude=True)

    def _approve_mine(self) -> None:
        said, wanted = self.mine_heard.get().strip(), self.mine_said.get().strip()
        if said and wanted and self._mine_target:
            self.voice.profile.approve(said, wanted, self._mine_target, self.app.config.user.language)
            self.mine_state.configure(text="Guardado: la próxima vez sale así (y al instante).")
            self.refresh_learned()

    # ------------------------------------------------------------ chat a voz
    def _test_chat_voice(self) -> None:
        text = self.chat_text.get().strip()
        if not text:
            return

        def work() -> None:
            translator = self.app.translator
            target = translator.outgoing_target()
            self._chat_target = target
            self._ui(lambda: (self.chat_said.set(""), self.chat_info.configure(text="Traduciendo…")))
            started = time.perf_counter()
            result = self._translate(translator.translate_outgoing(text, target, tone=self.app.config.user.tone,
                                                                   spoken=True))
            translate = time.perf_counter() - started
            if result.status == "error" or not result.translation.strip():
                self._ui(lambda: self.chat_info.configure(text=f"No se pudo traducir: {result.error}"))
                return
            started = time.perf_counter()
            speech = self._voices().synthesize(result.translation, target)
            voice = time.perf_counter() - started
            self._last_speech = speech
            info = f"En {target.upper()} · traducir {_seconds(translate)} · voz {_seconds(voice)}"
            self._ui(lambda: (self.chat_said.set(result.translation), self.chat_info.configure(text=info)))
            self._play(speech)

        self._run(work, needs_claude=True, needs_models=False)

    def _approve_chat(self) -> None:
        said, wanted = self.chat_text.get().strip(), self.chat_said.get().strip()
        if said and wanted and self._chat_target:
            self.voice.profile.approve(said, wanted, self._chat_target, self.app.config.user.language)
            self.chat_info.configure(text="Guardado: así vas a sonar.")
            self.refresh_learned()

    # ------------------------------------------------------------ lo que te dicen
    def _test_them(self) -> None:
        import numpy as np

        text = self.them_text.get().strip()
        if not text:
            return

        def work() -> None:
            final = self.voice.models[0]
            voices = self._voices()
            self._ui(lambda: self.them_info.configure(text="Hablando…"))
            speech = voices.synthesize(text, "en", gender="masculina")
            if speech is None:
                self._ui(lambda: self.them_info.configure(text="No hay voz sintética en inglés."))
                return
            threading.Thread(target=self._play, args=(speech,), daemon=True).start()
            count = int(len(speech.audio) * 16000 / speech.sample_rate)
            audio = np.interp(np.linspace(0, len(speech.audio) - 1, count), np.arange(len(speech.audio)),
                              speech.audio).astype(np.float32)
            started = time.perf_counter()
            heard = final.transcribe(audio, prior={"en": 1.5}, retry_beam=5)
            understand = time.perf_counter() - started
            if heard is None:
                self._ui(lambda: self.them_info.configure(text="No se entendió la frase."))
                return
            if self.voice._is_my_language(heard.text, heard.language):
                self._ui(lambda: self.them_info.configure(
                    text=f"Entendí ({heard.language.upper()}): «{heard.text}»\nYa está en tu idioma: no se traduce."))
                return
            started = time.perf_counter()
            result = self._translate(self.app.translator.translate_incoming(heard.text, "Voz 1", from_speech=True))
            translate = time.perf_counter() - started
            info = (f"Entendí ({heard.language.upper()}): «{heard.text}»\nSubtítulo: «{result.translation}»\n"
                    f"Entender {_seconds(understand)} · traducir {_seconds(translate)}")
            self._ui(lambda: self.them_info.configure(text=info))

        self._run(work, needs_claude=True)

    # ------------------------------------------------------------ tu PC
    def _test_pc(self) -> None:
        import numpy as np

        from ..voice.checks import PcReport, cpu_name, cpu_threads, gpu_names, memory_gb, rate_pc

        language = self.app.config.user.language.split("-")[0]

        def work() -> None:
            final = self.voice.my_asr()
            voices = self._voices()
            self._ui(lambda: (self.pc_rating.configure(text="Midiendo…", foreground=""),
                              self.pc_info.configure(text="")))
            sample = voices.synthesize("che, ¿alguien viene conmigo a la torre? esperame que ya voy", "es") or \
                voices.synthesize("hey, is anyone coming with me to the tower? wait for me", "en")
            count = int(len(sample.audio) * 16000 / sample.sample_rate)
            audio = np.interp(np.linspace(0, len(sample.audio) - 1, count), np.arange(len(sample.audio)),
                              sample.audio).astype(np.float32)
            final.transcribe(audio, language=language)  # la primera vez es más lenta: no cuenta
            started = time.perf_counter()
            final.transcribe(audio, language=language, hint=self.voice.profile.hint)
            understand = time.perf_counter() - started
            voices.synthesize("Wait for me, I'm coming to the tower!", "en")
            started = time.perf_counter()
            voices.synthesize("Wait for me, I'm coming to the tower!", "en")
            voice = time.perf_counter() - started
            translate = self.voice.profile.data["times"].get("traducir", 0.0)
            if not translate and self.app.ready and self.app.translator is not None:
                started = time.perf_counter()
                self._translate(self.app.translator.translate_outgoing("dale, ya voy", "en", spoken=True,
                                                                       from_speech=True))
                translate = time.perf_counter() - started
            report = rate_pc(PcReport(cpu_name(), cpu_threads(), memory_gb(), gpu_names(), final.name, understand,
                                      voice, translate or 1.3))
            text, color = RATING_TEXT.get(report.rating, (report.rating, "muted"))
            info = (f"{report.cpu} · {report.threads} hilos · {report.memory_gb:.0f} GB de memoria · "
                    f"{report.gpus[0] if report.gpus else 'sin placa de video'}\n"
                    f"Entender tu voz (modelo «{report.whisper}»): {_seconds(report.understand_s)} · traducir: "
                    f"{_seconds(report.translate_s)} · armar la voz: {_seconds(report.voice_s)}\n"
                    f"Tu voz traducida suena ~{_seconds(report.expected_s)} después de que terminás de hablar.\n\n"
                    + "\n".join(f"• {tip}" for tip in report.tips))
            self._ui(lambda: (self.pc_rating.configure(text=text, foreground=widgets.palette()[color]),
                              self.pc_info.configure(text=info)))

        self._run(work)

    # ------------------------------------------------------------ lo que aprendió
    def refresh_learned(self) -> None:
        profile = self.voice.profile
        counts = profile.summary()
        times = profile.data.get("times", {})
        text = (f"{counts['frases']} frases tuyas · {counts['palabras']} palabras tuyas · {counts['ejemplos']} "
                f"traducciones aprobadas · {counts['guardadas']} frases que salen al instante · conoce tu voz de "
                f"{counts['voz']} frases")
        calibration = profile.data.get("calibration", {})
        if calibration:
            known = [name for key, name in (("question_rise", "cómo preguntás"), ("shout_db", "cómo gritás"),
                                            ("exclaim_db", "cómo exclamás")) if key in calibration]
            if known:
                text += "\nDel entrenamiento: " + ", ".join(known)
        if times:
            text += "\nTiempos de tu voz (promedio): " + " · ".join(
                f"{stage} {_seconds(seconds)}" for stage, seconds in times.items())
        self.learned.configure(text=text)
        self._refresh_training()

    def _refresh_training(self) -> None:
        from ..voice.training import script_for

        language = self.app.config.user.language
        total = len(script_for(language))
        if not total:
            self.train_button.state(["disabled"])
            self.train_info.configure(text="Por ahora el entrenamiento está en español y en inglés.")
            return
        step = self.voice.profile.training_step(language)
        self.train_button.state(["!disabled"])
        if step >= total:
            self.train_button.configure(text="Entrenar de nuevo")
            self.train_info.configure(text="✓ Ya lo hiciste completo.")
        elif step:
            self.train_button.configure(text="Seguir")
            self.train_info.configure(text=f"Vas por la frase {step + 1} de {total}.")
        else:
            self.train_button.configure(text="Empezar")
            self.train_info.configure(text=f"{total} frases y preguntas.")

    def _train(self) -> None:
        from .training_window import TrainingWindow

        if self._training is not None:
            self._training.win.lift()
            return

        def closed() -> None:
            self._training = None
            self.refresh_learned()

        self._training = TrainingWindow(self.app, closed)

    def _forget(self) -> None:
        self.voice.profile.forget()
        self.refresh_learned()
