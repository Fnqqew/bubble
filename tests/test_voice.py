import numpy as np
import pytest

from bubble.voice import audio as audio_io
from bubble.voice.asr import cut_repetitions, pick_models
from bubble.voice.captions import MINE, CaptionBoard, same_words
from bubble.voice.live import FRAME, SAMPLE_RATE, Caption, LiveListener, Settings, drop_overlap
from bubble.voice.pipelines import DirectVoice, VoiceOut, VoiceSpeaker
from bubble.voice.speakers import fbank
from bubble.voice.stt import is_hallucination
from bubble.voice.tts import Speech


def test_whisper_inventions_are_dropped():
    assert is_hallucination("Thank you for watching!")
    assert is_hallucination("Subtítulos realizados por la comunidad de Amara.org")
    assert not is_hallucination("thank you bro, that was sick")


def test_repetitions_from_the_short_window_are_cut():
    assert cut_repetitions("Bro you just stole my kill. Bro, you just stole my kill that") == "Bro you just stole my kill."
    assert cut_repetitions("with the map you can find it for it for it for it for it") == "with the map you can find it for it"
    assert cut_repetitions("Galera, alguém quer trocar pets comigo? Galera") == "Galera, alguém quer trocar pets comigo?"
    assert cut_repetitions("no no no wait for me") == "no no no wait for me"  # repetir a propósito está bien


def test_models_follow_the_processor():
    assert pick_models(12) == ("base", "small")
    assert pick_models(8) == ("tiny", "base")
    assert pick_models(4) == ("", "base")


def test_long_speech_cut_with_overlap_does_not_repeat_words():
    previous = "and with the map you can find the hidden cave where"
    assert drop_overlap(previous, "the hidden cave where the boss is, but be careful") == "the boss is, but be careful"
    assert drop_overlap(previous, "where the boss is") == "the boss is"  # una palabra larga repetida
    assert drop_overlap(previous, "the boss is here") == "the boss is here"  # "the" sola no alcanza


def test_fbank_has_80_bands_every_10_ms():
    feats = fbank(np.zeros(SAMPLE_RATE, dtype=np.float32) + 0.01)
    assert feats.shape == (98, 80)


# ---------------------------------------------------------------- escucha en vivo (sin modelos de verdad)
class ScriptedVad:
    """Detector de voz de mentira: la probabilidad sale de una función del tiempo."""

    def __init__(self, speaking):
        self.speaking = speaking
        self.samples = 0

    def feed(self, samples):
        count = len(samples) // FRAME
        probs = []
        for _ in range(count):
            probs.append(0.9 if self.speaking(self.samples / SAMPLE_RATE) else 0.05)
            self.samples += FRAME
        return np.array(probs, dtype=np.float32)


class FakeWhisper:
    def __init__(self, text="anyone wanna trade?", language="en"):
        self.text, self.language, self.calls = text, language, []

    def transcribe(self, audio, language=None, beam_size=1, prior=None, retry_beam=0, hint="", clean=False):
        from bubble.voice.asr import Heard

        self.calls.append(len(audio) / SAMPLE_RATE)
        return Heard(self.text, language or self.language, 0.99, 0.01, -0.2, len(audio) / SAMPLE_RATE, 0.0)


def run_listener(seconds, speaking, settings=None, partial=True):
    captions = []
    final, quick = FakeWhisper(), FakeWhisper() if partial else None
    listener = LiveListener(final, captions.append, partial_asr=quick, vad=ScriptedVad(speaking),
                            settings=settings)
    stream = np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)
    for start in range(0, len(stream), FRAME * 3):
        listener.feed(stream[start:start + FRAME * 3])
        while (job := listener._next_job()) is not None:  # el trabajo del hilo de texto, en el acto
            job()
    return captions, final, quick


def test_a_phrase_shows_text_while_speaking_and_then_the_final():
    captions, final, quick = run_listener(4.0, lambda t: 0.5 <= t < 2.5)
    assert any(not c.final for c in captions)  # texto mientras habla
    finals = [c for c in captions if c.final]
    assert len(finals) == 1 and finals[0].text == "anyone wanna trade?"
    assert 1.8 <= finals[0].end - finals[0].start <= 2.6  # la frase, con un poco de antes y sin el silencio
    stable = [c for c in captions if c.stable]
    assert stable and captions.index(stable[0]) < captions.index(finals[0])  # se puede traducir antes del final
    assert final.calls == []  # en inglés alcanza la última lectura rápida: no hace falta el modelo preciso


