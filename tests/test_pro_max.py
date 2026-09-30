"""Bubble 3.0: el radio de escucha y el filtro de ruido (Basic y Pro), las voces de la nube, el ahorro de crédito y lo
que hace fluida la ventana (Tab, desplazamiento, animaciones). Sin conectarse de verdad."""

import threading
import time

import numpy as np
import pytest

from bubble import pro
from bubble.voice.hearing import Earshot, cloud_noise, looks_like_noise, speech_level


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    yield
    pro.set_active(False)


def tone(db: float, seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(16000 * seconds)) / 16000
    return (np.sin(2 * np.pi * 220 * t) * np.sqrt(2) * 10 ** (db / 20)).astype(np.float32)


# ---------------------------------------------------------------- radio de escucha
def test_speech_level_measures_the_voice_not_the_pauses():
    voice = np.concatenate([tone(-20, 1.0), np.zeros(16000, np.float32)])
    assert speech_level(voice) == pytest.approx(-20, abs=0.5)
    assert speech_level(np.zeros(100, np.float32)) < -100


def test_far_voices_are_left_out_and_the_radius_decides_how_far():
    now = [0.0]
    ear = Earshot("normal", clock=lambda: now[0])
    ear.learn(-20)  # alguien al lado
    assert ear.hears(-25) and ear.hears(-38)  # cerca, aunque tenga el micrófono más bajo
    assert not ear.hears(-45)  # la otra punta del mapa
    ear.radius = "lejos"
    assert ear.hears(-45)
    ear.radius = "todo"
    assert ear.hears(-54) and not ear.hears(-60)  # ni con "todas": un murmullo no es una voz entendible


def test_a_shout_does_not_close_the_radius_and_it_opens_again_when_nobody_is_near():
    now = [0.0]
    ear = Earshot("cerca", clock=lambda: now[0])
    ear.learn(-20)
    ear.learn(-4)  # un grito
    assert ear.hears(-20)  # la voz normal de al lado sigue entrando
    assert not ear.hears(-38)
    now[0] += 300  # 5 minutos sin nadie cerca
    assert ear.hears(-38)


def test_noise_is_not_translated():
    assert looks_like_noise("♪♪", 0.1, -0.3, 0.9)  # sin letras
    assert looks_like_noise("ah ah", 0.1, -2.5, 0.9)  # no se entendió nada
    assert looks_like_noise("hmm", 0.95, -1.1, 0.9)  # casi seguro no era voz
    assert looks_like_noise("bla", 0.2, -1.3, 0.2)  # ni siquiera se sabe qué idioma
    assert looks_like_noise("hola", 0.1, -0.2, 0.9, speech_ratio=0.1)  # casi todo era otra cosa
    assert not looks_like_noise("wait for me at the tower", 0.05, -0.45, 0.95)
    # frases reales de una pelea grabada (música y voces pisadas): pasan
    assert not looks_like_noise("Bet you're sitting in my... Yeah, right?", 0.60, -0.89, 0.86, 0.98)
    assert not looks_like_noise("You won't go away because you need attention", 0.67, -0.71, 0.95, 1.0)
    assert not looks_like_noise("No, no", 0.43, -1.92, 0.95, 0.98)
    assert cloud_noise("uh", [0.4])
    assert cloud_noise("mmm yeah", [0.3, 0.35])
    assert not cloud_noise("no", [0.6]) and not cloud_noise("no no", [0.45, 0.5])  # voces reales con ruido
    assert not cloud_noise("trade me", [0.9, 0.95])


def test_the_cloud_is_not_paid_for_far_voices():
    from bubble.cloud.deepgram import DeepgramListener

    class Vad:
        def feed(self, samples):
            return np.array([0.9 if np.max(np.abs(samples)) > 1e-4 else 0.1])

    ear = Earshot("normal")
    listener = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None, vad=Vad(), earshot=ear)
    block = 1600
    near, far = tone(-20, 1.0), tone(-48, 1.0)
    for audio in (near, np.zeros(16000 * 2, np.float32)):
        for i in range(0, len(audio), block):
            listener.feed(audio[i:i + block])
    sent_near = listener._unbilled
    for audio in (far, np.zeros(16000 * 2, np.float32)):
        for i in range(0, len(audio), block):
            listener.feed(audio[i:i + block])
    assert sent_near > 1.0
    assert listener._unbilled == pytest.approx(sent_near)  # la voz lejana no se mandó


