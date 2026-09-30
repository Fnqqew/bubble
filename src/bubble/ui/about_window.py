"""«Acerca de»: versión, autoría, tecnologías utilizadas, destino de los datos del jugador y canales de ayuda."""

from __future__ import annotations

import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import ttk
from typing import Callable

from .. import __version__, pro
from . import motion, widgets

LOGO = Path(__file__).resolve().parent.parent / "assets" / "bubble.png"
REPO_URL = "https://github.com/Fnqqew/bubble"
AUTHOR = "Juan Martín"
CREDITS = (
    ("Claude (Anthropic)", "traduce, con tu propia suscripción (Claude Agent SDK y Claude Code)"),
    ("faster-whisper", "entiende las voces en tu PC (modelos Whisper de OpenAI)"),
    ("Silero VAD", "sabe cuándo alguien empieza y termina de hablar"),
    ("Piper", "las voces sintéticas en tu PC"),
    ("Deepgram", "la voz de Bubble Pro: Nova-3 entiende y Aura-2 habla"),
    ("VB-Audio Virtual Cable", "el micrófono virtual para que te escuchen traducido"),
    ("sherpa-onnx", "distingue quién habla (Voz 1, Voz 2…)"),
    ("Sun Valley (sv-ttk)", "el diseño de la ventana, al estilo de Windows 11"),
    ("Y además", "OCR de Windows, lingua, NumPy, SciPy, Pillow, mss, dxcam, soundcard y FormSubmit"),
)
PRIVACY = (("Bubble funciona en tu PC. Lo que se debe traducir se envía a Claude con tu cuenta, y con Bubble Pro la "
            "voz se envía a Deepgram con tu clave. Una vez por día mido tu conexión con un archivo de prueba de "
            "Cloudflare. No tengo servidores propios ni recopilo datos: al creador solo le llega lo que vos envíes "
            "desde Soporte."))


class AboutWindow:
    def __init__(self, root: tk.Misc, open_support: Callable[[], None]) -> None:
        self.open_support = open_support
        self.window, body = widgets.dialog(root, "Acerca de Bubble")
        colors = widgets.palette()
        head = ttk.Frame(body)
        head.pack(fill="x")
        if LOGO.exists():
            from PIL import Image, ImageTk

            self._logo = ImageTk.PhotoImage(Image.open(LOGO).convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS))
            ttk.Label(head, image=self._logo).pack(side="left", padx=(0, 16))
        texts = ttk.Frame(head)
        texts.pack(side="left", fill="x", expand=True)
        ttk.Label(texts, text="Bubble", font="SunValleyTitleFont").pack(anchor="w")
        plan = "✦ Pro" if pro.active() else "Basic"
        ttk.Label(texts, text=f"Versión {__version__} · {plan}", font="SunValleyCaptionFont",
                  foreground=colors["accent"]).pack(anchor="w")
        widgets.muted(texts, "Traductor para Roblox, en vivo: el chat, las burbujas y las voces, de ida y de vuelta.", wrap=340, pady=(4, 0))

        box = widgets.card(body, "Creado por", pady=(16, 0))
        ttk.Label(box, text=f"{AUTHOR} · @Fnqqew", font="SunValleyBodyStrongFont").pack(anchor="w")
        widgets.muted(box, "Con la ayuda de Claude en cada línea.", pady=(2, 0))

        box = widgets.card(body, "Hecho con")
        box.columnconfigure(1, weight=1)
        for index, (name, what) in enumerate(CREDITS):
            ttk.Label(box, text=name, font="SunValleyCaptionFont").grid(row=index, column=0, sticky="nw",
                                                                        padx=(0, 14), pady=1)
            ttk.Label(box, text=what, font="SunValleyCaptionFont", foreground=colors["muted"], wraplength=300,
                      justify="left").grid(row=index, column=1, sticky="w", pady=1)

        box = widgets.card(body, "Tus datos")
        widgets.muted(box, PRIVACY, wrap=460, pady=(0, 0))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Soporte…", command=self._support).pack(side="left")
        ttk.Button(buttons, text="Ver en GitHub", command=lambda: webbrowser.open(REPO_URL)).pack(side="left",
                                                                                             padx=(8, 0))
        ttk.Button(buttons, text="Cerrar", style="Accent.TButton", command=self.close).pack(side="right")
        self.window.bind("<Escape>", lambda _event: self.close())
        widgets.present(self.window, root)

    def _support(self) -> None:
        self.close()
        self.open_support()

    def close(self) -> None:
        motion.vanish(self.window, self.window.destroy)