def test_two_phrases_with_a_pause_are_separate():
    captions, *_ = run_listener(6.0, lambda t: 0.5 <= t < 2.0 or 2.8 <= t < 4.5)
    assert len([c for c in captions if c.final]) == 2


def test_very_long_speech_is_cut_into_pieces():
    captions, *_ = run_listener(20.0, lambda t: 0.5 <= t < 18.0, Settings(max_seconds=6.0))
    finals = [c for c in captions if c.final]
    assert len(finals) >= 3 and all(c.end - c.start <= 6.5 for c in finals)


def test_muted_while_your_own_translated_voice_plays():
    captions, *_ = run_listener(0.1, lambda t: False)
    listener = LiveListener(FakeWhisper(), captions.append, vad=ScriptedVad(lambda t: True))
    listener.muted_until = 1e18
    listener.feed(np.ones(SAMPLE_RATE * 2, dtype=np.float32) * 0.1)
    assert listener._current is None and not listener._finished


# ---------------------------------------------------------------- subtítulos y traducción
def test_translation_starts_early_and_is_not_repeated():
    asked = []
    board = CaptionBoard("es-AR", lambda text, lang, voice, piece, done, _how="": asked.append((text, piece, done)))
    board.caption(Caption(1, 0.0, 1.0, "hey guys does anyone", "en", 1))
    board.caption(Caption(1, 0.0, 2.0, "Hey guys, does anyone know?", "en", 1, stable=True))
    assert [a[0] for a in asked] == ["Hey guys, does anyone know?"]  # ya se pidió en la pausa
    board.caption(Caption(1, 0.0, 2.0, "Hey guys does anyone know", "en", 1, final=True))
    assert len(asked) == 1  # el final dice lo mismo: se usa la traducción que ya viene
    asked[0][1]("Che, ¿alguien ")
    asked[0][2]("Che, ¿alguien sabe?")
    line = board.visible()[0]
    assert line.translation == "Che, ¿alguien sabe?" and line.done


def test_final_text_that_changed_is_translated_again():
    asked = []
    board = CaptionBoard("es", lambda text, lang, voice, piece, done, _how="": asked.append((text, piece, done)))
    board.caption(Caption(1, 0.0, 1.0, "wake where is it", "en", 2, stable=True))
    board.caption(Caption(1, 0.0, 1.0, "Wait, where is the left side?", "en", 2, final=True))
    assert [a[0] for a in asked] == ["wake where is it", "Wait, where is the left side?"]
    asked[0][2]("despertá")  # llega tarde la traducción vieja: no pisa a la nueva
    asked[1][2]("Pará, ¿dónde queda el lado izquierdo?")
    assert board.visible()[0].translation == "Pará, ¿dónde queda el lado izquierdo?"


def test_speech_in_your_language_is_not_subtitled():
    asked = []
    board = CaptionBoard("es-AR", lambda *a: asked.append(a))
    board.caption(Caption(1, 0.0, 1.0, "yo me sumo, ¿dónde están?", "es", 3, final=True))
    assert board.visible() == [] and asked == []


def test_your_translated_voice_shows_as_yours():
    board = CaptionBoard("es", lambda *a: None)
    board.mine("dale, voy", "sure, coming", "en")
    line = board.visible()[0]
    assert line.speaker == MINE and line.translation == "sure, coming"


def test_same_words_ignores_case_and_punctuation():
    assert same_words("Hey guys, does anyone know?", "hey guys does anyone know")
    assert not same_words("wake where is it", "Wait, where is the left side?")


# ---------------------------------------------------------------- tu voz
class FakeVoices:
    def synthesize(self, text, language, style=""):
        return Speech(np.zeros(22050, dtype=np.float32), 22050) if language == "en" else None


def fake_devices(monkeypatch):
    played = []
    monkeypatch.setattr(audio_io, "play", lambda output, audio, rate: played.append((output.is_cable, len(audio))))
    monkeypatch.setattr(audio_io, "voice_output", lambda: audio_io.Output(object(), True))  # micrófono virtual
    monkeypatch.setattr(audio_io, "monitor_output", lambda: audio_io.Output(object(), False))
    return played


