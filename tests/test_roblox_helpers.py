import pytest

from bubble import win32
from bubble.config import load_config, save_setting
from bubble import roblox
from bubble.roblox import combine_translations, split_message
from bubble.win32 import MOD_ALT, MOD_CONTROL, MOD_SHIFT, Binding, describe_binding, parse_binding, spec_for


def test_split_message_respects_limit_and_words():
    text = " ".join(["palabra"] * 60)  # ~480 caracteres
    parts = split_message(text, 200)
    assert all(len(p) <= 200 for p in parts)
    assert " ".join(parts) == text
    assert split_message("hola   che ") == ["hola che"]
    assert split_message("x" * 250, 200) == ["x" * 200, "x" * 50]


def test_combine_translations_for_mixed_servers():
    assert combine_translations([("en", "hi guys")]) == ["hi guys"]
    assert combine_translations([("en", "hi guys"), ("pt", "oi galera")]) == ["[EN] hi guys | [PT] oi galera"]
    long = [("en", "a" * 120), ("pt", "b" * 120)]
    assert combine_translations(long) == ["[EN] " + "a" * 120, "[PT] " + "b" * 120]


def test_parse_keys_and_mouse_buttons():
    assert parse_binding("F8") == Binding("key", 0, 0x77)
    assert parse_binding("ctrl+shift+t") == Binding("key", MOD_CONTROL | MOD_SHIFT, ord("T"))
    assert parse_binding("alt+space") == Binding("key", MOD_ALT, 0x20)
    assert parse_binding("mouse4") == Binding("mouse", 0, 0x05)
    assert parse_binding("ctrl+mouse5") == Binding("mouse", MOD_CONTROL, 0x06)
    assert parse_binding("mouse3") == Binding("mouse", 0, 0x04)
    with pytest.raises(ValueError):
        parse_binding("hyper+x")
    with pytest.raises(ValueError):
        parse_binding("mouse1")  # clic izquierdo: no permitido


def test_degree_sign_follows_keyboard_layout():
    scan = win32.user32.VkKeyScanW("°")
    if scan == -1:
        with pytest.raises(ValueError):
            parse_binding("°")
        return
    binding = parse_binding("°")
    assert binding.vk == scan & 0xFF
    assert bool(binding.modifiers & MOD_SHIFT) == bool((scan >> 8) & 1)
    # Capturar esa tecla física devuelve el mismo atajo "°".
    assert spec_for(binding.vk, binding.modifiers) == "°"


def test_spec_for_roundtrip_and_names():
    for vk, mods in ((0x05, 0), (0x06, MOD_CONTROL), (0x77, MOD_CONTROL), (ord("T"), MOD_ALT | MOD_SHIFT)):
        assert parse_binding(spec_for(vk, mods)) == Binding("mouse" if vk in (5, 6) else "key", mods, vk)
    assert describe_binding("mouse4") == "Botón lateral del mouse (atrás)"
    assert describe_binding("ctrl+shift+t") == "Ctrl + Shift + T"
    assert describe_binding("°") == "°"


def test_settings_saved_from_window_override_config(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert load_config().roblox.hotkey == "°"
    save_setting("roblox", "hotkey", "mouse4")
    save_setting("user", "tone", 5)
    config = load_config()
    assert config.roblox.hotkey == "mouse4"
    assert config.user.tone == 5


@pytest.mark.parametrize("leaves_char, expected", [(True, ["chat", "backspace"]), (False, ["chat"])])
def test_opening_chat_erases_the_character_the_key_leaves(monkeypatch, leaves_char, expected):
    # Con teclado en español, la tecla del chat escribe "}" en la barra y el texto resultaba "}lol" en lugar de "lol".
    pressed = []
    monkeypatch.setattr(win32, "press_chat_key", lambda: pressed.append("chat"))
    monkeypatch.setattr(win32, "press_backspace", lambda: pressed.append("backspace"))
    monkeypatch.setattr(win32, "chat_key_leaves_a_character", lambda: leaves_char)
    monkeypatch.setattr(roblox.time, "sleep", lambda _s: None)
    roblox.open_chat("/")
    assert pressed == expected
