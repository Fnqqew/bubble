"""Bubble 3.2: tu voz y las voces del juego salen más rápido, nombres y jerga, y arreglos (sin conectarse de verdad)."""

import asyncio
import threading
import time

import numpy as np
import pytest

from bubble.voice.live import Caption


# ---------------------------------------------------------------- tu voz: de a oraciones
def test_the_first_sentence_is_said_while_the_rest_is_translated():
    from bubble.voice.pipelines import Sentences

    said = []
    sentences = Sentences(said.append)
    for chunk in ["Hey, wait ", "for me at the tower. ", "Ok. ", "I'm coming right now, ", "don't leave!"]:
        sentences.add(chunk)
    assert said == ["Hey, wait for me at the tower."]  # la primera, apenas llegó entera
    sentences.finish("Hey, wait for me at the tower. Ok. I'm coming right now, don't leave!")
    assert said == ["Hey, wait for me at the tower.", "Ok. I'm coming right now, don't leave!"]  # "Ok." no queda solo


def test_a_corrected_translation_does_not_repeat_what_was_already_said():
    from bubble.voice.pipelines import Sentences

    said = []
    sentences = Sentences(said.append)
    sentences.add("Wait for me at the tower. And bring the sw")
    sentences.finish("Wait for me at the castle. And bring the sword.")  # la versión final cambió lo ya dicho
    assert said == ["Wait for me at the tower.", "And bring the sw"]


def test_speaker_says_each_sentence_in_order(monkeypatch):
    from bubble.voice.pipelines import Heard, VoiceOut, VoiceSpeaker

    class Output:
        is_cable = False

    spoken = []

    class Out(VoiceOut):
        def say(self, text, language, on_ready=None, style=""):
            spoken.append((text, time.monotonic()))
            if on_ready:
                on_ready(0.1)
            time.sleep(0.05)
            return True

    def translate(text, intonation="", on_sentence=None):
        on_sentence("Wait for me at the tower.", "en")
        time.sleep(0.2)  # Claude sigue traduciendo…
        on_sentence("I'm coming right now.", "en")
        return "Wait for me at the tower. I'm coming right now.", "en"

    class Whisper:
        def transcribe(self, *a, **k):
            return Heard("esperame en la torre, ya voy", "es", 1.0, 0.0, -0.1, 2.0, 0.1)

    speaker = VoiceSpeaker(Whisper(), Out(None, output=Output()), translate, 0, "es-AR", by_sentence=True)
    turn = speaker.speak(np.zeros(16000, np.float32))
    assert [text for text, _ in spoken] == ["Wait for me at the tower.", "I'm coming right now."]
    assert spoken[1][1] - spoken[0][1] >= 0.15  # la primera sonó antes de que terminara la traducción
    assert turn.translation.startswith("Wait") and not turn.error


# ---------------------------------------------------------------- Pro: tu voz con el botón, en vivo
class FakeListener:
    def __init__(self):
        self.on_caption = None
        self._pieces = []
        self.voice_at = 0.0
        self.fed = []
        self.finalized = 0
        self.started = None

    def start(self, capture=True):
        self.started = capture

    def stop(self):
        pass

    def feed(self, block):
        self.fed.append(len(block))
        self.voice_at = time.monotonic()

    def finalize(self):
        self.finalized += 1


def test_streamed_turn_knows_when_you_finished_and_has_the_text():
    from bubble.voice.pipelines import StreamedTurn

    listener = FakeListener()
    turn = StreamedTurn(listener, "es-AR")
    turn.start()
    assert listener.started is False  # solo la conexión: el audio lo pasa el botón
    turn.begin()
    turn.feed(np.zeros(1600, np.float32))
    quiet_since = time.monotonic()
    assert turn.said_since(quiet_since) is None  # la nube todavía no cerró la frase
    listener.on_caption(Caption(1, 0.0, 1.0, "che, hacemos pvp", "es", 0, final=True))
    assert turn.said_since(quiet_since) == "che, hacemos pvp"
    heard = turn.finish(1.5)
    assert heard.text == "che, hacemos pvp" and heard.took < 0.1  # ya estaba: no se espera
    assert listener.finalized == 1


def test_streamed_turn_waits_for_the_last_words_when_you_release_early():
    from bubble.voice.pipelines import StreamedTurn

    listener = FakeListener()
    turn = StreamedTurn(listener, "en")
    turn.begin()
    turn.feed(np.zeros(1600, np.float32))  # hablaste y soltaste el botón enseguida

    def cloud_answers():
        time.sleep(0.2)
        listener.on_caption(Caption(2, 0.0, 1.0, "trade me", "en", 0, final=True))

    threading.Thread(target=cloud_answers, daemon=True).start()
    heard = turn.finish(1.0, timeout=2)
    assert heard.text == "trade me" and 0.15 < heard.took < 1.0


def test_cloud_closes_the_phrase_when_you_release_the_button():
    from bubble.cloud.deepgram import DeepgramListener

    captions = []
    listener = DeepgramListener("clave", captions.append, source_factory=lambda: None)
    listener.handle({"type": "Results", "is_final": True, "start": 0, "duration": 1,
                     "channel": {"alternatives": [{"transcript": "wait for me", "words": []}]}})
    assert captions[-1].final is False
    listener.handle({"type": "Results", "is_final": True, "from_finalize": True, "start": 1, "duration": 0.3,
                     "channel": {"alternatives": [{"transcript": "please", "words": []}]}})
    assert captions[-1].final and captions[-1].text == "wait for me please"


