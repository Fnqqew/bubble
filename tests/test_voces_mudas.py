"""Voces que parecían salir pero no se escuchaban: audio vacío o mudo, la voz de la nube que se corta, la salida que
quedaba en un dispositivo viejo, el proceso de voces colgado y la prueba de voz en otro idioma."""

import subprocess
import sys
import time

import numpy as np
import pytest

from bubble.voice import audio as audio_io
from bubble.voice import tts
from bubble.voice.tts import Speech


def tone(seconds=0.6, rate=22050, volume=0.3):
    t = np.arange(int(seconds * rate)) / rate
    return (volume * np.sin(2 * np.pi * 180 * t)).astype(np.float32)


def test_silence_and_broken_audio_do_not_count_as_a_voice():
    assert tts.audible(tone(), 22050)
    assert not tts.audible(np.zeros(22050, np.float32), 22050)  # una voz que no pudo leer el texto
    assert not tts.audible(tone(volume=0.0005), 22050)  # casi muda
    assert not tts.audible(np.full(22050, np.nan, np.float32), 22050)
    assert not tts.audible(np.zeros(0, np.float32), 22050) and not tts.audible(None, 22050)


class Picky(tts.Voices):
    """Voces con resultados fijos por nombre (sin Piper ni Windows de verdad)."""

    def __init__(self, results):
        super().__init__(use_process=False)
        self.results = results
        self.tried = []

    def candidates(self, language, gender="femenina"):
        return list(self.results)

    def voice_for(self, language, gender="femenina"):
        return next(iter(self.results), None)

    def _one(self, name, text, language, gender, speed, style):
        self.tried.append(name)
        result = self.results[name]
        if isinstance(result, Exception):
            raise result
        return result


def test_a_mute_voice_is_replaced_by_the_next_one_of_the_same_language():
    voices = Picky({"he_IL-saspeech-medium": (np.zeros(22050, np.float32), 22050),
                    "windows:Microsoft Asaf": (tone(), 22050)})
    speech = voices.synthesize("שלום", "he")
    assert speech is not None and voices.tried == ["he_IL-saspeech-medium", "windows:Microsoft Asaf"]
    assert tts.audible(speech.audio, speech.sample_rate)


def test_a_voice_that_cannot_be_downloaded_does_not_leave_you_without_voice():
    voices = Picky({"ta_IN-x": OSError("sin conexión"), "windows:Microsoft Valluvar": (tone(), 22050)})
    assert voices.synthesize("வணக்கம்", "ta") is not None


def test_when_every_voice_is_mute_there_is_no_voice_instead_of_silence():
    voices = Picky({"x": (np.zeros(22050, np.float32), 22050), "y": None})
    assert voices.synthesize("hola", "es") is None


def test_the_backup_list_has_the_windows_voices_and_the_original_piper_voice(monkeypatch):
    from bubble.voice.windows_voices import WindowsVoice

    class Windows:
        def voice_for(self, language, gender="femenina"):
            if not language.startswith("pt"):
                return None
            return WindowsVoice("Microsoft Maria", "pt-BR", "femenina", "c", "t")

    monkeypatch.setitem(tts._piper_state, "error", "")
    voices = tts.Voices(use_process=False)
    voices._catalog = {"pt_BR-faber-medium": {}}
    voices._windows = Windows()
    assert voices.candidates("pt", "masculina") == ["pt_BR-faber-medium", "windows:Microsoft Maria"]
    assert voices.candidates("pt", "femenina") == ["windows:Microsoft Maria", "pt_BR-faber-medium"]


def test_every_language_tests_its_voice_in_its_own_language():
    from bubble.translate.languages import LANGUAGES
    from bubble.voice.samples import SAMPLES, sample_for

    assert set(LANGUAGES) <= set(SAMPLES)
    assert sample_for("es-AR") == SAMPLES["es"] and sample_for("xx") == SAMPLES["en"]


# ---------------------------------------------------------------- la salida y la nube
class Device:
    def __init__(self, name, broken=False):
        self.name, self.broken, self.played = name, broken, []


def test_the_voice_goes_to_the_current_output_not_the_first_one(monkeypatch):
    from bubble.voice.pipelines import VoiceOut

    outputs = [audio_io.Output(Device("Parlantes viejos"), False), audio_io.Output(Device("CABLE Input"), True)]
    monkeypatch.setattr(audio_io, "voice_output", lambda: outputs[0])
    monkeypatch.setattr(audio_io, "monitor_output", lambda: audio_io.Output(Device("Auriculares"), False))
    played = []
    monkeypatch.setattr(audio_io, "play", lambda output, audio, rate: played.append(output.name))

    class Voices:
        def synthesize(self, text, language, style=""):
            return Speech(tone(), 22050)

    out = VoiceOut(Voices(), hear_myself=False)
    assert out.say("hi", "en") and played == ["Parlantes viejos"]
    outputs.pop(0)  # se instaló el micrófono virtual (o cambiaste de auriculares)
    out._output_at -= out.OUTPUT_FRESH_S + 1
    assert out.say("hi", "en") and played[-1] == "CABLE Input"


