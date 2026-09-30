"""La ventana completa se construye oculta, sin conectarse ni acceder al micrófono, para que un error de arranque no
pase inadvertido.
"""

import tkinter as tk

import pytest


@pytest.fixture
def window(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))  # configuración y estado de prueba
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    from bubble.ui import main_window as mw
    from bubble.config import load_config
    from bubble.ui.voice_panel import VoicePanel

    for name in ("_connect", "_start_hotkey", "_start_screenshots", "_sync_shortcut", "open_tutorial"):
        monkeypatch.setattr(mw.BubbleWindow, name, lambda self: None)
    monkeypatch.setattr(VoicePanel, "early_start", lambda self: None)
    monkeypatch.setattr(VoicePanel, "warm_up", lambda self: None)  # evita que preparar la voz descargue ~60 MB
    root = tk.Tk()
    root.withdraw()
    root.attributes("-alpha", 0.0)
    try:
        yield mw.BubbleWindow(load_config(), root=root)
    finally:
        from bubble import pro
        from bubble.ui import theme

        pro.set_active(False)
        theme.tint_accent(root, False)
        root.destroy()


def test_the_window_builds_with_every_page(window):
    from bubble.ui import app_view

    for page in app_view.PAGES:
        window.page_var.set(page)
        app_view._show_page(window)
        window.root.update_idletasks()
    assert window.title_label.cget("text") == "Bubble"


def test_bubble_pro_turns_the_window_gold_and_back(window, monkeypatch):
    import bubble.cloud.keys
    from bubble import pro

    changes = []
    monkeypatch.setattr(bubble.cloud.keys, "load_key", lambda: "clave-de-prueba")
    monkeypatch.setattr(window.voice_panel, "pro_changed", lambda: changes.append(pro.active()))
    window.set_pro(True)
    assert pro.active() and window.config.pro.enabled
    assert window.title_label.cget("text") == "Bubble Pro" and window.pro_badge.winfo_manager() == "pack"
    assert window.pro_panel.enabled_var.get()
    window.set_pro(False, reason="Tu cuenta de Deepgram no tiene saldo")
    assert not pro.active() and not window.config.pro.enabled
    assert window.title_label.cget("text") == "Bubble" and window.pro_badge.cget("text") == "BASIC"
    assert "sin saldo" in window.status.cget("text") or "saldo" in window.status.cget("text")
    assert changes == [True, False]  # la voz se reconstruyó en ambos cambios


def test_pro_needs_a_saved_key(window):
    from bubble import pro

    window.set_pro(True)  # sin clave guardada, no se activa
    assert not pro.active() and not window.config.pro.enabled


def test_what_does_not_apply_is_blurred_and_locked(window):
    voice = window.voice_panel
    voice.subtitles_var.set(False)
    voice._toggle_subtitles()
    voice.speak_var.set(True)
    voice.mode_var.set("directo")
    voice._toggle_speak()
    window.root.update()
    assert voice.earshot_box.dimmed and window.subs_box.dimmed  # sin subtítulos: radio y apariencia bloqueados
    assert not voice.speak_rows.dimmed and voice.button_rows.dimmed  # modo directo: sin botón para hablar
    from tkinter import ttk

    def controls(widget):
        for child in widget.winfo_children():
            if isinstance(child, ttk.Radiobutton):
                yield child
            yield from controls(child)

    buttons = list(controls(voice.earshot_box))
    assert buttons and all(b.instate(["disabled"]) for b in buttons)
    voice.subtitles_var.set(True)
    voice._toggle_subtitles()
    assert not voice.earshot_box.dimmed and all(not b.instate(["disabled"]) for b in buttons)