def test_a_finished_sentence_is_translated_before_the_pause():
    from bubble.cloud.deepgram import DeepgramListener

    captions = []
    listener = DeepgramListener("clave", captions.append, source_factory=lambda: None)
    listener.handle({"type": "Results", "is_final": True, "start": 0, "duration": 1,
                     "channel": {"alternatives": [{"transcript": "Can you trade me?", "words": []}]}})
    assert captions[-1].stable and not captions[-1].final  # termina con "?": se pide la traducción ya


def test_push_to_talk_sends_everything_while_held():
    from bubble.cloud.deepgram import DeepgramListener

    class Vad:
        def feed(self, samples):
            return np.array([0.1])  # silencio

    listener = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None, vad=Vad(), send_all=True)
    listener.feed(np.zeros(1600, np.float32))
    assert listener._outbox.qsize() == 1  # también el silencio: así la nube nota enseguida que terminaste


# ---------------------------------------------------------------- dos carriles sin mezclar
def test_racing_lanes_never_mix_two_translations():
    from bubble.translate.engine import Translator

    class Router:
        def __init__(self, chunks, delay):
            self.chunks, self.delay = chunks, delay

        async def translate(self, request, on_delta=None):
            await asyncio.sleep(self.delay)
            for chunk in self.chunks:
                if on_delta:
                    on_delta(chunk)
                await asyncio.sleep(0.05)
            return "".join(self.chunks)

    engine = Translator.__new__(Translator)
    engine.voice_router = Router(["Hola ", "che"], 0.0)  # el rápido empieza enseguida…
    engine.router = Router(["Buenas"], 0.0)
    got = []
    import bubble.translate.engine as module

    old = module.HEDGE_AFTER_S
    module.HEDGE_AFTER_S = 0.01
    try:
        result = asyncio.run(engine._hedged(object(), got.append))
    finally:
        module.HEDGE_AFTER_S = old
    assert result == "Hola che" and got == ["Hola ", "che"]  # la voz sigue a uno solo


# ---------------------------------------------------------------- nombres, siglas y jerga
def test_spelled_gaming_acronyms_are_joined():
    from bubble.cloud.deepgram import join_spelled

    assert join_spelled("Hacemos p v p en Blox Fruits.") == "Hacemos pvp en Blox Fruits."
    assert join_spelled("estoy a f k, g g") == "estoy afk, gg"
    assert join_spelled("fui y a la plaza") == "fui y a la plaza"  # "y a" son palabras: no se toca


def test_chat_names_are_recognized_as_they_are_said():
    from bubble.capture.chat_parser import NameBook
    from bubble.cloud.deepgram import spoken_names

    book = NameBook()
    for name in ("lucas_br", "xXShadowXx_2012", "lucas_br", "DarkNinja123"):
        book.canonical(name)
    assert book.recent(2) == ["DarkNinja123", "lucas_br"]  # el que habló más recién, primero
    names = spoken_names(book.recent())
    assert names[:2] == ["Dark Ninja", "DarkNinja123"] and "Shadow" in names and "lucas" in names


def test_internet_slang_like_2tf_is_known():
    from bubble.translate.slang import prompt_reference

    reference = prompt_reference()
    assert "2tf" in reference and "intensifier" in reference


# ---------------------------------------------------------------- subtítulos
def test_your_language_is_never_shown_as_a_subtitle():
    from bubble.voice.captions import CaptionBoard

    board = CaptionBoard("es-AR", lambda *a, **k: None, is_native=lambda text, lang: "che" in text)
    board.caption(Caption(1, 0, 1, "che vamos al lobby", "en", 1))  # el reconocimiento dijo inglés, pero es tuyo
    assert not board.lines
    board.caption(Caption(2, 0, 1, "trade me your sword", "en", 1))
    assert [line.original for line in board.lines] == ["trade me your sword"]


def test_subtitle_card_is_fast():
    from bubble.ui.subtitles import render_subtitles
    from bubble.voice.captions import Line

    lines = [Line(i, 1, "en", "hey can you trade me your sword please", "che, ¿me cambiás tu espada?", True, True,
                  heard_at=time.monotonic() - 5) for i in range(3)]
    render_subtitles(lines)
    start = time.perf_counter()
    for _ in range(10):
        render_subtitles(lines)
    assert (time.perf_counter() - start) / 10 < 0.035  # antes ~64 ms por cuadro (trababa a Bubble y al juego)


# ---------------------------------------------------------------- desinstalar: el micrófono virtual primero
def test_the_virtual_mic_is_removed_first_and_by_default(monkeypatch, tmp_path):
    from bubble import install, uninstall

    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    (tmp_path / "local" / "Bubble" / "descargas").mkdir(parents=True)
    monkeypatch.setattr(install, "_cable_ready", lambda: True)
    monkeypatch.setattr(uninstall, "_desktop_link", lambda: None)
    import bubble.voice.devices

    monkeypatch.setattr(bubble.voice.devices, "restore_real_defaults", lambda: [])
    order = []
    monkeypatch.setattr(uninstall, "_uninstall_cable",
                        lambda: order.append(("cable", (tmp_path / "local" / "Bubble" / "descargas").exists())) or "ok")
    parts = {part.key: part for part in uninstall.parts(tmp_path)}
    assert parts["cable"].selected and parts["cable"].available  # como si nunca hubiera estado
    uninstall.run({"cable", "modelos"}, tmp_path, after_exit=False)
    assert order == [("cable", True)]  # antes de borrar las descargas (ahí está su desinstalador)
