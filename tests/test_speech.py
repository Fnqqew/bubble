"""Cómo se dijo: pausas, preguntas, gritos; lo que aprende de tu voz; y las pruebas de micrófono y de PC."""

import numpy as np

from bubble.voice.checks import PcReport, analyze_mic, rate_pc, word_error_rate
from bubble.voice.profile import VoiceProfile, phrase_key
from bubble.voice.speech import Melody, Usual, melody, sounds_finished, sounds_unfinished

RATE = 16000


def tone(start_hz, end_hz, seconds=1.2, level=0.2, noise=0.0):
    """Una "voz" (tono con armónicos) que va de un tono a otro."""
    t = np.arange(int(seconds * RATE)) / RATE
    freq = np.linspace(start_hz, end_hz, len(t))
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    wave = sum(np.sin(k * phase) / k for k in range(1, 6))
    wave = level * wave / np.max(np.abs(wave))
    if noise:
        wave = wave + np.random.default_rng(0).normal(0, noise, len(wave))
    return wave.astype(np.float32)


def test_a_phrase_left_hanging_waits_for_more():
    assert sounds_unfinished("fui a la tienda y", "es")
    assert sounds_unfinished("Esperame porque.", "es-AR")  # Whisper pone un punto igual
    assert sounds_unfinished("I was gonna say that,", "en")
    assert not sounds_unfinished("Dale, vamos a la torre.", "es")
    assert sounds_finished("¿Vamos a la torre?", "es") and not sounds_finished("vamos a la", "es")


def test_rising_voice_is_a_question_and_falling_is_not():
    rising = melody(np.concatenate([tone(180, 180, 0.8), tone(180, 260, 0.4)]))
    falling = melody(np.concatenate([tone(200, 200, 0.8), tone(200, 160, 0.4)]))
    assert rising.kind() == "question"
    assert "question" not in falling.kind()


def test_shouting_is_noticed_against_your_usual_voice():
    usual = Usual()
    for _ in range(4):
        usual.add(melody(tone(150, 140, level=0.03)))
    shout = melody(tone(240, 230, level=0.5))
    calm = melody(tone(150, 140, level=0.03))
    assert "shout" in shout.kind(usual.get())
    assert calm.kind(usual.get()) == ""


def test_profile_learns_your_words_and_translations(tmp_path):
    profile = VoiceProfile(tmp_path / "perfil.json")
    profile.learn_phrase("che, ¿vamos a farmear al lobby?", "es-AR")
    assert "farmear" in profile.hint("es")
    profile.approve("dale, esperame", "okay, wait for me", "en", "es")
    assert profile.saved("Dale esperame", "en") == "okay, wait for me"  # sin signos ni mayúsculas: la misma frase
    assert profile.examples("en") == (("dale, esperame", "okay, wait for me"),)
    again = VoiceProfile(tmp_path / "perfil.json")  # queda guardado
    assert again.saved("dale, esperame", "en") == "okay, wait for me"
    assert phrase_key("¿vamos?") != phrase_key("vamos") != phrase_key("¡vamos!")
    profile.remember("una frase larguísima que depende mucho del contexto de la partida", "en", "x")
    assert profile.summary()["guardadas"] == 1
    profile.forget()
    assert not any(profile.summary().values())


def test_profile_knows_your_usual_voice_after_a_few_phrases(tmp_path):
    profile = VoiceProfile(tmp_path / "perfil.json")
    assert profile.usual() is None
    for _ in range(5):
        profile.learn_melody(Melody(0.0, 150.0, -30.0, -15.0, 4.0, 0.0, 1.0))
    pitch, level, _effort = profile.usual()
    assert round(pitch) == 150 and round(level) == -30


def test_mic_check_rates_good_quiet_and_noisy_microphones():
    sentence = "che alguien viene conmigo a la torre"
    silence = np.zeros(RATE, np.float32)
    good = np.concatenate([silence + 0.0005, tone(150, 150, 2.0, level=0.2), silence + 0.0005])
    quiet = np.concatenate([silence, tone(150, 150, 2.0, level=0.003), silence])
    noisy = np.concatenate([tone(150, 150, 1.0, level=0.02, noise=0.02), tone(150, 150, 2.0, level=0.05, noise=0.02)])
    assert analyze_mic(good, sentence, sentence).rating == "bien"
    report = analyze_mic(quiet, sentence, sentence)
    assert report.rating == "mal" and any("baja" in tip for tip in report.tips)
    assert analyze_mic(noisy, sentence, sentence).rating in ("mal", "normal")
    assert analyze_mic(good, "che viene a la torre", sentence).rating == "normal"  # entendió solo una parte
    assert word_error_rate("hola che", "hola che") == 0 and word_error_rate("hola che", "") == 1