def test_pro_options_are_locked_in_basic_and_switching_is_quick(window, monkeypatch):
    import time

    import bubble.cloud.keys
    from bubble import pro
    from bubble.ui import app_view

    monkeypatch.setattr(bubble.cloud.keys, "load_key", lambda: "clave-de-prueba")
    monkeypatch.setattr(window.voice_panel, "pro_changed", lambda: None)
    window.page_var.set("pro")
    app_view._show_page(window)
    window.root.update()
    cards = [box for box, _note in window.pro_panel._locked]
    assert cards and all(box.dimmed for box in cards)  # Basic: funciones de Pro bloqueadas
    assert window.pro_panel.try_button.instate(["disabled"])
    start = time.perf_counter()
    window.set_pro(True)
    window.root.update()
    assert time.perf_counter() - start < 0.5  # debe completarse en menos de 0,5 s
    assert pro.active() and not any(box.dimmed for box in cards)
    assert not window.pro_panel.try_button.instate(["disabled"])
    window.set_pro(False)
    assert all(box.dimmed for box in cards)


def test_the_tone_chosen_in_the_bar_is_kept(window):
    from bubble.state import load_state

    window.compose.tone = 3
    window.compose._change_tone(+1)  # ↑ en la barra de escritura
    assert window.config.user.tone == 4
    assert load_state()["settings"]["user"]["tone"] == 4  # el tono se conserva para la próxima sesión
    assert window.tone.get() == window.tone.cget("values")[3]  # Ajustes muestra el tono guardado


def test_your_pc_check_shows_problems_and_fills_the_card(window):
    from bubble import system
    from bubble.ui import app_view

    window.page_var.set("pruebas")
    app_view._show_page(window)  # la página se construye al abrirla por primera vez
    card = window.tests_panel.equipment
    assert "Revisando" in str(card.state.cget("text"))
    info = system.System(windows="Windows 11", build=26200, threads=8, ram_gb=16, microphones=["Mic"],
                         ocr_languages=["en-US"], roblox="roblox.com",
                         claude=system.Claude(installed=True, logged_in=False))
    window._checking_system = True
    window._on_system(info, system.recommend(info))
    assert window.system_info is info and not window._checking_system
    assert "sesión iniciada" in str(window.status.cget("text"))  # el problema queda visible
    assert card.table.winfo_children() and card.advice.winfo_children()
    assert str(card.state.cget("text")) == ""



def test_a_new_version_shows_the_link_and_waits_for_the_match_to_end(window, monkeypatch):
    from bubble import update

    release = update.Release("9.9.0", "Algo nuevo")
    offered = []
    monkeypatch.setattr(update, "should_offer", lambda release: True)
    monkeypatch.setattr(window, "_in_game", lambda: True)  # en juego: no se abre nada sobre el juego
    monkeypatch.setattr(window.root, "after", lambda ms, action: offered.append(ms))
    window._on_update(release, asked=False)
    assert window.update_link.winfo_manager() and "9.9.0" in str(window.update_link.cget("text"))
    assert window._update_window is None and offered == [30000]  # vuelve a preguntar más tarde
    window._on_update(None, asked=True)
    assert not window.update_link.winfo_manager()
    assert "Estás al día" in str(window.status.cget("text"))


def test_without_claude_pro_translates_and_basic_stays_locked(window, monkeypatch):
    import bubble.cloud.keys
    from bubble import pro
    from bubble.ui import app_view

    monkeypatch.setattr(bubble.cloud.keys, "load_key", lambda: "clave-de-prueba")
    monkeypatch.setattr(window.voice_panel, "pro_changed", lambda: None)
    window.page_var.set("pro")
    app_view._show_page(window)  # la página ✦ Pro se construye al abrirla por primera vez
    window._ev_claude_access(("gratis", True))  # cuenta gratuita de Claude, con clave de Bubble Pro
    panel = window.pro_panel
    assert window.cloud_translation and pro.active()  # Pro se activa solo: es el que traduce
    assert panel.enabled_var.get() and panel.no_claude.winfo_manager()
    assert panel.comparison.dimmed  # las opciones de Basic, atenuadas
    panel.enabled_var.set(False)
    panel._toggle()  # el interruptor pasa a Basic
    assert pro.active() and panel.enabled_var.get()  # vuelve a Pro automáticamente
    assert "Basic necesitás Claude" in str(window.status.cget("text"))
    shown = []
    monkeypatch.setattr(window.toast, "show", lambda text, gold, area=None: shown.append(text))
    window._toggle_plan_in_game()  # Ctrl+P en el juego
    assert pro.active() and "Basic necesita Claude" in shown[0]

    window._ev_claude_access(("", False))  # Claude conectado
    assert not window.cloud_translation and not panel.no_claude.winfo_manager()
    assert panel.comparison.dimmed  # sigue atenuado, ahora solo por estar en Pro
    window.set_pro(False)
    assert not pro.active()  # ahora se puede volver a Basic
    assert not panel.comparison.dimmed and not panel.comparison_note.winfo_manager()


