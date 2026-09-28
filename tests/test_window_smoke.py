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