def test_if_the_device_disappeared_it_looks_for_the_output_again(monkeypatch):
    from bubble.voice.pipelines import VoiceOut

    outputs = [audio_io.Output(Device("Auriculares Bluetooth"), False), audio_io.Output(Device("Parlantes"), False)]
    monkeypatch.setattr(audio_io, "voice_output", lambda: outputs.pop(0))
    played = []

    def play(output, audio, rate):
        if output.name == "Auriculares Bluetooth":
            raise OSError("el dispositivo no está")
        played.append(output.name)

    monkeypatch.setattr(audio_io, "play", play)

    class Voices:
        def synthesize(self, text, language, style=""):
            return Speech(tone(), 22050)

    assert VoiceOut(Voices(), hear_myself=False).say("hi", "en") and played == ["Parlantes"]


def test_if_the_cloud_sends_no_audio_the_phrase_goes_with_your_pc_voice(monkeypatch):
    from bubble.voice.pipelines import VoiceOut

    monkeypatch.setattr(audio_io, "voice_output", lambda: audio_io.Output(Device("CABLE Input"), True))
    played = []
    monkeypatch.setattr(audio_io, "play", lambda output, audio, rate: played.append(("pc", rate)))
    monkeypatch.setattr(audio_io, "play_stream", lambda *a, **k: played.append("nube"))

    class Local:
        def synthesize(self, text, language, style=""):
            return Speech(tone(), 22050)

    class Cloud:
        local = Local()

        def stream(self, text, language, style=""):
            return 24000, iter([])  # la conexión se cortó antes de mandar audio

        def synthesize(self, text, language, style=""):
            raise AssertionError("no se vuelve a pagar la nube")

    assert VoiceOut(Cloud(), hear_myself=False).say("wait for me", "en") and played == [("pc", 22050)]


def test_a_cloud_reply_without_sound_goes_with_your_pc_voice(monkeypatch):
    from bubble.cloud import speak

    class Silent:
        def request(self, *args):
            return np.zeros(24000, dtype="<i2").tobytes()

    class Local:
        gender, speed = "femenina", 1.0

        def synthesize(self, text, language, gender=None, speed=None, style=""):
            return "local"

    monkeypatch.setattr(speak, "pool", lambda timeout=0: Silent())
    assert speak.CloudVoices("clave", Local()).synthesize("wait for me", "en") == "local"


# ---------------------------------------------------------------- el proceso de voces colgado
def test_a_hung_voice_process_is_restarted_instead_of_waiting_forever(monkeypatch):
    from bubble.voice import piper_worker

    def asleep(self):
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, creationflags=piper_worker.NO_WINDOW)

    monkeypatch.setattr(piper_worker.PiperProcess, "_start", asleep)
    process = piper_worker.PiperProcess()
    started = time.monotonic()
    with pytest.raises(piper_worker.VoiceError):
        process._ask({"op": "say"}, timeout=1.0)
    assert time.monotonic() - started < 10 and process._process is None


# ---------------------------------------------------------------- idiomas sin voz que ahora hablan
def test_lithuanian_is_read_by_the_latvian_voice(monkeypatch):
    monkeypatch.setitem(tts._piper_state, "error", "")
    voices = tts.Voices(use_process=False)
    voices._catalog = {"lv_LV-aivars-medium": {}, "lt_LT-reginute1-medium": {}}
    voices._windows = None
    assert voices.voice_for("lt", "masculina") == "lv_LV-aivars-medium"  # (la lituana de Piper no habla)


def test_a_voice_that_cannot_load_is_not_tried_again(monkeypatch):
    from bubble.voice import piper_worker

    monkeypatch.setitem(tts._piper_state, "error", "")

    class Worker:
        failed, loaded, calls = "", [], 0

        def say(self, *args):
            Worker.calls += 1
            raise piper_worker.VoiceError("ValueError: 'lithuanian' is not a valid PhonemeType")

    voices = tts.Voices()
    voices._catalog = {"xx_XX-rota-medium": {}}
    voices._windows = None
    monkeypatch.setattr(voices, "_worker", lambda: Worker())
    monkeypatch.setattr(voices, "_files", lambda name: ("modelo", "config"))
    monkeypatch.setattr(voices, "voice_for", lambda language, gender="femenina": "xx_XX-rota-medium")
    assert voices.synthesize("hola", "xx") is None and voices.synthesize("hola", "xx") is None
    assert Worker.calls == 1  # la segunda vez ya se sabe que no habla


def test_the_japanese_voice_pack_is_installed_only_when_needed(monkeypatch):
    import subprocess

    monkeypatch.setitem(tts._piper_state, "error", "")
    installed = []
    monkeypatch.setattr(tts, "_has_modules", lambda family: bool(installed) or family not in tts.NEEDS)
    voices = tts.Voices(use_process=False)
    assert voices.pack_missing("ja") and not voices.pack_missing("es")

    def pip(command, **_kw):
        installed.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", pip)
    assert voices.install_pack("ja") and "pyopenjtalk-plus" in installed[0]
    assert not voices.pack_missing("ja") and voices.install_pack("ja") and len(installed) == 1


def test_a_phrase_with_only_emojis_is_not_reported_as_a_missing_voice():
    from bubble.voice.pipelines import VoiceOut

    class Voices:
        def synthesize(self, text, language, style=""):
            raise AssertionError("no hay nada que decir")

    out = VoiceOut(Voices(), output=audio_io.Output(Device("CABLE Input"), True), hear_myself=False)
    assert out.say("👍👍", "en") and out.say("...", "es")