def test_without_claude_or_a_key_it_shows_how_to_continue(window, monkeypatch):
    from bubble.ui import main_window as mw

    opened = []
    monkeypatch.setattr(window, "open_no_claude", lambda reason="": opened.append(reason))
    monkeypatch.setattr(window, "_in_game", lambda: False)  # con Roblox al frente, la ventana espera a que se salga
    window.link = "conectando"
    window._ev_claude_access(("sin_sesion", False))
    window._ev_started(mw.NoClaudeError("Falta Claude para traducir"))
    assert opened == [""] and window.link == "error"
    assert "falta Claude" in str(window.greeting.cget("text"))


def test_measuring_your_pc_without_any_voice_says_so(window, monkeypatch):
    import time

    from bubble.ui import app_view

    window.page_var.set("pruebas")
    app_view._show_page(window)
    panel = window.tests_panel

    class NoVoices:
        def synthesize(self, *args, **kwargs):
            return None

    monkeypatch.setattr(panel.voice, "my_asr", lambda: object())
    monkeypatch.setattr(panel, "_voices", lambda: NoVoices())
    monkeypatch.setattr(panel, "_run", lambda work, **kwargs: work())  # sin hilos ni modelos
    panel._test_pc()
    end = time.monotonic() + 3
    while time.monotonic() < end and "No se pudo" not in str(panel.pc_rating.cget("text")):
        window._drain_events() if hasattr(window, "_drain_events") else None
        while not window.events.empty():
            kind, payload = window.events.get_nowait()
            window._handle(kind, payload)
        window.root.update()
    assert str(panel.pc_rating.cget("text")) == "✗ No se pudo medir"  # debe mostrar el fallo en lugar de «Midiendo…»
    assert "voz" in str(panel.pc_info.cget("text"))


# ---------------------------------------------------------------- bloqueos que se enciman (no se pisan)
def test_two_locks_on_the_same_button_do_not_step_on_each_other(window):
    from tkinter import ttk

    from bubble.ui import widgets

    outer = ttk.Frame(window.root)
    inner = ttk.Frame(outer)
    label = ttk.Label(inner, text="Botón para hablar")
    button = ttk.Button(inner, text="Cambiar")
    widgets.dim(outer, True, animate=False)  # voz desactivada
    widgets.dim(inner, True, animate=False)  # modo directo (sin botón)
    widgets.dim(outer, False, animate=False)  # se activa la voz: continúa el modo directo
    assert "disabled" in button.state() and label.dim_color  # el texto permanece atenuado
    widgets.dim(inner, False, animate=False)  # se cambia a «con botón»
    assert "disabled" not in button.state() and not hasattr(label, "dim_color")  # se desbloquea


def test_finishing_a_test_does_not_unlock_a_dimmed_button(window):
    from tkinter import ttk

    from bubble.ui import widgets

    box = ttk.Frame(window.root)
    button = ttk.Button(box, text="Medir")
    widgets.dim(box, True, animate=False, reason="pro")
    widgets.set_enabled(button, True)  # otra prueba termina y se habilitan los botones
    assert "disabled" in button.state()
    widgets.dim(box, False, animate=False, reason="pro")
    assert "disabled" not in button.state()  # se habilita solo al liberarse


