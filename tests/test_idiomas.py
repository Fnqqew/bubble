"""Bubble 4.0: 59 idiomas (con voz de mujer y de hombre donde se puede), la ventana en tu idioma, la barra del juego con
todos los idiomas a la vista y la voz a mano, textos de derecha a izquierda en el juego, subtítulos largos enteros y
actualizaciones solas. Sin conectarse de verdad."""

import json
import time
import tkinter as tk

import pytest

from bubble import i18n


@pytest.fixture(autouse=True)
def _spanish_again():
    yield
    i18n.use("es")


# ---------------------------------------------------------------- todos los idiomas
def test_there_are_many_more_languages_and_each_has_a_voice_where_possible(monkeypatch):
    from bubble.translate.languages import LANGUAGES, NATIVE_NAMES
    from bubble.voice import tts

    assert len(LANGUAGES) >= 59 and {"uk", "sv", "el", "he", "cs", "hu", "ro", "ms", "bn"} <= set(LANGUAGES)
    assert NATIVE_NAMES["uk"] == "Українська" and NATIVE_NAMES["el"] == "Ελληνικά"
    monkeypatch.setitem(tts._piper_state, "error", "")
    voices = tts.Voices(use_process=False)
    voices._catalog = {name.split("#")[0]: {} for pair in tts.CURATED.values() for name in pair if name}
    voices._windows = None
    assert voices.voice_for("uk", "femenina") == "uk_UA-ukrainian_tts-medium#2"  # Tetiana
    assert voices.voice_for("et", "masculina") == "et_EE-news-medium#0"  # Albert
    assert voices.voice_for("hr", "masculina") == "sl_SI-artur-medium"  # el croata, con la voz eslovena
    assert voices.voice_for("fa", "femenina") == "fa_IR-amir-medium~femenina"  # solo hay hombre: se arma la mujer
    assert voices.voice_for("ta") is None  # (sin voz de Piper: la de Windows, si está)


def test_the_detector_knows_the_new_languages_and_still_keeps_your_spanish():
    from bubble.translate.langdetect import LanguageDetector, script_of

    detector = LanguageDetector()
    assert detector.detect("hvem vil bytte med meg").lang in ("no", "da")  # (lingua le dice "nb": acá es "no")
    assert detector.detect("хто хоче обмінятися").lang == "uk"
    assert script_of("дякую") == "cyrillic" and script_of("hola") == "latin" and script_of("תודה") == "hebrew"


@pytest.mark.parametrize("text", ["дякую", "tack", "mersi", "ευχαριστώ", "תודה"])
async def test_a_single_word_in_another_alphabet_or_language_is_translated(text):
    import sys

    sys.path.insert(0, "tests")
    from fakes import FakeProvider

    from bubble.config import Config
    from bubble.translate.engine import Translator
    from bubble.translate.router import Router

    provider = FakeProvider(reply="traducido")
    config = Config()
    config.user.language = "es-AR"
    result = await Translator(config, Router([provider])).translate_incoming(text, "Pedro")
    assert result.status == "translated", text


async def test_your_spanish_is_not_mistaken_for_catalan_among_so_many_languages():
    import sys

    sys.path.insert(0, "tests")
    from fakes import FakeProvider

    from bubble.config import Config
    from bubble.translate.engine import Translator
    from bubble.translate.router import Router

    for text in ("esperame en la torre", "pasame el item", "vamos a la base"):
        provider = FakeProvider(reply="traducido")
        config = Config()
        config.user.language = "es-AR"
        result = await Translator(config, Router([provider])).translate_incoming(text, "Juan")
        assert result.status in ("same_language", "universal", "local"), text


# ---------------------------------------------------------------- la ventana en tu idioma
def test_the_window_speaks_your_language(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "user_folder", lambda: tmp_path)
    assert i18n.use("en") == "en"
    assert i18n.t("Tamaño") == "Size"
    downloaded = i18n.t("Ya bajé Bubble 4.0. Lo instalo cuando no estés jugando.\n")  # con variables
    assert "4.0" in downloaded and "bajé" not in downloaded
    assert i18n.t("• Las voces lejanas y los ruidos no se mandan.").startswith("• ")  # con viñeta adelante
    assert i18n.t("algo que no es de la interfaz") == "algo que no es de la interfaz"
    i18n.install()
    root = tk.Tk()
    root.withdraw()
    try:
        label = tk.Label(root, text="Tamaño")
        assert label.cget("text") == "Size"
        label.configure(text="Micrófono")
        assert label.cget("text") != "Micrófono"
    finally:
        root.destroy()


def test_a_language_without_translation_falls_back_to_english_and_can_be_built(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "user_folder", lambda: tmp_path)
    monkeypatch.setattr(i18n, "system_language", lambda: "sw")  # suajili: no viene con Bubble
    assert i18n.choose("auto") == "sw" and not i18n.has_catalog("sw")
    assert i18n.use("sw") == "en"
    import bubble.tools.ui_strings as ui_strings

    monkeypatch.setattr(ui_strings, "translate", lambda texts, language, **_kw: {text: f"[{language}] {text}"
                                                                                 for text in texts})
    finished = []
    i18n.build_in_background("sw", finished.append).join(5)
    assert finished == [True] and json.loads((tmp_path / "sw.json").read_text(encoding="utf-8"))
    assert i18n.use("sw") == "sw" and i18n.t("Tamaño") == "[Swahili] Tamaño"


def test_every_shipped_translation_keeps_the_variables():
    import re

    slots = re.compile(r"(?<!\{)\{\d+\}(?!\})")
    for path in i18n.LOCALES.glob("[a-z][a-z].json"):
        table = json.loads(path.read_text(encoding="utf-8"))
        broken = [source for source, text in table.items() if sorted(slots.findall(source)) != sorted(
            slots.findall(text))]
        assert not broken, (path.name, broken[:3])


