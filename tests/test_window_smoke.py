"""La ventana entera se arma (escondida, sin conectarse ni tocar el micrófono): así un error al arrancar no se escapa."""

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
    assert changes == [True, False]  # la voz se rearmó las dos veces


def test_pro_needs_a_saved_key(window):
    from bubble import pro

    window.set_pro(True)  # sin clave guardada no se prende
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
    assert cards and all(box.dimmed for box in cards)  # Basic: lo de Pro, bloqueado
    assert window.pro_panel.try_button.instate(["disabled"])
    start = time.perf_counter()
    window.set_pro(True)
    window.root.update()
    assert time.perf_counter() - start < 0.5  # antes ~0,6 s trabada (en esta PC ahora ~0,07 s)
    assert pro.active() and not any(box.dimmed for box in cards)
    assert not window.pro_panel.try_button.instate(["disabled"])
    window.set_pro(False)
    assert all(box.dimmed for box in cards)


def test_the_tone_chosen_in_the_bar_is_kept(window):
    from bubble.state import load_state

    window.compose.tone = 3
    window.compose._change_tone(+1)  # ↑ en la barra para escribir
    assert window.config.user.tone == 4
    assert load_state()["settings"]["user"]["tone"] == 4  # queda para la próxima (antes volvía a casual)
    assert window.tone.get() == window.tone.cget("values")[3]  # y Ajustes lo muestra


def test_your_pc_check_shows_problems_and_fills_the_card(window):
    from bubble import system
    from bubble.ui import app_view

    window.page_var.set("pruebas")
    app_view._show_page(window)  # (se arma la primera vez que la abrís)
    card = window.tests_panel.equipment
    assert "Revisando" in str(card.state.cget("text"))
    info = system.System(windows="Windows 11", build=26200, threads=8, ram_gb=16, microphones=["Mic"],
                         ocr_languages=["en-US"], roblox="roblox.com",
                         claude=system.Claude(installed=True, logged_in=False))
    window._checking_system = True
    window._on_system(info, system.recommend(info))
    assert window.system_info is info and not window._checking_system
    assert "sesión iniciada" in str(window.status.cget("text"))  # el problema, a la vista
    assert card.table.winfo_children() and card.advice.winfo_children()
    assert str(card.state.cget("text")) == ""



def test_a_new_version_shows_the_link_and_waits_for_the_match_to_end(window, monkeypatch):
    from bubble import update

    release = update.Release("9.9.0", "Algo nuevo")
    offered = []
    monkeypatch.setattr(update, "should_offer", lambda release: True)
    monkeypatch.setattr(window, "_in_game", lambda: True)  # jugando: no se abre nada encima del juego
    monkeypatch.setattr(window.root, "after", lambda ms, action: offered.append(ms))
    window._on_update(release, asked=False)
    assert window.update_link.winfo_manager() and "9.9.0" in str(window.update_link.cget("text"))
    assert window._update_window is None and offered == [30000]  # pregunta de nuevo en un rato
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
    app_view._show_page(window)  # (la página ✦ Pro se arma la primera vez que la abrís)
    window._ev_claude_access(("gratis", True))  # cuenta gratuita de Claude, con clave de Bubble Pro
    panel = window.pro_panel
    assert window.cloud_translation and pro.active()  # Pro se activa solo: es lo que traduce
    assert panel.enabled_var.get() and panel.no_claude.winfo_manager()
    assert panel.comparison.dimmed  # lo de Basic, difuminado
    panel.enabled_var.set(False)
    panel._toggle()  # tocás el interruptor para pasar a Basic
    assert pro.active() and panel.enabled_var.get()  # vuelve a Pro solo
    assert "Basic necesita Claude" in str(window.status.cget("text"))
    shown = []
    monkeypatch.setattr(window.toast, "show", lambda text, gold, area=None: shown.append(text))
    window._toggle_plan_in_game()  # Ctrl+P en el juego
    assert pro.active() and "Basic necesita Claude" in shown[0]

    window._ev_claude_access(("", False))  # conectaste Claude
    assert not window.cloud_translation and not panel.comparison.dimmed and not panel.no_claude.winfo_manager()
    window.set_pro(False)
    assert not pro.active()  # ahora sí se puede volver a Basic


def test_without_claude_or_a_key_it_shows_how_to_continue(window, monkeypatch):
    from bubble.ui import main_window as mw

    opened = []
    monkeypatch.setattr(window, "open_no_claude", lambda reason="": opened.append(reason))
    window.link = "conectando"
    window._ev_claude_access(("sin_sesion", False))
    window._ev_started(mw.NoClaudeError("Falta Claude para traducir"))
    assert opened == [""] and window.link == "error"
    assert "Falta Claude" in str(window.greeting.cget("text"))
