"""La ventana de «Entrenar tu voz» (página Pruebas, opcional): una frase por vez, grande.

Tocás «Grabar» (o la barra espaciadora), la leés y Bubble te dice qué entendió y qué aprendió. Si salió bien pasa
sola a la siguiente. En la segunda parte contestás con tus palabras y podés corregir lo que entendió antes de
guardarlo. Se corta cuando quieras («Terminar por ahora») y la próxima vez sigue desde ahí. La lógica está en
voice/training.py.
"""

from __future__ import annotations

import threading
import tkinter as tk
from typing import TYPE_CHECKING, Callable

from ..voice.training import HOW, Result, calibrate, check, feedback, script_for
from .tutorial import _colors

if TYPE_CHECKING:
    from .main_window import BubbleWindow

WIDTH, HEIGHT = 660, 480
AUTO_NEXT_MS = 1600  # si salió bien, pasa sola a la siguiente


class TrainingWindow:
    def __init__(self, app: BubbleWindow, on_close: Callable[[], None]) -> None:
        self.app = app
        self.voice = app.voice_panel
        self.profile = self.voice.profile
        self.language = app.config.user.language
        self.items = script_for(self.language)
        self.on_close = on_close
        self.results: dict[int, Result] = {}
        step = self.profile.training_step(self.language)
        self.index = 0 if step >= len(self.items) else step  # ya lo terminaste: empieza de nuevo
        self.recording = False
        self._auto: str | None = None
        self.c = c = _colors()
        self.win = tk.Toplevel(app.root, bg=c["bg"])
        self.win.title("Entrenar tu voz")
        self.win.resizable(False, False)
        self.win.transient(app.root)
        self.win.protocol("WM_DELETE_WINDOW", self.finish)
        self.win.bind("<space>", lambda _e: self.record() if not self._typing() else None)
        self.win.bind("<Right>", lambda _e: self.next() if not self._typing() else None)
        self.win.bind("<Escape>", lambda _e: self.finish())
        self._build()
        self._place()
        self.show()

    # ------------------------------------------------------------ armado
    def _build(self) -> None:
        c = self.c
        header = tk.Frame(self.win, bg=c["bg"])
        header.pack(fill="x", padx=26, pady=(20, 2))
        self.counter = tk.Label(header, font=("Segoe UI", 9), fg=c["muted"], bg=c["bg"])
        self.counter.pack(side="right", anchor="n")
        tk.Label(header, text="Entrenar tu voz · opcional", font=("Segoe UI", 10), fg=c["muted"],
                 bg=c["bg"]).pack(anchor="w")
        self.part = tk.Label(header, font=("Segoe UI", 15, "bold"), fg=c["text"], bg=c["bg"], anchor="w")
        self.part.pack(anchor="w")
        self.bar = tk.Canvas(self.win, height=6, bg=c["bg"], highlightthickness=0)
        self.bar.pack(fill="x", padx=26, pady=(8, 0))

        self.sentence = tk.Label(self.win, font=("Segoe UI", 19, "bold"), fg=c["text"], bg=c["bg"], justify="left",
                                 anchor="w", wraplength=WIDTH - 60)
        self.sentence.pack(fill="x", padx=26, pady=(22, 4))
        self.how = tk.Label(self.win, font=("Segoe UI", 10), fg=c["muted"], bg=c["bg"], anchor="w", justify="left",
                            wraplength=WIDTH - 60)
        self.how.pack(fill="x", padx=26)
        self.answer = tk.Entry(self.win, font=("Segoe UI", 12), bg=c["button"], fg=c["text"], relief="flat",
                               insertbackground=c["text"])
        self.status = tk.Label(self.win, font=("Segoe UI", 11), fg=c["text"], bg=c["bg"], anchor="nw", justify="left",
                               wraplength=WIDTH - 60)
        self.status.pack(fill="both", expand=True, padx=26, pady=(16, 4))

        bottom = tk.Frame(self.win, bg=c["bg"])
        bottom.pack(fill="x", padx=26, pady=(0, 20))
        link = tk.Label(bottom, text="Terminar por ahora", font=("Segoe UI", 9, "underline"), fg=c["muted"],
                        bg=c["bg"], cursor="hand2")
        link.bind("<Button-1>", lambda _e: self.finish())
        link.pack(side="left")
        self.next_button = self._button(bottom, "Siguiente ›", self.next, primary=False)
        self.next_button.pack(side="right")
        self.skip_button = self._button(bottom, "Saltar", self.next, primary=False)
        self.skip_button.pack(side="right", padx=(0, 8))
        self.save_button = self._button(bottom, "Guardar", self.save_answer, primary=True)
        self.record_button = self._button(bottom, "🎙  Grabar", self.record, primary=True)
        self.record_button.pack(side="right", padx=(0, 8))

    def _button(self, parent, text: str, command, primary: bool) -> tk.Button:
        c = self.c
        colors = ((c["accent"], c["on_accent"], c["accent_hover"]) if primary
                  else (c["button"], c["text"], c["button_hover"]))
        button = tk.Button(parent, text=text, command=command, font=("Segoe UI", 10, "bold" if primary else "normal"),
                           bg=colors[0], fg=colors[1], activebackground=colors[2], activeforeground=colors[1],
                           relief="flat", bd=0, padx=14, pady=6, cursor="hand2", disabledforeground=c["muted"])
        button.bind("<Enter>", lambda _e: button.configure(bg=colors[2]))
        button.bind("<Leave>", lambda _e: button.configure(bg=colors[0]))
        return button

    def _place(self) -> None:
        root = self.app.root
        self.win.update_idletasks()
        x = root.winfo_rootx() + (root.winfo_width() - WIDTH) // 2
        y = root.winfo_rooty() + (root.winfo_height() - HEIGHT) // 3
        self.win.geometry(f"{WIDTH}x{HEIGHT}+{max(0, x)}+{max(0, y)}")
        self.win.lift()
        self.win.focus_force()

    def _typing(self) -> bool:
        return self.win.focus_get() is self.answer

    def _draw_bar(self) -> None:
        self.bar.delete("all")
        width = WIDTH - 52
        self.bar.create_rectangle(0, 0, width, 6, fill=self.c["dot_off"], outline="")
        done = width * self.index / max(1, len(self.items))
        if done:
            self.bar.create_rectangle(0, 0, done, 6, fill=self.c["accent"], outline="")

    # ------------------------------------------------------------ una frase
    def show(self) -> None:
        if self._auto:
            self.win.after_cancel(self._auto)
            self._auto = None
        if self.index >= len(self.items):
            self.finish(completed=True)
            return
        item = self.items[self.index]
        free = item.kind == "free"
        reading = sum(1 for i in self.items if i.kind != "free")
        self.part.configure(text="Con tus palabras" if free else "Leé en voz alta")
        position = self.index + 1 - (reading if free else 0)
        total = len(self.items) - reading if free else reading
        self.counter.configure(text=f"{'Pregunta' if free else 'Frase'} {position} de {total}")
        self.sentence.configure(text=item.text if free else f"«{item.text}»")
        self.how.configure(text=HOW[item.kind])
        self.status.configure(text="Tocá «Grabar» (o la barra espaciadora) y hablá. Cuando hacés una pausa, "
                                   "termina solo.", fg=self.c["muted"])
        self.answer.delete(0, "end")
        self.answer.pack_forget()
        self.save_button.pack_forget()
        self.record_button.configure(state="normal", text="🎙  Grabar")
        self.next_button.configure(text="Siguiente ›")
        self._draw_bar()

    def record(self) -> None:
        if self.recording or self.index >= len(self.items):
            return
        self.recording = True
        self.record_button.configure(state="disabled", text="Te escucho…")
        self.status.configure(text="Te escucho… hablá y hacé una pausa al terminar.", fg=self.c["accent"])
        item = self.items[self.index]
        index = self.index

        def work() -> None:
            from ..voice.checks import record_phrase
            from ..voice.speech import melody

            try:
                free = item.kind == "free"
                audio = record_phrase(self.voice._my_microphone(), max_s=25 if free else 14,
                                      quiet_s=1.3 if free else 0.9, wait_s=8)
                heard = None
                if len(audio):
                    heard = self.voice.my_asr().transcribe(audio, language=self.language, hint=self.profile.hint,
                                                           retry_beam=3)
                    if not free:  # tu voz real (solo en tu PC): para medir cómo te entiende y mejorarlo
                        from ..voice.training import save_clip

                        save_clip(audio, item.text, self.language, index)
                result = check(item, heard.text if heard else "", melody(audio) if len(audio) else None)
                self.app.events.put(("call", lambda: self._heard(index, result)))
            except Exception as exc:  # noqa: BLE001 - se muestra
                message = f"No se pudo grabar: {exc}"
                self.app.events.put(("call", lambda: self._failed(message)))

        self.voice._prepare(lambda: threading.Thread(target=work, name="bubble-entrenar", daemon=True).start())

    def _failed(self, message: str) -> None:
        self.recording = False
        if self.win.winfo_exists():
            self.record_button.configure(state="normal", text="🎙  Grabar")
            self.status.configure(text=message, fg=self.c["muted"])

    def _heard(self, index: int, result: Result) -> None:
        self.recording = False
        if not self.win.winfo_exists() or index != self.index:
            return
        self.results[index] = result
        self.record_button.configure(state="normal", text="🎙  Repetir")
        self.status.configure(text=feedback(result), fg=self.c["text"])
        if result.item.kind == "free":
            self.answer.delete(0, "end")
            self.answer.insert(0, result.heard)
            self.answer.pack(fill="x", padx=26, pady=(12, 0), before=self.status)
            self.save_button.pack(side="right", padx=(0, 8), before=self.skip_button)
            return
        # Se aprende ya (aunque cortes a mitad del entrenamiento): las palabras que no te entendió y tu voz de siempre.
        self.profile.learn_words(result.missed, self.language)
        if result.item.kind == "normal" and result.error <= 0.5:
            self.profile.learn_melody(result.melody)
        if result.heard and result.error <= 0.2:
            self._auto = self.win.after(AUTO_NEXT_MS, self.next)

    def save_answer(self) -> None:
        """Con tus palabras: lo que dijiste (corregido) queda como tu forma de hablar, y lo raro (nombres, jerga) como
        palabras tuyas."""
        from ..translate.langdetect import known_anywhere, words_in

        text = self.answer.get().strip()
        if text:
            self.profile.learn_phrase(text, self.language)
            self.profile.learn_words([word for word in words_in(text) if len(word) > 2 and not known_anywhere(word)],
                                     self.language)
            self.status.configure(text="✓ Guardado: así hablás vos.", fg=self.c["text"])
        self.next()

    def next(self) -> None:
        if self.recording or not self.win.winfo_exists():
            return
        self.index += 1
        self.profile.set_training_step(self.language, self.index)
        self.show()

    # ------------------------------------------------------------ fin
    def finish(self, completed: bool = False) -> None:
        """Termina (o corta por ahora): se calculan tus umbrales con lo que hiciste y se cierra."""
        if self._auto:
            self.win.after_cancel(self._auto)
            self._auto = None
        found = calibrate(list(self.results.values()), self.profile.usual())
        if found:
            self.profile.calibrate(found)
        if not completed:
            self.profile.set_training_step(self.language, self.index)
        learned = []
        counts = self.profile.summary()
        if counts["palabras"]:
            learned.append(f"{counts['palabras']} palabras tuyas")
        if self.profile.usual():
            learned.append("tu voz de siempre")
        if "question_rise" in found:
            learned.append("cómo preguntás")
        if "shout_db" in found:
            learned.append("cómo gritás")
        message = ("Listo, terminaste el entrenamiento." if completed else
                   f"Guardé lo que hiciste: la próxima seguís desde la frase {self.index + 1}.")
        if learned:
            message += " Aprendí " + ", ".join(learned[:-1]) + (" y " if len(learned) > 1 else "") + learned[-1] + "."
        self.win.destroy()
        self.on_close()
        self.app._set_status(message)