def test_speaker_says_the_translation_and_you_hear_it_too(monkeypatch):
    import time

    played, events = fake_devices(monkeypatch), []
    whisper = FakeWhisper("hola a todos, alguien quiere cambiar mascotas?")
    speaker = VoiceSpeaker(whisper, VoiceOut(FakeVoices()), lambda text, _how: ("hey everyone, anyone wanna trade pets?", "en"),
                           push_to_talk_vk=0x06, my_language="es-AR",
                           on_event=lambda kind, text: events.append((kind, text)))
    speaker.speak(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    assert [kind for kind, _ in events] == ["entendi", "traduccion"]
    time.sleep(0.2)  # tu copia suena en otro hilo
    assert sorted(played) == [(False, 22050), (True, 22050)]  # a Roblox (micrófono virtual) y a tus auriculares


def test_hearing_yourself_can_be_turned_off(monkeypatch):
    played = fake_devices(monkeypatch)
    out = VoiceOut(FakeVoices(), hear_myself=False)
    assert out.say("hey", "en") and played == [(True, 22050)]
    assert not out.say("hola", "xx")  # sin voz para ese idioma


def test_direct_voice_translates_each_phrase_early_and_in_order(monkeypatch):
    import time

    played, events, asked = fake_devices(monkeypatch), [], []

    def translate(text, _how=""):
        asked.append(text)
        return f"EN: {text}", "en"

    direct = DirectVoice(FakeWhisper(), VoiceOut(FakeVoices()), translate, "es-AR",
                         on_event=lambda kind, text: events.append((kind, text)))
    direct._running.set()
    import threading

    threading.Thread(target=direct._speak_in_order, daemon=True).start()
    direct._caption(Caption(1, 0.0, 1.5, "dale, esperame en la torre", "es", 0, stable=True))
    direct._caption(Caption(1, 0.0, 1.6, "Dale, esperame en la torre.", "es", 0, final=True))
    direct._caption(Caption(2, 2.0, 3.0, "ya voy", "es", 0, final=True))
    time.sleep(0.4)
    direct.stop()
    assert asked == ["dale, esperame en la torre", "ya voy"]  # la primera se pidió en la pausa, una sola vez
    assert [text for kind, text in events if kind == "traduccion"] == ["EN: dale, esperame en la torre", "EN: ya voy"]
    assert direct.listener.language == "es"  # tu idioma ya se sabe: no se detecta


# ---------------------------------------------------------------- micrófono virtual (como Soundpad)
class _FakeRecorder:
    def __init__(self, level):
        self.level = level

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes):
        import time

        time.sleep(0.002)
        return np.full((numframes, 1), self.level, dtype=np.float32)


class _FakePlayer(_FakeRecorder):
    def __init__(self):
        self.blocks = []

    def play(self, block):
        self.blocks.append(float(np.abs(block).max()))


def test_bridge_passes_your_voice_and_lowers_it_while_the_translation_plays(monkeypatch):
    import time

    from bubble.voice import bridge

    player = _FakePlayer()
    cable = type("Cable", (), {"player": lambda self, *a, **k: player})()
    mic = type("Mic", (), {"recorder": lambda self, *a, **k: _FakeRecorder(0.5)})()
    monkeypatch.setattr(bridge, "cable_input", lambda: cable)
    monkeypatch.setattr(bridge, "_microphone", lambda name: mic)
    loop = bridge.MicBridge(duck=0.2)
    assert loop.start()
    for _ in range(100):  # la primera vez carga el audio de Windows
        if len(player.blocks) >= 3:
            break
        time.sleep(0.03)
    assert player.blocks and max(player.blocks[-3:]) == 0.5  # tu voz pasa tal cual
    loop.duck(0.3)
    time.sleep(0.1)
    assert abs(player.blocks[-1] - 0.1) < 1e-6  # mientras suena la traducida, baja
    loop.enabled = False
    time.sleep(0.1)
    assert player.blocks[-1] == 0.0  # "pasar mi voz real" apagado: solo la traducida
    loop.stop()


def test_bridge_does_not_start_without_a_virtual_microphone(monkeypatch):
    from bubble.voice import bridge

    monkeypatch.setattr(bridge, "cable_input", lambda: None)
    loop = bridge.MicBridge()
    assert not loop.start() and "virtual" in loop.error