def test_pc_check_estimates_how_fast_your_voice_sounds():
    fast = rate_pc(PcReport("CPU", 12, 16, ["GPU"], "small", 0.5, 0.2, 1.5))
    slow = rate_pc(PcReport("CPU", 4, 6, [], "base", 2.0, 0.8, 2.8))
    assert fast.rating == "excelente" and fast.expected_s < 2.6
    assert slow.rating == "lenta" and len(slow.tips) >= 3


def test_your_own_question_and_shout_thresholds_are_used(tmp_path):
    profile = VoiceProfile(tmp_path / "perfil.json")
    for _ in range(5):
        profile.learn_melody(Melody(0.0, 150.0, -30.0, -15.0, 4.0, 0.0, 1.0))
    question = Melody(1.2, 150.0, -30.0, -15.0, 4.0, 0.0, 1.0)
    assert profile.intonation(question) == ""  # con el umbral de todos (2 semitonos) no parecía pregunta
    profile.calibrate({"question_rise": 0.8, "shout_db": 5.0})  # esta persona casi no sube la voz al preguntar
    assert profile.intonation(question) == "question"  # con el suyo, sí


def test_whisper_echoes_and_copies_are_undone():
    from bubble.voice.asr import collapse_echo, copied_from

    assert collapse_echo("Hola Hola Hola") == "Hola"
    assert collapse_echo("Dale. Dale. Dale.") == "Dale."
    assert collapse_echo("comandas, comandas, comandas") == "comandas"
    assert collapse_echo("Como andas? Como andas?") == "Como andas?"
    assert collapse_echo("no no no wait for me") == "no no no wait for me"  # hay más: no se toca
    example = "¿Vamos a la torre? ¡Dale, esperame! ¿Alguien viene conmigo a farmear?"
    assert copied_from("¿Alguien viene conmigo a farmear?", example)
    assert not copied_from("Hola, ¿cómo andás?", example)
    assert not copied_from("vamos a la torre con los pibes que hay un boss nuevo", example)


def test_only_clean_things_are_learned_and_old_junk_is_removed(tmp_path):
    import json

    from bubble.voice.profile import looks_clean

    assert looks_clean("dale, esperame en la torre") and looks_clean("che, vamos con aro y maico")
    for junk in ("Hola Hola Hola", "comandas, comandas, comandas", "Como andas? Como andas?",
                 "yonna kiona giona giona giona kiona giona?"):
        assert not looks_clean(junk), junk
    path = tmp_path / "perfil.json"
    path.write_text(json.dumps({  # así quedaba un perfil con la versión anterior
        "phrases": {"es": ["Hola, ¿cómo te va?", "Hola Hola Hola", "comandas, comandas, comandas"]},
        "saved": {"en|hola hola hola": "Hey hey, hey", "en|dale esperame": "wait for me",
                  "en|yonna kiona giona giona giona kiona giona?": "Yonna, kiona"},
        "words": {"es": ["que", "ese", "tradear", "lauti", "nos"]},
        "calibration": {"question_rise": -1.19},
    }), encoding="utf-8")
    profile = VoiceProfile(path)
    assert profile.data["phrases"]["es"] == ["Hola, ¿cómo te va?"]
    assert profile.data["saved"] == {"en|dale esperame": "wait for me"}
    assert profile.vocabulary("es") == ("tradear", "lauti")  # las comunes no hacen falta
    assert profile.data["calibration"]["question_rise"] == 0.5  # una pregunta tiene que subir
    assert profile.hint("es") and "lauti" not in profile.hint("es")  # a Whisper, solo el ejemplo fijo
    profile.remember("Hola Hola Hola", "en", "Hey hey hey")
    assert profile.saved("hola hola hola", "en") is None
