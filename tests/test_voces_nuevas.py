"""Idiomas que no tenían voz (tailandés, tamil, guyaratí, panyabí), voces prestadas que leen mejor, volumen igual al
oído para todas las voces, más presencia para las opacas (la japonesa) y la voz de la PC sin cortes.
"""

import subprocess
import sys

import numpy as np

from bubble.voice import prosody, reading, thai, tts


def tone(hz, seconds=1.0, rate=22050, volume=0.1):
    t = np.arange(int(seconds * rate)) / rate
    return (volume * np.sin(2 * np.pi * hz * t)).astype(np.float32)


# ---------------------------------------------------------------- cómo leen las voces prestadas
def test_serbian_in_cyrillic_is_read_in_latin_letters():
    assert reading.serbian_latin("Љубав, џеп и ђак: ЋАО!") == "Ljubav, džep i đak: ĆAO!"
    assert reading.prepare("Здраво", "sr") == "Zdravo" and reading.prepare("Здраво", "mk") == "Здраво"


class Recorder(tts.Voices):
    def __init__(self):
        super().__init__(use_process=False)
        self.calls = []

    def _piper(self, name, text, pace, expressive, reads_as=None):
        self.calls.append((name, text, reads_as))
        return tone(200), 22050


def test_each_borrowed_voice_reads_with_the_phonetics_that_is_understood_best():
    voices = Recorder()
    voices._one("ml_IN-meera-medium", "வணக்கம்", "ta", "femenina", None, "")
    voices._one("sl_SI-artur-medium", "Здраво", "sr", "masculina", None, "")
    voices._one("sl_SI-artur-medium", "Bok", "hr", "masculina", None, "")
    voices._one("bg_BG-dimitar-medium", "Здраво", "mk", "masculina", None, "")
    assert voices.calls == [("ml_IN-meera-medium", "வணக்கம்", "ta"), ("sl_SI-artur-medium", "Zdravo", None),
                            ("sl_SI-artur-medium", "Bok", None), ("bg_BG-dimitar-medium", "Здраво", "mk")]


def test_tamil_gujarati_and_punjabi_now_have_a_voice(monkeypatch):
    monkeypatch.setitem(tts._piper_state, "error", "")
    voices = tts.Voices(use_process=False)
    voices._catalog = {name: {} for name in ("ml_IN-meera-medium", "ml_IN-arjun-medium", "hi_IN-priyamvada-medium",
                                             "hi_IN-pratham-medium")}
    voices._windows = None
    assert voices.voice_for("ta", "femenina") == "ml_IN-meera-medium"
    assert voices.voice_for("ta", "masculina") == "ml_IN-arjun-medium"
    assert voices.voice_for("gu", "masculina") == "hi_IN-pratham-medium"
    assert voices.voice_for("pa", "masculina") == "hi_IN-priyamvada-medium~masculina"  # (la de hombre se trababa)


def test_the_voice_goes_back_to_its_own_phonetics_after_reading_another_language():
    from bubble.voice.piper_worker import speak

    class Voice:
        config = type("Config", (), {"espeak_voice": "hi"})()
        heard = []

        def synthesize(self, text, config):
            self.heard.append(self.config.espeak_voice)
            return iter(["audio"])

    voice = Voice()
    assert speak(voice, "નમસ્તે", None, "gu") == ["audio"] and voice.heard == ["gu"]
    assert voice.config.espeak_voice == "hi"


# ---------------------------------------------------------------- tailandés
def test_tltk_loads_without_its_heavy_packages(tmp_path, monkeypatch):
    package = tmp_path / "tltk"
    package.mkdir()
    (package / "__init__.py").write_text("from tltk import nlp\nimport gensim\n", encoding="utf-8")
    (package / "nlp.py").write_text(
        "import pandas as pd\nfrom sklearn.ensemble import RandomForestClassifier\nfrom nltk.parse import malt\n"
        "MODEL = RandomForestClassifier(n_estimators=5)\n"
        "def th2ipa(text):\n    return 'sa2.wat2.diː1'\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in [name for name in sys.modules if name.split(".")[0] in ("tltk", *thai.HEAVY)]:
        monkeypatch.delitem(sys.modules, name)
    thai.load()
    from tltk.nlp import th2ipa

    assert th2ipa("สวัสดี") == "sa2.wat2.diː1"
    assert not any(name.split(".")[0] in thai.HEAVY for name in sys.modules)  # no quedan módulos de mentira
    for name in [name for name in sys.modules if name.split(".")[0] == "tltk"]:
        monkeypatch.delitem(sys.modules, name)


def test_the_thai_voice_installs_its_package_without_the_heavy_ones(monkeypatch):
    monkeypatch.setitem(tts._piper_state, "error", "")
    monkeypatch.setattr(tts, "_has_modules", lambda family: family not in tts.NEEDS)
    commands = []
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **_kw: commands.append(command) or subprocess.CompletedProcess(command, 1))
    voices = tts.Voices(use_process=False)
    assert voices.pack_missing("th")
    voices.install_pack("th")
    assert "--no-deps" in commands[0] and "tltk==1.10" in commands[0] and "--no-cache-dir" in commands[0]


# ---------------------------------------------------------------- volumen y timbre
def test_a_deep_voice_and_a_bright_one_end_up_equally_loud_to_the_ear():
    deep, bright = tone(110), tone(2500)
    assert abs(prosody.level_db(deep, 22050) - prosody.level_db(bright, 22050)) < 0.5  # el mismo volumen eléctrico
    assert prosody.loudness_db(bright, 22050) - prosody.loudness_db(deep, 22050) > 3  # pero no al oído
    polish = prosody.Polish()
    deep_out = polish.apply(deep, 22050, "grave", tone=False, bright=False)
    bright_out = polish.apply(bright, 22050, "aguda", tone=False, bright=False)
    assert abs(prosody.loudness_db(deep_out, 22050) - prosody.loudness_db(bright_out, 22050)) < 0.5


def test_only_dull_voices_get_more_presence():
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 0.1, 22050).astype(np.float32)
    spectrum = np.fft.rfft(noise)
    frequencies = np.fft.rfftfreq(len(noise), 1 / 22050)
    dull = np.fft.irfft(spectrum / (1 + (frequencies / 600) ** 4), len(noise)).astype(np.float32)  # apagada
    polish = prosody.Polish()
    assert polish.brightening("opaca", dull, 22050) > 3
    assert polish.brightening("clara", noise, 22050) == 0  # una voz con brillo no se toca
    boosted = prosody.presence(dull, 22050, 6.0)
    assert prosody.timbre(boosted, 22050)[0] > prosody.timbre(dull, 22050)[0] + 4
    assert np.allclose(prosody.presence(dull, 22050, 0.0), dull)


# ---------------------------------------------------------------- sin cortes al reproducir
def test_pc_voices_play_with_a_buffer_that_does_not_cut(monkeypatch):
    from bubble.voice import audio as audio_io

    monkeypatch.setattr(audio_io, "com_ready", lambda: None)  # (en el hilo de las pruebas COM ya está iniciado)

    class Device:
        name = "Parlantes"

        def play(self, data, samplerate, blocksize=None):
            self.blocksize = blocksize

    device = Device()
    audio_io.play(audio_io.Output(device, False), tone(200), 22050)
    assert device.blocksize >= 22050 * 0.2  # (sin indicarlo, Windows usa ~10 ms y la voz se cortaba)