# ---------------------------------------------------------------- voces de la nube
def test_each_language_gets_a_voice_with_personality():
    from bubble.cloud.speak import voice_name

    assert voice_name("es-AR") == "aura-2-antonia-es"  # la argentina
    assert voice_name("es-AR", "masculina") == "aura-2-aquila-es"
    assert voice_name("en", "femenina", "alegre") == "aura-2-thalia-en"
    assert voice_name("en-GB", "masculina") == "aura-2-draco-en"
    assert voice_name("ja", "masculina") == "aura-2-ebisu-ja"
    assert voice_name("es-MX", "femenina", "tranquila") == "aura-2-estrella-es"  # todas las de Deepgram, por zona
    assert voice_name("es-ES", "masculina", "tranquila") == "aura-2-nestor-es"
    assert voice_name("es", "masculina", "alegre") == "aura-2-luciano-es"
    assert voice_name("pt-BR") is None  # la nube no tiene portugués: la voz de tu PC


class FakePool:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def _check(self, path):
        from bubble.cloud.errors import CloudError

        self.calls.append(path)
        if self.fail:
            raise CloudError("sin conexión")

    def request(self, method, path, body, headers):
        self._check(path)
        return (np.full(2400, 1000, dtype="<i2")).tobytes()

    def stream(self, method, path, body, headers):
        self._check(path)
        data = (np.full(2400, 1000, dtype="<i2")).tobytes()
        return iter([data[:1001], data[1001:]])  # un pedazo cortado a mitad de muestra

    def warm(self):
        pass


class Local:
    gender, speed = "femenina", 1.0

    def __init__(self):
        self.said = []

    def synthesize(self, text, language, gender=None, speed=None, style=""):
        self.said.append((text, language))
        return "local"

    def voice_for(self, language, gender="femenina"):
        return "piper"


def test_cloud_voices_are_cached_and_fall_back_to_your_pc(monkeypatch):
    from bubble.cloud import speak

    fake = FakePool()
    monkeypatch.setattr(speak, "pool", lambda timeout=0: fake)
    local, failures = Local(), []
    voices = speak.CloudVoices("clave", local, failures.append)
    first = voices.synthesize("gg", "en")
    again = voices.synthesize("gg", "en")
    assert first.sample_rate == 24000 and len(first.audio) == 2400
    assert len(fake.calls) == 1 and np.allclose(first.audio, again.audio)  # "gg" otra vez no se paga
    assert "speed=" in fake.calls[0] and "aura-2-andromeda-en" in fake.calls[0]
    assert voices.synthesize("obrigado", "pt") == "local"  # sin voz en la nube: la de tu PC
    assert voices.synthesize("ok", "en") is None and len(fake.calls) == 1  # prepararla no gasta
    fake.fail = True
    assert voices.synthesize("wait for me", "en") == "local" and failures
    assert pro.month_characters() == 3  # "gg!": se manda con signo (la nube termina mejor las palabras sueltas)


def test_short_cloud_words_get_a_sign_and_a_soft_ending():
    from bubble.cloud.speak import SAMPLE_RATE, soften_end, speakable

    assert speakable("nice") == "nice." and speakable("gg", "exclaim") == "gg!"  # el signo es el de cómo lo dijiste
    assert speakable("vamos", "question") == "vamos?"
    assert speakable("wait for me at the tower") == "wait for me at the tower."
    assert speakable("Good.") == "Good." and speakable("¿vamos?") == "¿vamos?"
    t = np.arange(SAMPLE_RATE // 2) / SAMPLE_RATE
    cut = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)  # termina sonando fuerte (cortada)
    soft = soften_end(cut)
    assert abs(soft[-1]) < 0.01 and np.allclose(soft[:-2000], cut[:-2000])  # solo cambia el final
    quiet = np.concatenate([cut, np.zeros(2400, np.float32)])
    assert np.array_equal(soften_end(quiet), quiet)  # si ya terminaba en silencio, no se toca


def test_cloud_voices_play_while_they_arrive(monkeypatch):
    from bubble.cloud import speak

    fake = FakePool()
    monkeypatch.setattr(speak, "pool", lambda timeout=0: fake)
    voices = speak.CloudVoices("clave", Local())
    rate, pieces = voices.stream("nice one", "en")
    audio = np.concatenate(list(pieces))
    assert rate == 24000 and len(audio) == 2400  # los pedazos se juntan sin perder muestras
    rate, pieces = voices.stream("nice one", "en")
    assert len(fake.calls) == 1 and len(np.concatenate(list(pieces))) == 2400  # guardada
    assert voices.stream("oi", "pt") is None


