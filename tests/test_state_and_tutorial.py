from bubble import roblox, state
from bubble.geometry import Rect
from bubble.ui.tutorial import build_steps


def test_state_roundtrip_keeps_other_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert state.load_state() == {}
    roblox.save_chat_region(Rect(110, 120, 400, 200), Rect(100, 100, 1920, 1080))
    state.update_state(tutorial_seen=True)
    saved = state.load_state()
    assert saved["tutorial_seen"] is True
    assert roblox.load_chat_region() == {"relative": True, "x": 10, "y": 20, "w": 400, "h": 200, "manual": True}


def test_tutorial_steps_use_hotkey_and_actions():
    calls = []
    steps = build_steps("F8", lambda: calls.append("calibrar"), lambda: calls.append("captura"))
    assert any("F8" in s.title for s in steps)
    actions = [s for s in steps if s.action]
    assert [s.action_label for s in actions] == ["Buscar el chat ahora"]
    for s in actions:
        s.action()
    assert calls == ["calibrar"]
    # Nombra lo que existe en la ventana nueva (no botones viejos).
    text = " ".join(s.title + s.body for s in steps)
    assert "Probar captura" not in text and "Tono al enviar" not in text and "Ctrl+Enter" in text

