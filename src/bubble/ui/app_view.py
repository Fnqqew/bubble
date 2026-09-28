"""La ventana de Bubble: simple a primera vista (tu idioma y cuatro interruptores) y personalizable en Ajustes.

    ┌ Bubble ─────────────── ● Todo listo ┐
    │ Inicio · Voz · Ajustes · Actividad   │
    │ ┌ Hablo ───────── Español (AR) ▾ ┐  │
    │ │ Chat del juego            ( ●) │  │
    │ │ Burbujas                  ( ●) │  │
    │ │ Lo que te dicen por voz   (● ) │  │
    │ │ Tu voz para los demás     (● ) │  │
    │ └────────────────────────────────┘  │
    └──────────────────────────────────────┘

Arma los mismos controles que usa la lógica de `BubbleWindow` (idiomas, tono, estado de Roblox, registro…).
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import TYPE_CHECKING

from .. import pro
from ..config import save_setting
from ..translate.base import clamp_tone
from . import inline, subtitles, theme, widgets

if TYPE_CHECKING:
    from .main_window import BubbleWindow

LOGO = Path(__file__).resolve().parent.parent / "assets" / "bubble.png"
PAGES = {"inicio": "Inicio", "voz": "Voz", "pruebas": "Pruebas", "pro": "✦ Pro", "ajustes": "Ajustes",
         "actividad": "Actividad"}
# Páginas que se arman recién la primera vez que las abrís (son las más pesadas): Bubble abre un poco antes.
LAZY = ("pruebas", "pro")
PILL_NAMES = {"grafito": "Grafito", "medianoche": "Medianoche", "violeta": "Violeta", "bosque": "Bosque",
              "negro": "Negro"}
ACCENT_NAMES = {"azul": "Azul", "verde": "Verde", "rosa": "Rosa", "naranja": "Naranja", "ninguno": "Sin color"}
TEXT_SIZES = {"1.0": "Como el chat", "1.15": "Más grande", "1.3": "Grande"}
SUB_SIZES = {"0.85": "Chicos", "1.0": "Normales", "1.2": "Grandes"}
POSITIONS = {"abajo": "Abajo", "arriba": "Arriba"}
THEMES = {"oscuro": "Oscuro", "claro": "Claro"}
PERFORMANCE = {"auto": "Automático", "alta": "Máxima", "media": "Equilibrado", "baja": "Liviano"}


def build(app: BubbleWindow) -> None:
    root = app.root
    root.configure(background=widgets.palette()["bg"])
    # Las listas desplegables de solo lectura quedaban con el texto resaltado (seleccionado) al elegir o al tener el
    # foco: se saca la selección.
    root.bind_class("TCombobox", "<FocusIn>", lambda event: event.widget.selection_clear(), add="+")
    # Con la ruedita encima, una lista cambiaba de valor sola (y se guardaba) al bajar por la página: ahora la ruedita
    # solo mueve la página.
    root.bind_class("TCombobox", "<MouseWheel>", lambda _event: None)
    root.bind_all("<<ComboboxSelected>>", lambda event: (event.widget.selection_clear(),
                                                         root.after_idle(root.focus_set)), add="+")
    # Con Bubble Pro: una franja dorada arriba de todo (ver apply_pro_look). Está siempre (en Basic, del color del
    # fondo): prenderla o apagarla es solo cambiarle el color. Antes se ponía y se sacaba, y eso redibujaba toda la
    # ventana (~0,4 s trabada).
    colors = widgets.palette()
    app.pro_stripe = tk.Canvas(root, height=3, background=colors["bg"], borderwidth=0, highlightthickness=0)
    app.pro_stripe.pack(fill="x", side="top")
    shell = app.shell = ttk.Frame(root, padding=(22, 18, 22, 10))
    shell.pack(fill="both", expand=True)
    _header(app, shell)

    holder = ttk.Frame(shell)
    holder.pack(fill="x", pady=(16, 14))
    nav = ttk.Frame(holder)
    nav.pack(fill="x")
    app.page_var = tk.StringVar(value="inicio")
    app.nav_buttons = {}
    for key, name in PAGES.items():
        button = ttk.Radiobutton(nav, text=name, value=key, variable=app.page_var, style="Toggle.TButton",
                                 command=lambda: _show_page(app))
        button.pack(side="left", padx=(0, 6))
        app.nav_buttons[key] = button
    # Una rayita debajo de la pestaña elegida, que se desliza hasta la nueva al cambiar.
    app.nav_line = tk.Canvas(holder, height=3, background=colors["bg"], borderwidth=0, highlightthickness=0)
    app.nav_line.pack(fill="x", pady=(5, 0))
    app.nav_mark = app.nav_line.create_rectangle(0, 0, 0, 3, width=0, fill=colors["accent"])
    root.after(120, lambda: _slide_nav(app, animate=False))

    app.pages = {}
    stack = ttk.Frame(shell)
    stack.pack(fill="both", expand=True)
    builders = {"inicio": lambda page: _home(app, page), "voz": app.voice_panel.build_page,
                "pruebas": app.tests_panel.build_page, "pro": app.pro_panel.build_page,
                "ajustes": lambda page: _settings(app, page), "actividad": lambda page: _activity(app, page)}
    app.lazy_pages = {}
    for key in PAGES:
        if key == "actividad":
            parent = app.pages[key] = ttk.Frame(stack)
        else:
            scroll = app.pages[key] = widgets.Scrollable(stack)  # se desplaza si la ventana es chica
            parent = scroll.body
        if key in LAZY:
            app.lazy_pages[key] = (parent, builders[key])  # se arma la primera vez que la abrís
        else:
            builders[key](parent)

    footer = ttk.Frame(shell)
    footer.pack(fill="x", side="bottom", pady=(8, 0))
    links = ttk.Frame(footer)
    links.pack(side="right", anchor="n")
    for text, command in (("Soporte", app.open_support), ("Acerca de", app.open_about)):
        link = ttk.Label(links, text=text, font="SunValleyCaptionFont", foreground=widgets.palette()["accent"],
                         cursor="hand2")
        link.pack(side="left", padx=(12, 0))
        link.bind("<Button-1>", lambda _event, action=command: action())
    app.status = ttk.Label(footer, text="", font="SunValleyCaptionFont", foreground=widgets.palette()["muted"],
                           anchor="w", wraplength=420, justify="left")
    app.status.pack(side="left", fill="x", expand=True)
    app.voice_panel._update_locks(animate=False)  # (Ajustes se arma después de Voz)
    _show_page(app)


# ---------------------------------------------------------------- encabezado
def _header(app: BubbleWindow, parent) -> None:
    head = ttk.Frame(parent)
    head.pack(fill="x")
    if LOGO.exists():
        from PIL import Image, ImageTk

        app._logo = ImageTk.PhotoImage(Image.open(LOGO).convert("RGBA").resize((40, 40), Image.Resampling.LANCZOS))
        ttk.Label(head, image=app._logo).pack(side="left", padx=(0, 12))
    texts = ttk.Frame(head)
    texts.pack(side="left", fill="x", expand=True)
    title = ttk.Frame(texts)
    title.pack(anchor="w")
    app.title_label = ttk.Label(title, text="Bubble", font="SunValleySubtitleFont")
    app.title_label.pack(side="left")
    app.pro_badge = tk.Label(title, text="PRO", font=("Segoe UI", 8, "bold"), padx=7, pady=1, borderwidth=0,
                             background=pro.gold(), foreground=widgets.palette()["bg"])
    app.greeting = ttk.Label(texts, text="Preparando todo… dame un segundito.", font="SunValleyCaptionFont",
                             foreground=widgets.palette()["muted"])
    app.greeting.pack(anchor="w")
    side = ttk.Frame(head)
    side.pack(side="right", anchor="n")
    app.state_chip = ttk.Label(side, text="●  Conectando", font="SunValleyCaptionFont",
                               foreground=widgets.palette()["warn"])
    app.state_chip.pack(anchor="e", pady=(2, 4))
    # Si algo anda raro: reinicia todo el mecanismo sin cerrar Bubble.
    app.refresh_button = ttk.Button(side, text="↻  Refrescar", command=app._refresh)
    app.refresh_button.pack(anchor="e")


def set_state(app: BubbleWindow, kind: str, greeting: str, chip: str) -> None:
    colors = widgets.palette()
    app.greeting.configure(text=greeting)
    app.state_chip.configure(text=f"●  {chip}", foreground=colors.get(kind, colors["muted"]))


def _show_page(app: BubbleWindow) -> None:
    pending = getattr(app, "lazy_pages", {}).pop(app.page_var.get(), None)
    if pending is not None:
        parent, builder = pending
        builder(parent)  # (se arma con los colores del plan de ahora: no hace falta recolorear)
    _slide_nav(app)
    for key, page in app.pages.items():
        if key == app.page_var.get():
            page.pack(fill="both", expand=True)
        else:
            page.pack_forget()
    if app.page_var.get() in ("voz", "pruebas"):
        app.voice_panel.warm_up()  # que «Probar voz» (y tu voz traducida) salga enseguida
    if app.page_var.get() == "pro":
        app.pro_panel.refresh()
    if app.page_var.get() == "pruebas":
        app.tests_panel.refresh_learned()
        if app.ready:
            app.voice_panel._open_voice_lane()  # el carril rápido de Claude, listo para probar


# ---------------------------------------------------------------- Inicio
def _home(app: BubbleWindow, page) -> None:
    from .main_window import LANG_CHOICES, _choice

    box = widgets.card(page, "Hablo", "Te muestro todo en este idioma.")
    app.my_lang = ttk.Combobox(box, values=LANG_CHOICES, state="readonly")
    app.my_lang.set(_choice(app.config.user.language))
    app.my_lang.bind("<<ComboboxSelected>>", app._on_lang_change)
    app.my_lang.pack(fill="x")

    box = widgets.card(page, "Qué traduzco")
    app.read_var = tk.BooleanVar(value=app.config.roblox.read_chat)
    widgets.switch_row(box, "chat", "Chat del juego", "Cada mensaje en tu idioma, encima del original.",
                       app.read_var, app._toggle_reading)
    app.bubbles_var = tk.BooleanVar(value=app.config.roblox.translate_bubbles)
    widgets.switch_row(box, "bubbles", "Burbujas", "Lo que dicen sobre la cabeza de los jugadores.",
                       app.bubbles_var, app._toggle_bubbles)
    voice = app.voice_panel
    widgets.switch_row(box, "listen", "Lo que te dicen por voz", "Subtítulos de quién habla y qué dice.",
                       voice.subtitles_var, voice._toggle_subtitles)
    widgets.switch_row(box, "mic", "Tu voz para los demás", "Hablás en tu idioma y te escuchan en el suyo.",
                       voice.speak_var, voice._toggle_speak)

    box = widgets.card(page, "Para escribir en otro idioma")
    row = ttk.Frame(box)
    row.pack(fill="x")
    ttk.Label(row, text="En el juego apretá").pack(side="left")
    app.hotkey_label = ttk.Label(row, text="", font="SunValleyBodyStrongFont")
    app.hotkey_label.pack(side="left", padx=8)
    ttk.Button(row, text="Cambiar", command=app._change_hotkey).pack(side="right")
    widgets.muted(box, "Escribís como hablás. Enter lo manda al chat traducido; Ctrl+Enter lo dice en voz.")

    info = ttk.Frame(page)
    info.pack(fill="x", pady=(2, 0))
    app.roblox_status = ttk.Label(info, text="Buscando Roblox…", font="SunValleyCaptionFont",
                                  foreground=widgets.palette()["muted"])
    app.roblox_status.pack(anchor="w")
    app.region_label = ttk.Label(info, text=app._region_text(), font="SunValleyCaptionFont",
                                 foreground=widgets.palette()["faint"], wraplength=460, justify="left")
    app.region_label.pack(anchor="w")


# ---------------------------------------------------------------- Ajustes
def _settings(app: BubbleWindow, page) -> None:
    from .main_window import AUTO_CHOICE, LANG_CHOICES, TONE_CHOICES, TONE_HINTS, _choice

    look = app.config.appearance
    box = widgets.card(page, "Apariencia")
    app.theme_var = tk.StringVar(value=look.theme)
    row = widgets.label_row(box, "Tema", pady=(0, 2))
    widgets.segmented(row, app.theme_var, THEMES, lambda: _change_theme(app)).pack(side="right")

    box = widgets.card(page, "Traducciones en el juego", "Cómo se ven las traducciones encima del chat.")
    app.pill_var = tk.StringVar(value=look.pill_color)
    row = widgets.label_row(box, "Fondo")
    _option_menu(row, app.pill_var, PILL_NAMES, lambda: _change_look(app))
    app.accent_var = tk.StringVar(value=look.accent)
    row = widgets.label_row(box, "Detalle de color")
    _option_menu(row, app.accent_var, ACCENT_NAMES, lambda: _change_look(app))
    app.opacity_var = tk.DoubleVar(value=look.pill_opacity)
    row = widgets.label_row(box, "Opacidad")
    ttk.Scale(row, from_=0.7, to=1.0, variable=app.opacity_var, length=180,
              command=lambda _v: later(app, "look", lambda: _change_look(app))).pack(side="right")
    app.text_var = tk.StringVar(value=_closest(look.text_scale, TEXT_SIZES))
    row = widgets.label_row(box, "Letra")
    _option_menu(row, app.text_var, TEXT_SIZES, lambda: _change_look(app))
    app.look_preview = ttk.Label(box)
    app.look_preview.pack(anchor="w", pady=(12, 0))
    _render_preview(app)
    app.shots_var = tk.BooleanVar(value=look.in_screenshots)
    ttk.Checkbutton(box, text="Que salgan en tus capturas y grabaciones", variable=app.shots_var,
                    style="Switch.TCheckbutton", command=lambda: _change_screenshots(app)).pack(anchor="w", pady=(12, 0))
    app.capture_label = widgets.muted(box, "")  # (ver main_window._refresh_capture_label)

    box = app.subs_box = widgets.card(page, "Subtítulos de voz")  # (se difumina sin subtítulos: ver voice_panel)
    app.sub_size_var = tk.StringVar(value=_closest(look.subtitle_size, SUB_SIZES))
    row = widgets.label_row(box, "Tamaño")
    widgets.segmented(row, app.sub_size_var, SUB_SIZES, lambda: _change_subtitles(app)).pack(side="right")
    app.sub_pos_var = tk.StringVar(value=look.subtitle_position)
    row = widgets.label_row(box, "Dónde")
    widgets.segmented(row, app.sub_pos_var, POSITIONS, lambda: _change_subtitles(app)).pack(side="right")
    app.sub_original_var = tk.BooleanVar(value=look.subtitle_original)
    ttk.Checkbutton(box, text="Mostrar también lo que dijeron en su idioma", variable=app.sub_original_var,
                    style="Switch.TCheckbutton", command=lambda: _change_subtitles(app)).pack(anchor="w", pady=(10, 0))

    box = widgets.card(page, "Al escribir")
    row = widgets.label_row(box, "Mandar en")
    app.out_lang = ttk.Combobox(row, values=[AUTO_CHOICE, *LANG_CHOICES], state="readonly", width=30)
    app.out_lang.set(_choice(app.config.user.outgoing_language))
    app.out_lang.bind("<<ComboboxSelected>>", app._on_lang_change)
    app.out_lang.pack(side="right")
    row = widgets.label_row(box, "Tono")
    app.tone = ttk.Combobox(row, values=TONE_CHOICES, state="readonly", width=22)
    app.tone.set(TONE_CHOICES[clamp_tone(app.config.user.tone) - 1])
    app.tone.bind("<<ComboboxSelected>>", app._on_tone_change)
    app.tone.pack(side="right")
    app.tone_hint = widgets.muted(box, TONE_HINTS[clamp_tone(app.config.user.tone)])

    box = widgets.card(page, "Chat de Roblox", "Bubble encuentra el chat solo. Si en algún juego no lo encuentra, "
                                              "marcalo a mano.")
    row = ttk.Frame(box)
    row.pack(fill="x")
    ttk.Button(row, text="Buscar el chat", command=app._detect_chat).pack(side="left")
    ttk.Button(row, text="Marcarlo a mano", command=app._calibrate).pack(side="left", padx=8)
    ttk.Button(row, text="Probar lectura", command=app._capture_test).pack(side="left")

    box = widgets.card(page, "Rendimiento")
    app.perf_var = tk.StringVar(value=app.config.roblox.performance
                                if app.config.roblox.performance in PERFORMANCE else "auto")
    row = widgets.label_row(box, "Modo", pady=(0, 2))
    _option_menu(row, app.perf_var, PERFORMANCE, lambda: _change_performance(app))
    app.perf_label = widgets.muted(box, "Detectando tu PC…")

    box = widgets.card(page, "Instalación", "Bubble instala solo lo que le falta al abrirse. Desinstalar borra todo "
                                            "lo que trajo (elegís qué).")
    row = ttk.Frame(box)
    row.pack(fill="x")
    ttk.Button(row, text="Revisar instalación", command=app.open_setup).pack(side="left")
    ttk.Button(row, text="Desinstalar Bubble…", command=app.open_uninstall).pack(side="right")

    box = widgets.card(page, "Ayuda", "¿Algo no anda o se te ocurre una mejora? Escribilo en Soporte (con capturas, "
                                      "si querés): le llega directo al creador.")
    row = ttk.Frame(box)
    row.pack(fill="x")
    ttk.Button(row, text="Ver el tutorial", command=app.open_tutorial).pack(side="left")
    ttk.Button(row, text="Soporte…", command=app.open_support).pack(side="left", padx=(8, 0))
    ttk.Button(row, text="Acerca de", command=app.open_about).pack(side="left", padx=(8, 0))
    ttk.Label(box, text=f"Traduce: Claude {app.config.claude.model}", font="SunValleyCaptionFont",
              foreground=widgets.palette()["faint"]).pack(anchor="w", pady=(10, 0))


def later(app: BubbleWindow, name: str, action, delay_ms: int = 250) -> None:
    """Hace `action` cuando se deja de mover un deslizador (si no, se guardaba el ajuste en cada píxel)."""
    jobs = app.__dict__.setdefault("_later_jobs", {})
    if name in jobs:
        app.root.after_cancel(jobs[name])
    jobs[name] = app.root.after(delay_ms, lambda: (jobs.pop(name, None), action()))


def _option_menu(parent, variable: tk.StringVar, options: dict[str, str], command) -> ttk.Combobox:
    """Lista desplegable que muestra nombres lindos y guarda el valor."""
    box = ttk.Combobox(parent, values=list(options.values()), state="readonly", width=16)
    box.set(options.get(variable.get(), next(iter(options.values()))))

    def chosen(_event=None):
        label = box.get()
        variable.set(next(key for key, text in options.items() if text == label))
        command()

    box.bind("<<ComboboxSelected>>", chosen)
    box.pack(side="right")
    return box


def _closest(value: float, options: dict[str, str]) -> str:
    return min(options, key=lambda key: abs(float(key) - value))


def _change_theme(app: BubbleWindow) -> None:
    """Cambia el tema detrás de una "foto" de la ventana y después la desvanece: así el cambio es de una vez y suave
    (antes se veía cada parte cambiar por separado)."""
    if app.config.appearance.theme == app.theme_var.get():
        return
    app.config.appearance.theme = app.theme_var.get()
    save_setting("appearance", "theme", app.config.appearance.theme)
    cover = _freeze(app.root)
    theme.apply_theme(app.root, app.config.appearance.theme)
    recolor(app)
    _render_preview(app)  # el fondo de la vista previa sigue al tema
    app.root.update_idletasks()
    app.root.update()
    if cover is not None:
        _fade(cover)


def _freeze(root) -> tk.Toplevel | None:
    """Una ventana sin bordes encima de Bubble con la imagen de cómo se ve ahora."""
    import ctypes
    from ctypes import wintypes

    from PIL import Image, ImageTk

    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        rect = wintypes.RECT()
        ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(rect))
        width, height = rect.right, rect.bottom
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        hdc = user32.GetDC(hwnd)
        memory = gdi32.CreateCompatibleDC(hdc)
        bitmap = gdi32.CreateCompatibleBitmap(hdc, width, height)
        gdi32.SelectObject(memory, bitmap)
        user32.PrintWindow(hwnd, memory, 3)  # solo el interior, con todo dibujado
        buffer = ctypes.create_string_buffer(width * height * 4)

        class Header(ctypes.Structure):
            _fields_ = [("size", wintypes.DWORD), ("width", wintypes.LONG), ("height", wintypes.LONG),
                        ("planes", wintypes.WORD), ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                        ("image_size", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG),
                        ("used", wintypes.DWORD), ("important", wintypes.DWORD)]

        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
        gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(header), 0)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(hwnd, hdc)
        image = Image.frombuffer("RGBA", (width, height), buffer, "raw", "BGRA", 0, 1).convert("RGB")
        cover = tk.Toplevel(root)
        cover.overrideredirect(True)
        cover.transient(root)
        cover.geometry(f"{width}x{height}+{root.winfo_rootx()}+{root.winfo_rooty()}")
        cover._image = ImageTk.PhotoImage(image)
        tk.Label(cover, image=cover._image, borderwidth=0).pack()
        cover.lift()
        cover.update()
        return cover
    except Exception:  # noqa: BLE001 - sin foto, el tema cambia igual
        return None


def _fade(cover: tk.Toplevel, step: int = 0, steps: int = 12) -> None:
    if step >= steps:
        cover.destroy()
        return
    cover.attributes("-alpha", 1 - (step + 1) / steps)
    cover.after(16, lambda: _fade(cover, step + 1, steps))


def apply_pro_look(app: BubbleWindow, animate: bool = False) -> None:
    """Bubble Pro se nota: "Bubble Pro" con una insignia dorada, una franja dorada arriba (que brilla al activarlo),
    el acento de la ventana (botones, interruptores, íconos) dorado y lo mismo en las traducciones dentro del juego.
    Sin Pro, todo vuelve. Tarda unos milisegundos (antes la ventana quedaba trabada ~0,6 s)."""
    recolor(app, animate=animate)
    apply_overlay_style(app)
    if hasattr(app, "look_preview"):
        _render_preview(app)
    for hook in getattr(app, "plan_hooks", ()):
        hook(animate)  # lo que se bloquea o se desbloquea según el plan (página Pro, etc.)


def _slide_nav(app: BubbleWindow, animate: bool = True) -> None:
    """Lleva la rayita de las pestañas hasta la elegida."""
    from . import motion

    button = app.nav_buttons.get(app.page_var.get())
    if button is None:
        return
    if not button.winfo_ismapped() or button.winfo_width() <= 1:
        # La ventana todavía no está en pantalla (se está abriendo): se ubica apenas aparezca.
        if not getattr(app, "_nav_waiting", False):
            app._nav_waiting = True

            def retry() -> None:
                app._nav_waiting = False
                _slide_nav(app, animate=False)

            app.nav_line.after(200, retry)
        return
    left, right = button.winfo_x() + 6, button.winfo_x() + button.winfo_width() - 6
    start = app.nav_line.coords(app.nav_mark) or [left, 0, right, 3]
    x0, x1 = start[0], start[2]
    if not animate or x1 <= x0:
        app.nav_line.coords(app.nav_mark, left, 0, right, 3)
        return
    motion.animate(app.nav_line, 0.22, lambda p: app.nav_line.coords(
        app.nav_mark, x0 + (left - x0) * p, 0, x1 + (right - x1) * p, 3))


def _stripe(app: BubbleWindow, on: bool, animate: bool) -> None:
    """La franja de arriba: dorada con Pro (y un brillo que la recorre al activarlo), del color del fondo en Basic."""
    from . import motion

    stripe = app.pro_stripe
    target = pro.gold() if on else widgets.palette()["bg"]
    start = widgets._hex(stripe, str(stripe.cget("background")))
    stripe.delete("shine")
    if not animate or start == target:
        stripe.configure(background=target)
        return
    motion.animate(stripe, 0.3, lambda p: stripe.configure(background=widgets.mix(start, target, p)))
    if on:
        width = max(1, stripe.winfo_width())
        shine = stripe.create_rectangle(-140, 0, 0, 3, width=0, fill=widgets.mix(pro.gold(), "#ffffff", 0.6),
                                        tags="shine")

        def sweep(p: float) -> None:
            x = -140 + (width + 140) * p
            stripe.coords(shine, x, 0, x + 140, 3)

        motion.animate(stripe, 0.7, sweep, lambda: stripe.delete("shine"))


def _pro_header(app: BubbleWindow, animate: bool = False) -> None:
    """"Bubble Pro" con la insignia PRO dorada y la franja arriba; en Basic, "Bubble" con una insignia BASIC gris."""
    on = pro.active()
    colors = widgets.palette()
    app.title_label.configure(text="Bubble Pro" if on else "Bubble")
    if on:
        app.pro_badge.configure(text="PRO", background=pro.gold(), foreground=colors["bg"])
    else:
        app.pro_badge.configure(text="BASIC", background=colors["card"], foreground=colors["muted"])
    if not app.pro_badge.winfo_manager():
        app.pro_badge.pack(side="left", padx=(8, 0), pady=(4, 0))
    _stripe(app, on, animate)
    app.nav_line.configure(background=colors["bg"])
    app.nav_line.itemconfigure(app.nav_mark, fill=colors["accent"])


def recolor(app: BubbleWindow, animate: bool = False) -> None:
    """Después de cambiar el tema (o de prender o apagar Bubble Pro): lo que no es del tema (textos grises, registro,
    fondos, el acento) toma los colores nuevos."""
    colors = widgets.palette()
    app.root.configure(background=colors["bg"])
    theme.tint_accent(app.root, pro.active())
    _pro_header(app, animate)
    olds = [*widgets.PALETTES.values(), *({"accent": gold} for gold in pro.GOLD.values())]
    for page in app.pages.values():
        if isinstance(page, widgets.Scrollable):
            page.recolor()

    def walk(widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Label):
                blocked = getattr(child, "dim_color", None)  # difuminado (bloqueado): se guarda su color de verdad
                current = blocked or str(child.cget("foreground"))
                new = current
                if getattr(child, "gold", False):
                    new = pro.gold()  # lo de Pro, dorado siempre (con el dorado del tema)
                else:
                    for old in olds:
                        for key in ("muted", "faint", "accent", "good", "warn", "bad"):
                            if current == old.get(key):
                                new = colors[key]
                if blocked:
                    child.dim_color = new
                    child.configure(foreground=widgets.mix(widgets._hex(child, new), colors["bg"], widgets.DIM))
                elif new != current:
                    child.configure(foreground=new)
            walk(child)

    walk(app.root)
    theme.style_text(app.log)
    _log_tags(app)


def _change_look(app: BubbleWindow, redraw_preview: bool = True) -> None:
    look = app.config.appearance
    look.pill_color, look.accent = app.pill_var.get(), app.accent_var.get()
    look.pill_opacity = round(float(app.opacity_var.get()), 2)
    look.text_scale = float(app.text_var.get())
    for key in ("pill_color", "accent", "pill_opacity", "text_scale"):
        save_setting("appearance", key, getattr(look, key))
    apply_overlay_style(app)
    if redraw_preview:
        _render_preview(app)


def apply_overlay_style(app: BubbleWindow) -> None:
    look = app.config.appearance
    # Con Bubble Pro, la rayita de las traducciones en el juego es dorada (si no elegiste otro color).
    accent = "dorado" if pro.active() and look.accent == "azul" else look.accent
    inline.set_style(look.pill_color, look.pill_opacity, accent, look.text_scale)
    subtitles.set_subtitles(look.subtitle_size, look.subtitle_position, look.subtitle_original)


def _render_preview(app: BubbleWindow) -> None:
    """Muestra cómo queda una traducción con los ajustes elegidos, sobre un pedacito de "juego"."""
    from PIL import Image, ImageDraw, ImageTk

    width, height = 440, 64
    scene = Image.new("RGBA", (width, height), (118, 170, 228, 255))
    draw = ImageDraw.Draw(scene)
    draw.rectangle((0, 40, width, height), fill=(72, 132, 62, 255))
    draw.ellipse((300, 6, 380, 56), fill=(46, 110, 50, 255))
    size = int(round(16 * inline.STYLE.scale))
    name_font = inline._font(15)
    draw.text((12, 32), "Pedro_BR:", font=name_font, fill=(120, 200, 255), anchor="lm", stroke_width=1,
              stroke_fill=(0, 0, 0))
    left = 16 + int(name_font.getlength("Pedro_BR: "))
    text = "¿alguien sabe dónde está el jefe?"
    pill_width = min(width - left - 8, int(inline._font(size, text).getlength(text)) + inline.TEXT_INSET + 12)
    pill = inline.render_pill(pill_width, max(24, size + 10), text, size, fill=inline.STYLE.fill,
                              text_color=inline.CHAT_TEXT, accent=inline.STYLE.accent)
    scene.alpha_composite(pill, (left - inline.PILL_BEFORE_TEXT, 32 - pill.height // 2))
    rounded = Image.new("L", scene.size, 0)
    ImageDraw.Draw(rounded).rounded_rectangle((0, 0, width - 1, height - 1), 10, fill=255)
    scene.putalpha(rounded)
    background = Image.new("RGBA", scene.size, widgets.palette()["card"])
    background.alpha_composite(scene)
    app._look_image = ImageTk.PhotoImage(background.convert("RGB"))
    app.look_preview.configure(image=app._look_image)


def _change_subtitles(app: BubbleWindow) -> None:
    look = app.config.appearance
    look.subtitle_size = float(app.sub_size_var.get())
    look.subtitle_position = app.sub_pos_var.get()
    look.subtitle_original = bool(app.sub_original_var.get())
    for key in ("subtitle_size", "subtitle_position", "subtitle_original"):
        save_setting("appearance", key, getattr(look, key))
    apply_overlay_style(app)


def _change_screenshots(app: BubbleWindow) -> None:
    app.config.appearance.in_screenshots = bool(app.shots_var.get())
    save_setting("appearance", "in_screenshots", app.config.appearance.in_screenshots)
    app._start_screenshots()


def _change_performance(app: BubbleWindow) -> None:
    app.config.roblox.performance = app.perf_var.get()
    save_setting("roblox", "performance", app.config.roblox.performance)
    app._set_status("Listo: el modo de rendimiento nuevo se usa la próxima vez que abras Bubble.")


# ---------------------------------------------------------------- Actividad
def _activity(app: BubbleWindow, page) -> None:
    ttk.Label(page, text="Lo que fui traduciendo", font="SunValleyBodyStrongFont").pack(anchor="w")
    widgets.muted(page, "Lo último abajo. Azul: lo que te dicen; verde: lo que mandás.", pady=(2, 8))
    frame, app.log = theme.scrolled_text(page, wrap="word", state="disabled", font=("Segoe UI", 10), height=14)
    frame.pack(fill="both", expand=True)
    _log_tags(app)
    app.live = ttk.Label(page, text="", font="SunValleyCaptionFont", foreground=widgets.palette()["muted"])
    app.live.pack(fill="x", pady=(6, 0))

    box = widgets.card(page, "Probar sin Roblox", "Escribí un mensaje como si fuera de otro jugador, o uno tuyo.",
                       pady=(10, 0))
    row = ttk.Frame(box)
    row.pack(fill="x", pady=(0, 6))
    app.speaker = ttk.Entry(row, width=12)
    app.speaker.insert(0, "Player1")
    app.speaker.pack(side="left")
    app.incoming_text = ttk.Entry(row)
    app.incoming_text.pack(side="left", fill="x", expand=True, padx=8)
    app.incoming_text.bind("<Return>", lambda _e: app._send_incoming())
    ttk.Button(row, text="Traducir", command=app._send_incoming).pack(side="right")
    row = ttk.Frame(box)
    row.pack(fill="x")
    ttk.Label(row, text="Vos:", width=12).pack(side="left")
    app.outgoing_text = ttk.Entry(row)
    app.outgoing_text.pack(side="left", fill="x", expand=True, padx=8)
    app.outgoing_text.bind("<Return>", lambda _e: app._send_outgoing())
    ttk.Button(row, text="Traducir", command=app._send_outgoing).pack(side="right")


def _log_tags(app: BubbleWindow) -> None:
    colors = widgets.palette()
    app.log.tag_configure("in", foreground=colors["in"])
    app.log.tag_configure("out", foreground=colors["out"])
    app.log.tag_configure("meta", foreground=colors["meta"], font=("Segoe UI", 8))
    app.log.tag_configure("error", foreground=colors["error"])
    app.log.tag_configure("info", foreground=colors["info"])