def test_voice_out_streams_and_keeps_muting_while_it_sounds(monkeypatch):
    from bubble.voice import audio as audio_io
    from bubble.voice.pipelines import VoiceOut

    class Output:
        is_cable = False

    played = []

    def play_stream(output, pieces, rate, on_piece=None):
        for piece in pieces:
            if on_piece:
                on_piece(len(piece) / rate)
            played.append(len(piece))
        return sum(played) / rate

    monkeypatch.setattr(audio_io, "play_stream", play_stream)

    class Voices:
        def stream(self, text, language, style=""):
            return 1000, iter([np.zeros(500, np.float32), np.zeros(700, np.float32)])

    out = VoiceOut(Voices(), hear_myself=False, output=Output())
    heard, ready = [], []
    out.listeners.append(heard.append)
    assert out.say("hello there", "en", on_ready=ready.append)
    assert played == [500, 700] and ready
    assert heard[0] > 0.5 and len(heard) >= 3  # aviso al empezar y en cada pedazo


# ---------------------------------------------------------------- ahorro de crédito
def test_keyterms_fit_the_limit_and_do_not_repeat():
    from bubble.cloud.deepgram import GAME_TERMS, fit_keyterms

    chosen = fit_keyterms(["Pvp", "pvp", "  Blox   Fruits ", *GAME_TERMS, *[f"palabra{i}" for i in range(500)]])
    assert chosen[:2] == ["Pvp", "Blox Fruits"]
    assert len(chosen) <= 100 and sum(max(1, round(len(t) / 3.5)) for t in chosen) <= 450


def test_game_audio_does_not_pay_for_keyterms_but_your_voice_does():
    from bubble.cloud.deepgram import DeepgramListener

    game = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None)
    mine = DeepgramListener("clave", lambda _c: None, source_factory=lambda: None, language="es-AR",
                            keyterms=lambda: ["pvp", "farmear"])
    assert "keyterm" not in game.url() and "diarize=true" in game.url()
    assert "keyterm=pvp" in mine.url() and "keyterm=farmear" in mine.url() and mine._with_keyterms


def test_languages_the_cloud_understands():
    from bubble.cloud.deepgram import model_for, understands

    assert model_for("es-AR") == "nova-3" and model_for("ko") == "nova-3"
    assert understands("th") and understands("tr") and not understands("xx")


def test_silence_around_a_phrase_is_not_sent():
    from bubble.cloud.deepgram import trim_silence

    audio = np.concatenate([np.zeros(32000, np.float32), tone(-20, 1.0), np.zeros(32000, np.float32)])
    trimmed = trim_silence(audio)
    assert 1.0 <= len(trimmed) / 16000 <= 1.6


def test_the_month_cost_adds_listening_and_speaking():
    pro.count_seconds(60, multi=True)
    pro.count_seconds(60, multi=False, keyterms=True)
    pro.count_characters(1000)
    minutes, cost = pro.month_usage()
    assert minutes == pytest.approx(2.0)
    assert cost == pytest.approx(0.0058 + 0.0048 + 0.0013 + 0.030)


# ---------------------------------------------------------------- Basic: Whisper recién si hace falta
def test_local_whisper_loads_only_when_needed(monkeypatch):
    from bubble.voice import asr

    loaded = []

    class Whisper:
        def __init__(self, name, threads):
            loaded.append(name)

        def transcribe(self, audio, **kwargs):
            return "texto"

    monkeypatch.setattr(asr, "FastWhisper", Whisper)
    lazy = asr.LazyWhisper("small", 4)
    assert not loaded and not lazy.loaded
    assert lazy.transcribe(np.zeros(10)) == "texto" and loaded == ["small"]
    lazy.transcribe(np.zeros(10))
    assert loaded == ["small"]


# ---------------------------------------------------------------- Tab y modo directo
def test_tab_through_many_languages_prepares_only_the_last_voice():
    from bubble.voice.pipelines import VoiceOut

    class Output:
        is_cable = False

    prepared, release = [], threading.Event()

    class Voices:
        def is_loaded(self, language):
            return False

        def synthesize(self, text, language, **kwargs):
            prepared.append(language)
            release.wait(2)

    out = VoiceOut(Voices(), output=Output())
    for language in ("en", "pt", "fr", "hi", "ru", "tr"):
        out.warm_up(language)
    release.set()
    deadline = time.monotonic() + 3
    while out._warming and time.monotonic() < deadline:
        time.sleep(0.01)
    assert prepared == ["en", "tr"]  # la que estaba en curso y la última (antes: todas, dos veces)


def test_direct_voice_listens_only_while_you_play():
    from bubble.voice.pipelines import DirectVoice, VoiceOut

    class Output:
        is_cable = False

    class Listener:
        muted_until = 0.0
        on_caption = None

    listener = Listener()
    direct = DirectVoice(None, VoiceOut(None, output=Output()), lambda t, _h="": (t, "en"), "es-AR",
                         listener=listener)
    direct.set_listening(False)
    assert listener.muted_until == float("inf")
    direct._mute(2.0)  # sonó tu voz traducida: no destapa el micrófono fuera del juego
    assert listener.muted_until == float("inf")
    direct.set_listening(True)
    assert listener.muted_until == 0.0