# ---------------------------------------------------------------- con Pro, lo de Basic difuminado
def test_with_pro_the_basic_parts_are_dimmed(window, monkeypatch):
    import bubble.cloud.keys
    from bubble import pro
    from bubble.ui import app_view

    monkeypatch.setattr(bubble.cloud.keys, "load_key", lambda: "clave-de-prueba")
    monkeypatch.setattr(window.voice_panel, "pro_changed", lambda: None)
    for page in ("pro", "pruebas"):
        window.page_var.set(page)
        app_view._show_page(window)
    panel, tests = window.pro_panel, window.tests_panel
    assert not panel.comparison.dimmed and not tests.pc_box.dimmed
    window.set_pro(True)
    assert pro.active() and panel.comparison.dimmed and panel.comparison_note.winfo_manager()
    assert tests.pc_box.dimmed and tests.pc_note.winfo_manager()  # «Cuánto tarda en tu PC» mide Basic
    medir = next(b for b in tests._buttons if str(b.cget("text")) == "Medir")
    tests._done()  # termina otra prueba
    assert "disabled" in medir.state()
    window.set_pro(False)
    assert not panel.comparison.dimmed and not tests.pc_box.dimmed and not tests.pc_note.winfo_manager()
    assert "disabled" not in medir.state()


# ---------------------------------------------------------------- un buen micrófono, lo primero
def test_the_microphone_tip_is_the_first_thing_you_see(window, monkeypatch):
    from bubble.state import load_state
    from bubble.ui import app_view

    tip = window.mic_tip
    assert tip and tip["outer"].winfo_manager()
    home = app_view.PAGES and window.pages["inicio"]
    first = home.body.winfo_children()[0] if hasattr(home, "body") else home.winfo_children()[0]
    assert first is tip["outer"]  # primer elemento de Inicio
    assert any("buen micrófono" in str(w.cget("text")) for w in tip["texts"])
    started = []
    monkeypatch.setattr(window.tests_panel, "_test_mic", lambda: started.append(1))
    window.test_microphone()
    assert window.page_var.get() == "pruebas" and started  # «Probar mi micrófono»
    window.hide_mic_tip()
    assert window.mic_tip is None and load_state().get("mic_tip_done")  # no vuelve a aparecer


def test_the_tutorial_opens_with_the_microphone(window):
    from bubble.ui.tutorial import build_steps

    first = build_steps("F8", lambda: None)[0]
    assert first.highlight and "micrófono" in first.highlight[0]


def test_windows_that_open_by_themselves_wait_their_turn(window, monkeypatch):
    import tkinter as tk
    from types import SimpleNamespace

    monkeypatch.setattr(window, "_in_game", lambda: False)
    later = []
    monkeypatch.setattr(window.root, "after", lambda ms, action: later.append((ms, action)))
    other = tk.Toplevel(window.root)
    other.withdraw()
    window._setup_window = SimpleNamespace(window=other)  # «Preparar Bubble» abierta
    opened = []
    window.when_free(lambda: opened.append("tutorial"))
    assert opened == [] and later and later[0][0] == 1500  # espera su turno
    other.destroy()
    later.pop(0)[1]()
    assert opened == ["tutorial"]  # se abre al cerrarse la otra


def test_the_microphone_tip_turns_gold_with_pro(window, monkeypatch):
    import bubble.cloud.keys
    from bubble import pro

    monkeypatch.setattr(bubble.cloud.keys, "load_key", lambda: "clave-de-prueba")
    monkeypatch.setattr(window.voice_panel, "pro_changed", lambda: None)
    bar = window.mic_tip["bar"]
    blue = str(bar.cget("background"))
    window.set_pro(True)
    window.root.update()
    assert str(bar.cget("background")).lower() == pro.gold().lower() != blue.lower()