def test_every_language_gets_both_genders(monkeypatch):
    """Mujer y hombre en cada idioma: la de Piper; si Piper no tiene ese género, una de Windows; si tampoco, la del
    otro género convertida (prosody.change_gender)."""
    from types import SimpleNamespace

    from bubble.voice import tts

    class Windows:
        def __init__(self, voices):
            self.list = voices

        def voice_for(self, language, gender="femenina"):
            family = language.split("-")[0]
            found = [v for v in self.list if v.language.startswith(family)]
            return min(found, key=lambda v: v.gender != gender) if found else None

    monkeypatch.setitem(tts._piper_state, "error", "")
    voices = tts.Voices(use_process=False)
    voices._catalog = {name.split("#")[0]: {} for pair in tts.CURATED.values() for name in pair if name}
    voices._windows = Windows([])
    assert voices.voice_for("en", "masculina") == "en_US-ryan-medium"
    assert voices.voice_for("es-AR", "femenina") == "es_AR-daniela-high"
    assert voices.voice_for("pt", "femenina") == "pt_BR-faber-medium~femenina"  # hecha a partir de la masculina
    assert voices.voice_for("ko", "masculina") == "ko_KR-kss-medium~masculina"
    assert voices.voice_for("tl", "femenina") == "id_ID-news_tts-medium"  # el tagalo, con la voz indonesia
    voices._windows = Windows([SimpleNamespace(name="Microsoft Maria", language="pt-BR", gender="femenina")])
    assert voices.voice_for("pt", "femenina") == "windows:Microsoft Maria"  # una de verdad, si Windows la tiene
    assert voices.voice_for("pt", "masculina") == "pt_BR-faber-medium"
    monkeypatch.setattr(tts, "_has_modules", lambda family: family not in ("ja", "th"))
    assert voices.voice_for("ja") is None and voices.voice_for("th") is None  # (sin sus paquetes ni voz de Windows)


def test_the_other_gender_sounds_like_one():
    from bubble.voice import prosody

    rate = 22050
    t = np.arange(int(rate * 1.2)) / rate
    pitch = 220 * (1 + 0.03 * np.sin(2 * np.pi * 3 * t))  # una "voz" de mujer, con algo de melodía
    phase = 2 * np.pi * np.cumsum(pitch) / rate
    woman = (0.3 * sum(np.sin(k * phase) / k for k in range(1, 12))).astype(np.float32)
    man = prosody.change_gender(woman, rate, "masculina")
    assert len(man) == len(woman)  # misma duración
    assert 100 < prosody.median_pitch(man, rate) < 130  # tono de hombre


# ---------------------------------------------------------------- el botón para hablar: tocar o mantener
class _RealTimeMic:
    """Micrófono falso al ritmo real (silencio: lo que importa es cuándo termina de grabar)."""

    def recorder(self, **_options):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def record(self, numframes):
        import time as _time

        _time.sleep(numframes / 16000)
        return np.zeros(numframes, np.float32)


class _ScriptedVad:
    """Hay voz hasta `speech_until` segundos después de crearlo."""

    def __init__(self, speech_until: float) -> None:
        import time as _time

        self.until = _time.monotonic() + speech_until

    def feed(self, _samples):
        import time as _time

        return np.array([1.0 if _time.monotonic() < self.until else 0.0], np.float32)


def _speaker(held_for: float, speech_s: float):
    import time as _time

    from bubble.voice.pipelines import VoiceSpeaker

    pressed = _time.monotonic()
    speaker = VoiceSpeaker(FakeWhisper("dale, vamos."), None, lambda _t, _how: None, 0, "es", mic_factory=_RealTimeMic,
                           vad_factory=lambda: _ScriptedVad(speech_s), held=lambda: _time.monotonic() - pressed < held_for)
    speaker._running.set()
    return speaker


def test_tap_listens_until_you_stop_talking():
    import time as _time

    started = _time.monotonic()
    audio, early = _speaker(held_for=0.1, speech_s=1.0)._record()
    took = _time.monotonic() - started
    assert audio is not None and early is not None  # lo dicho ya se leyó en la pausa: no hay que leerlo de nuevo
    # 1 s hablando + la pausa: como lo último suena terminado ("dale, vamos."), no se espera todo el silencio
    assert 1.1 <= took <= 1.9


