"""Ventana de Soporte: título, descripción del problema y pasos para reproducirlo, imágenes (de archivo, pegadas o una
captura de Roblox) y envío. El envío lo realiza bubble/support.py.
"""

from __future__ import annotations

import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

from .. import support
from . import motion, theme, widgets

TITLE_HINT = "Ej.: la voz se corta cuando hablo"
BODY_HINT = "Describí qué pasó y cómo llegaste a eso:\n1. Abrí…\n2. Presioné…\n3. Pasó…"


class SupportWindow:
    def __init__(self, root: tk.Misc, grab_roblox=None) -> None:
        """`grab_roblox()` devuelve una captura de la ventana de Roblox (o None) para adjuntarla mediante un botón."""
        self.root = root
        self.grab_roblox = grab_roblox
        self.images: list[Path] = []
        self._thumbs: list = []
        self.sent = False
        self.window, body = widgets.dialog(root, "Soporte")
        ttk.Label(body, text="Soporte", font="SunValleySubtitleFont").pack(anchor="w")
        widgets.muted(body, "¿Algo no funciona o tenés una idea? Escribilo acá y le llega directo al creador de "
                            "Bubble.", pady=(2, 12))

        self.kind = tk.StringVar(value="problema")
        row = widgets.label_row(body, "¿Qué nos contás?", pady=(0, 6))
        widgets.segmented(row, self.kind, support.KINDS, lambda: None).pack(side="right")

        ttk.Label(body, text="Título", font="SunValleyBodyStrongFont").pack(anchor="w", pady=(6, 2))
        self.title = ttk.Entry(body, width=56)
        self.title.pack(fill="x")
        self._placeholder(self.title, TITLE_HINT)

        ttk.Label(body, text="¿Qué pasó y cómo?", font="SunValleyBodyStrongFont").pack(anchor="w", pady=(12, 2))
        frame, self.text = theme.scrolled_text(body, height=7, width=56, wrap="word", font=("Segoe UI", 10))
        frame.pack(fill="x")
        self._text_placeholder()

        ttk.Label(body, text="Imágenes (opcional)", font="SunValleyBodyStrongFont").pack(anchor="w", pady=(12, 2))
        buttons = ttk.Frame(body)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Agregar imágenes…", command=self._add_files).pack(side="left")
        ttk.Button(buttons, text="Pegar captura", command=self._paste).pack(side="left", padx=(8, 0))
        if grab_roblox is not None:
            ttk.Button(buttons, text="Captura de Roblox", command=self._shot).pack(side="left", padx=(8, 0))
        self.gallery = ttk.Frame(body)
        self.gallery.pack(fill="x", pady=(8, 0))

        self.with_system = tk.BooleanVar(value=True)
        self.with_log = tk.BooleanVar(value=True)
        ttk.Checkbutton(body, text="Enviar datos de mi PC (Windows, procesador, memoria, nada personal)",
                        variable=self.with_system).pack(anchor="w", pady=(12, 0))
        ttk.Checkbutton(body, text="Adjuntar el registro de errores de Bubble", variable=self.with_log).pack(
            anchor="w", pady=(4, 0))
        row = widgets.label_row(body, "Tu mail (opcional, para responderte)", pady=(10, 2))
        self.contact = ttk.Entry(row, width=26)
        self.contact.pack(side="right")

        self.status = widgets.muted(body, "", pady=(12, 6))
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        self.send_button = ttk.Button(actions, text="Enviar", style="Accent.TButton", command=self._send)
        self.send_button.pack(side="right")
        ttk.Button(actions, text="Cancelar", command=self.close).pack(side="right", padx=(0, 8))
        widgets.muted(body, "Le llega a Juan Martín, el creador, por FormSubmit (un servicio de formularios).",
                      pady=(10, 0))
        self.window.bind("<Escape>", lambda _event: self.close())
        widgets.present(self.window, root)
        self.title.focus_set()

    # ------------------------------------------------------------ campos
    @staticmethod
    def _placeholder(entry: ttk.Entry, hint: str) -> None:
        muted = widgets.palette()["muted"]
        entry.insert(0, hint)
        entry.configure(foreground=muted)
        entry.hint = hint

        def focus_in(_event=None) -> None:
            if entry.get() == hint and str(entry.cget("foreground")) == muted:
                entry.delete(0, "end")
                entry.configure(foreground="")

        def focus_out(_event=None) -> None:
            if not entry.get().strip():
                entry.delete(0, "end")
                entry.insert(0, hint)
                entry.configure(foreground=muted)

        entry.bind("<FocusIn>", focus_in, add="+")
        entry.bind("<FocusOut>", focus_out, add="+")

    def _text_placeholder(self) -> None:
        colors = widgets.palette()
        self.text.insert("1.0", BODY_HINT)
        self.text.configure(foreground=colors["muted"])
        self._text_hint = True

        def focus_in(_event=None) -> None:
            if self._text_hint:
                self.text.delete("1.0", "end")
                self.text.configure(foreground=colors["text"])
                self._text_hint = False

        def focus_out(_event=None) -> None:
            if not self.text.get("1.0", "end").strip():
                self.text.insert("1.0", BODY_HINT)
                self.text.configure(foreground=colors["muted"])
                self._text_hint = True

        self.text.bind("<FocusIn>", focus_in, add="+")
        self.text.bind("<FocusOut>", focus_out, add="+")

    def _value(self, entry: ttk.Entry) -> str:
        value = entry.get().strip()
        return "" if value == getattr(entry, "hint", None) else value

    def _description(self) -> str:
        return "" if self._text_hint else self.text.get("1.0", "end").strip()

    # ------------------------------------------------------------ imágenes
    def _add(self, path: Path) -> None:
        if len(self.images) >= support.MAX_IMAGES or path in self.images:
            return
        self.images.append(path)
        self._render_gallery()

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(parent=self.window, title="Elegí imágenes",
                                            filetypes=[("Imágenes", "*.png *.jpg *.jpeg *.bmp *.gif *.webp")])
        for path in paths:
            self._add(Path(path))

    def _save_temp(self, image, name: str) -> Path:
        path = Path(tempfile.gettempdir()) / "bubble-soporte" / f"{name}-{int(time.time() * 1000)}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        return path

    def _paste(self) -> None:
        from PIL import Image, ImageGrab

        grabbed = ImageGrab.grabclipboard()
        if isinstance(grabbed, Image.Image):
            self._add(self._save_temp(grabbed, "captura"))
        elif isinstance(grabbed, list) and grabbed:
            for path in grabbed:
                if str(path).lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")):
                    self._add(Path(path))
        else:
            self.status.configure(text="No hay ninguna imagen copiada. Sacá una con Win + Shift + S y probá otra vez.")

    def _shot(self) -> None:
        image = self.grab_roblox() if self.grab_roblox else None
        if image is None:
            self.status.configure(text="Roblox no está abierto (o está minimizado).")
            return
        self._add(self._save_temp(image, "roblox"))

    def _render_gallery(self) -> None:
        from PIL import Image, ImageTk

        for child in self.gallery.winfo_children():
            child.destroy()
        self._thumbs.clear()
        for path in self.images:
            cell = ttk.Frame(self.gallery)
            cell.pack(side="left", padx=(0, 8))
            try:
                with Image.open(path) as image:
                    image.thumbnail((84, 60))
                    thumb = ImageTk.PhotoImage(image.convert("RGB"))
            except OSError:
                continue
            self._thumbs.append(thumb)
            ttk.Label(cell, image=thumb).pack()
            remove = ttk.Label(cell, text="✕ Quitar", font="SunValleyCaptionFont", cursor="hand2",
                               foreground=widgets.palette()["muted"])
            remove.pack(pady=(2, 0))
            remove.bind("<Button-1>", lambda _event, p=path: self._remove(p))

    def _remove(self, path: Path) -> None:
        self.images.remove(path)
        self._render_gallery()

    # ------------------------------------------------------------ enviar
    def _send(self) -> None:
        colors = widgets.palette()
        title, description = self._value(self.title), self._description()
        if len(title) < 4:
            self.status.configure(text="Poné un título que cuente el problema, por ejemplo «la voz se corta».",
                                  foreground=colors["warn"])
            self.title.focus_set()
            return
        if len(description) < 10:
            self.status.configure(text="Contá un poco más qué pasó y cómo.", foreground=colors["warn"])
            self.text.focus_set()
            return
        self.send_button.state(["disabled"])
        self.status.configure(text="Enviando…", foreground=colors["muted"])
        report = support.Report(self.kind.get(), title, description, self._value(self.contact), list(self.images))
        with_system, with_log = self.with_system.get(), self.with_log.get()
        result: list[tuple[bool, str]] = []

        def work() -> None:
            report.system = support.system_text() if with_system else ""
            report.log = support.log_text() if with_log else ""
            try:
                result.append((True, support.send(report)))
                support.remember(report)
            except (OSError, ValueError) as exc:
                result.append((False, str(exc)))

        def wait() -> None:
            if not result:
                self.window.after(150, wait)
                return
            ok, message = result[0]
            if ok:
                self.status.configure(text=f"✓ {message}", foreground=colors["good"])
                self.sent = True
                self.window.after(2600, self.close)
                return
            folder = support.fallback(report)
            self.status.configure(text=f"No se pudo enviar desde acá ({message}). Te abrí el correo con el mensaje y "
                                       f"la carpeta «{folder.name}» con las imágenes. Adjuntalas y envialo.",
                                  foreground=colors["warn"])
            self.send_button.state(["!disabled"])

        threading.Thread(target=work, name="bubble-soporte", daemon=True).start()
        wait()

    def close(self) -> None:
        motion.vanish(self.window, self.window.destroy)