# ---------------------------------------------------------------- la barra del juego
@pytest.fixture
def bar(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    from bubble.ui.overlays import ComposeBar

    monkeypatch.setattr(ComposeBar, "visible", property(lambda self: True))  # (sin mostrarla ni sacarte del juego)
    root = tk.Tk()
    root.withdraw()
    events = []
    bar = ComposeBar(root, lambda *key: events.append(("preview", key)), lambda *a, **k: None, lambda: None)
    bar.on_gender = lambda gender: events.append(("gender", gender))
    bar.on_tone = lambda tone: events.append(("tone", tone))
    bar.targets, bar.index, bar.tone = ["en", "pt", "fr", "de", "it", "ru", "tr", "pl", "uk", "sv"], 0, 3
    yield bar, root, events
    root.destroy()


def test_tab_shows_the_languages_around_and_shift_tab_goes_back(bar):
    bar, root, _events = bar
    bar._next_target()
    root.update_idletasks()
    shown = [child.cget("text") for child in bar.strip.pack_slaves()]
    assert "PT" in shown and "EN" in shown and any("2/10" in text for text in shown)
    bar.entry.event_generate("<Shift-Tab>")
    bar._next_target(step=-1)
    assert bar.targets[bar.index] == "en"


def test_the_voice_and_the_tone_change_from_the_bar(bar):
    bar, root, events = bar
    bar._render_target()
    assert bar.voice_label.cget("text") == "♀ Mujer"
    bar._toggle_gender()
    assert bar.voice_label.cget("text") == "♂ Hombre" and ("gender", "masculina") in events
    root.update_idletasks()

    class Click:
        x = int(bar.tone_view.winfo_reqwidth() * 0.95)

    bar.tone_view.configure(width=bar.tone_view.winfo_reqwidth())
    bar._click_tone(type("Click", (), {"x": 10_000})())
    assert bar.tone == 5 and ("tone", 5) in events


def test_nothing_overlaps_in_the_bar_with_any_language(bar):
    from bubble import pro
    from bubble.translate.languages import LANGUAGES

    bar, root, _events = bar
    bar.targets = ["*", *LANGUAGES]
    bar.labels = {"*": "Todos los del chat (EN + PT + FR)"}
    bar._cycling = True
    inner = bar.WIDTH - 36
    try:
        for active in (False, True):
            pro.set_active(active)
            for index in range(len(bar.targets)):
                bar.index, bar.tone = index, 1
                bar._render_target()
                root.update_idletasks()
                footer = sum(child.winfo_reqwidth() + 12 for child in bar.hint.master.pack_slaves())
                strip = sum(child.winfo_reqwidth() + 2 for child in bar.strip.pack_slaves()) + 8
                assert footer <= inner and strip <= inner, bar.targets[index]
    finally:
        pro.set_active(False)


# ---------------------------------------------------------------- el juego: derecha a izquierda y frases largas
def test_right_to_left_text_is_drawn_in_reading_order():
    from bubble.ui.rtl import shape, visual

    assert visual("שלום") == "םולש"
    assert visual("hola") == "hola"
    assert [hex(ord(c)) for c in shape("سلام")] == ["0xfeb3", "0xfee0", "0xfe8e", "0xfee1"]  # letras unidas


def test_a_long_translation_is_not_cut_and_stays_on_screen_longer():
    from bubble.ui.subtitles import fit_lines
    from bubble.voice.captions import CaptionBoard, Line

    long = " ".join(["ayer estuvimos jugando con los chicos y encontramos una cueva escondida"] * 4)
    size, lines = fit_lines(long, 700, 30, 22)
    assert not lines[-1].endswith("…") and size >= 20 and len(lines) > 2
    board = CaptionBoard("es", lambda *a: None)
    short, longer = Line(1, 1, "en", "hi", "hola"), Line(2, 1, "en", long, long)
    assert board.show_for(longer) > board.show_for(short)


def test_your_voice_can_talk_for_a_minute():
    from bubble.voice.pipelines import VoiceSpeaker

    assert VoiceSpeaker.MAX_SECONDS >= 60


# ---------------------------------------------------------------- actualizarse solo
def test_with_auto_update_a_new_version_downloads_and_installs_when_you_are_not_playing(monkeypatch, tmp_path):
    from bubble import update
    from bubble.update import Release

    launched, closed = [], []
    monkeypatch.setattr(update, "prepare", lambda release, progress: tmp_path)
    monkeypatch.setattr(update, "launch", lambda folder, reopen=True: launched.append((folder, reopen)))

    class Window:
        from bubble.ui.main_window import BubbleWindow

        AUTO_RETRY_MS = 60_000
        _auto_update = BubbleWindow._auto_update
        _update_downloaded = BubbleWindow._update_downloaded
        _install_when_free = BubbleWindow._install_when_free

        def __init__(self):
            self._update_ready = None
            self.events = []
            self.playing = True
            self.after = []
            self.root = type("Root", (), {"after": lambda _s, ms, fn: self.after.append(fn)})()
            self.lines = []

        def _append(self, text, tag):
            self.lines.append(text)

        def _in_game(self):
            return self.playing

        def _dialog_open(self):
            return False

        def _on_close(self):
            closed.append(True)

    window = Window()
    monkeypatch.setattr("bubble.win32.toplevel_hwnd", lambda root: -1)
    window._update_downloaded(Release("9.9", ""), tmp_path)
    assert window._update_ready and not launched and window.after  # jugando: espera
    window.playing = False
    window._install_when_free()
    assert launched == [(tmp_path, True)] and closed  # ya no: se instala y Bubble se reabre