def test_holding_records_until_release():
    import time as _time

    started = _time.monotonic()
    audio, _early = _speaker(held_for=0.9, speech_s=5.0)._record()
    assert audio is not None and 0.8 <= _time.monotonic() - started <= 1.2


def test_tap_without_talking_is_cancelled():
    from bubble.voice.pipelines import VoiceSpeaker

    events = []
    speaker = _speaker(held_for=0.1, speech_s=0.0)
    speaker.NO_SPEECH_S = 0.6
    speaker.on_event = lambda kind, text: events.append(kind)
    assert speaker._record() == (None, None) and "error" in events
    assert VoiceSpeaker.NO_SPEECH_S > 3


def test_only_the_latest_synthetic_voices_stay_loaded(monkeypatch):
    import sys
    import types

    from bubble.voice import tts

    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(
        PiperVoice=types.SimpleNamespace(load=lambda model, config_path=None: model)))
    monkeypatch.setattr(tts, "download", lambda url, path, *args: str(path))
    voices = tts.Voices(use_process=False)  # (sin el proceso aparte: se cargan en este)
    catalog = {n: {"files": {f"{n}.onnx": {}, f"{n}.onnx.json": {}}} for n in "abcd"}
    monkeypatch.setattr(voices, "catalog", lambda: catalog)
    for name in "abca":  # "a" se vuelve a usar: pasa a ser la más reciente
        voices._load_here(name)
    voices._load_here("d")
    assert list(voices._loaded) == ["c", "a", "d"]  # "b", la menos usada hace más tiempo, se liberó


def test_piper_voices_load_in_another_process_without_freezing(monkeypatch):
    """Cargar una voz congelaba la ventana ~2 s (onnxruntime no suelta el candado de Python): va en otro proceso."""
    import threading
    import time

    from bubble.voice import tts

    if tts.piper_blocked():
        pytest.skip("Windows no deja usar Piper en esta PC")
    voices = tts.Voices()
    if not voices.is_downloaded("en", "femenina"):
        pytest.skip("la voz en inglés no está bajada")
    worst, done = [0.0], threading.Event()

    def speak():
        speech[0] = voices.synthesize("Wait for me at the tower.", "en", "femenina")
        done.set()

    speech = [None]
    threading.Thread(target=speak).start()
    last = time.perf_counter()
    while not done.wait(0.01):
        now = time.perf_counter()
        worst[0], last = max(worst[0], now - last), now
    try:
        assert speech[0] is not None and len(speech[0].audio) > speech[0].sample_rate * 0.5
        assert worst[0] < 0.3  # antes: 1,6 a 1,9 s sin responder
        assert voices._process is not None and not voices._process.failed
    finally:
        if voices._process is not None:
            voices._process.close()


def test_a_pause_in_the_middle_of_your_sentence_does_not_cut_it():
    """Tu voz (modo directo): hablás, pausa de 0,8 s y seguís. Si lo último sonó a medias ("…y"), es una sola frase;
    si sonó terminado, son dos (y la primera sale enseguida)."""

    def finals(text):
        captions = []
        whisper = FakeWhisper(text, "es")
        settings = Settings(fast_final_languages=("es",), first_partial_s=60.0, partial_every_s=60.0,
                            end_silence_s=0.7, quick_end_s=0.3, wait_for_tail=True, unfinished_end_s=1.5)
        listener = LiveListener(whisper, captions.append, partial_asr=whisper, settings=settings, language="es",
                                vad=ScriptedVad(lambda t: 0.5 <= t < 2.0 or 2.8 <= t < 4.5))
        stream = np.zeros(int(7.0 * SAMPLE_RATE), dtype=np.float32)
        for start in range(0, len(stream), FRAME * 3):
            listener.feed(stream[start:start + FRAME * 3])
            while (job := listener._next_job()) is not None:
                job()
        return [c for c in captions if c.final]

    assert len(finals("fui a buscar la espada y")) == 1
    assert len(finals("Dale, vamos a la torre.")) == 2


def test_tap_waits_longer_when_you_leave_a_phrase_hanging():
    import time as _time

    speaker = _speaker(held_for=0.1, speech_s=1.0)
    speaker.transcriber = FakeWhisper("fui a buscar la espada y", "es")
    started = _time.monotonic()
    audio, _early = speaker._record()
    assert audio is not None and _time.monotonic() - started >= 1.0 + speaker.UNFINISHED_S - 0.15