# ---------------------------------------------------------------- ventana: desplazamiento y animaciones
@pytest.fixture
def root():
    import tkinter as tk

    window = tk.Tk()
    window.withdraw()
    yield window
    window.destroy()


def test_pages_glide_to_where_you_scrolled(root):
    from tkinter import ttk

    from bubble.ui import widgets

    root.geometry("300x200")
    page = widgets.Scrollable(root)
    page.pack(fill="both", expand=True)
    for index in range(60):
        ttk.Label(page.body, text=f"fila {index}").pack()
    root.update()
    page.bar.pack(side="right", fill="y")  # (escondida no mide bien: se fuerza)
    page.scroll_by(150)
    deadline = time.monotonic() + 2
    while page._gliding is not None and time.monotonic() < deadline:
        root.update()
        time.sleep(0.005)
    assert page.canvas.canvasy(0) == pytest.approx(150, abs=1)
    page.scroll_to_top()
    assert page.canvas.canvasy(0) == 0


def test_animations_finish_and_can_be_cut(root):
    from bubble.ui import motion

    steps, done = [], []
    motion.animate(root, 0.05, steps.append, lambda: done.append(True))
    deadline = time.monotonic() + 1
    while not done and time.monotonic() < deadline:
        root.update()
        time.sleep(0.005)
    assert done and steps[-1] == pytest.approx(1.0)
    cut = []
    alive = [True]
    motion.animate(root, 0.2, cut.append, alive=lambda: alive[0])
    alive[0] = False
    for _ in range(20):
        root.update()
        time.sleep(0.01)
    assert len(cut) == 1  # se cortó sin seguir tocando nada


def test_new_subtitles_slide_in_and_the_toast_renders():
    from bubble.ui.subtitles import freshness, render_subtitles
    from bubble.ui.toast import render_toast
    from bubble.voice.captions import Line

    now = time.monotonic()
    old = Line(1, 1, "en", "hello", "hola", True, True, heard_at=now - 5)
    new = Line(2, 2, "en", "wait", "esperá", True, True, heard_at=now - 0.05)
    fresh = freshness([old, new], now)
    assert 1 not in fresh and 0 < fresh[2] < 1
    appearing = render_subtitles([old, new], fresh=fresh)
    settled = render_subtitles([old, new])
    assert appearing.size == settled.size
    assert np.asarray(appearing)[..., 3].sum() < np.asarray(settled)[..., 3].sum()  # la nueva todavía tenue
    assert render_toast("✦  Bubble Pro activado", True).size[1] > 30


class _RealTimeSpeaker:
    """Un parlante de mentira que se vacía en tiempo real, como el de Windows: cuenta las veces que se quedó sin
    audio en el medio (eso es lo que se escucha entrecortado)."""

    def __init__(self):
        self.gaps = 0
        self.blocksizes = []

    def player(self, samplerate, channels, blocksize=None):
        speaker = self

        class Player:
            def __enter__(self):
                self.rate, self.capacity = samplerate, (blocksize or int(samplerate * 0.01)) / samplerate
                speaker.blocksizes.append(blocksize)
                self.queued, self.last, self.started = 0.0, time.perf_counter(), False
                return self

            def __exit__(self, *exc):
                return False

            def _drain(self):
                now = time.perf_counter()
                if self.started:
                    self.queued -= now - self.last
                    if self.queued < -0.02:
                        speaker.gaps += 1
                    self.queued = max(0.0, self.queued)
                self.last = now

            def play(self, data):
                seconds = len(data) / self.rate
                while seconds > 1e-9:
                    self._drain()
                    space = self.capacity - self.queued
                    if space <= 0:
                        time.sleep(0.001)
                        continue
                    take = min(space, seconds)
                    self.queued += take
                    seconds -= take
                    self.started = True

        return Player()


def test_cloud_voice_does_not_stutter_when_the_network_hiccups():
    from bubble.voice import audio as audio_io

    rate = 24000

    def network():
        for index in range(30):  # 3 s de voz, de a 0,1 s
            time.sleep(0.15 if index % 6 == 5 else 0.03)  # cada tanto la red se demora
            yield np.zeros(rate // 10, np.float32)

    class Output:
        device = _RealTimeSpeaker()

    played = audio_io.play_stream(Output(), network(), rate)
    assert played == pytest.approx(3.0, abs=0.01)
    assert Output.device.gaps == 0  # antes: se quedaba sin audio en cada demora (entrecortado)
    assert Output.device.blocksizes == [int(rate * audio_io.STREAM_BUFFER_S)]
