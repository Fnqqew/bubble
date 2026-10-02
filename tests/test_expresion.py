"""Bubble 3.7: la voz traducida suena pareja de una frase a la otra (mismo tono y volumen) y conserva la expresión del
jugador, en todos los idiomas con voz femenina y masculina; la nube reconoce los idiomas que no mezcla; se incorporan
las palabras de Roblox; y la ventana ya no se bloquea. Sin conectarse de verdad.
"""

import math
import threading
import time

import numpy as np
import pytest

from bubble.voice import prosody

RATE = 22050


def voice(pitch: float = 200.0, seconds: float = 1.2, level: float = 0.3, rise: float = 0.0, pauses: bool = False,
          rate: int = RATE) -> np.ndarray:
    """Voz sintética: armónicos con algo de melodía y, opcionalmente, pausas entre "palabras"."""
    t = np.arange(int(rate * seconds)) / rate
    melody = pitch * (1 + 0.02 * np.sin(2 * np.pi * 3 * t)) * 2 ** (rise * np.clip(t / seconds - 0.75, 0, 1) * 4 / 12)
    phase = 2 * np.pi * np.cumsum(melody) / rate
    audio = sum(np.sin(k * phase) / k for k in range(1, 12))
    if pauses:
        audio *= (np.sin(2 * np.pi * 1.5 * t) > -0.6)  # "palabras" separadas por silencios cortos
    return (level * audio / np.max(np.abs(audio))).astype(np.float32)


def semitones(a: float, b: float) -> float:
    return 12 * math.log2(a / b)


# ---------------------------------------------------------------- mismo tono y volumen entre frases
def test_every_sentence_ends_up_at_the_same_volume():
    polish = prosody.Polish()
    quiet = polish.apply(voice(level=0.08), RATE, "nube")  # hasta 14 dB más; subir más amplificaría ruido
    loud = polish.apply(voice(level=0.9), RATE, "nube")
    assert prosody.loudness_db(quiet, RATE) == pytest.approx(prosody.TARGET_DB, abs=1)  # (al oído)
    assert prosody.loudness_db(loud, RATE) == pytest.approx(prosody.TARGET_DB, abs=1)
    assert np.max(np.abs(loud)) <= 0.97  # sin saturación


def test_a_sentence_that_came_out_higher_is_brought_back_to_the_voice_pitch():
    """La nube varía el tono entre frases: la misma voz salía a 100 Hz en una y a 250 Hz en la siguiente."""
    polish = prosody.Polish()
    for _ in range(3):
        polish.apply(voice(200), RATE, "una-voz")  # la voz habitual
    odd = polish.apply(voice(250), RATE, "una-voz")  # 3,9 semitonos más aguda
    # casi por completo; igualarla del todo sonaría plana
    assert abs(semitones(prosody.median_pitch(odd, RATE), 200)) < 1.0
    other_voice = polish.apply(voice(250), RATE, "otra-voz")  # cada voz tiene su tono; esta no se modifica
    assert abs(semitones(prosody.median_pitch(other_voice, RATE), 250)) < 0.3
    far = polish.apply(voice(500), RATE, "una-voz")  # a más de una octava: es un error de medición, no se modifica
    assert abs(semitones(prosody.median_pitch(far, RATE), 500)) < 0.5


def test_cloud_voices_are_evened_out_from_their_first_sentence():
    """Cada voz de la nube trae su tono medido de antemano, de modo que la primera frase ya se empareja."""
    import bubble.cloud.speak  # noqa: F401 - (carga los tonos de las voces de la nube)

    first = prosody.POLISH.apply(voice(240), RATE, "aura-2-apollo-en")  # Apollo: 141 Hz habitual
    assert semitones(prosody.median_pitch(first, RATE), 240) < -6  # bajó casi hasta su tono


def test_changing_the_pitch_keeps_the_length_and_the_timbre():
    audio = voice(180)
    for shift in (-3, 3):
        moved = prosody.psola(audio, RATE, 2 ** (shift / 12))
        assert len(moved) == len(audio)
        assert semitones(prosody.median_pitch(moved, RATE), 180) == pytest.approx(shift, abs=0.35)


# ---------------------------------------------------------------- tu expresión
def test_how_you_said_it_changes_how_it_sounds():
    polish = prosody.Polish()
    calm = polish.apply(voice(200), RATE, "", "")
    shout = polish.apply(voice(200), RATE, "", "shout")
    soft = polish.apply(voice(200), RATE, "", "soft")
    assert semitones(prosody.median_pitch(shout, RATE), prosody.median_pitch(calm, RATE)) > 1.5  # más aguda
    assert prosody.level_db(shout, RATE) > prosody.level_db(calm, RATE) + 3  # y más fuerte
    assert prosody.level_db(soft, RATE) < prosody.level_db(calm, RATE) - 3  # baja, más suave


def test_a_question_rises_at_the_end_even_with_voices_that_do_not():
    flat = voice(200, seconds=1.5)
    asked = prosody.Polish().apply(flat, RATE, "", "question")
    f0, voiced = prosody.pitch_track(asked, RATE)
    tail, body = f0[voiced][-12:], f0[voiced][:-30]
    assert semitones(np.median(tail), np.median(body)) > 1.5
    already = voice(200, seconds=1.5, rise=4)  # ya sube (voz de la nube con "?"): no se corrige de más
    f0, voiced = prosody.pitch_track(prosody.Polish().apply(already, RATE, "", "question"), RATE)
    assert semitones(np.median(f0[voiced][-12:]), np.median(f0[voiced][:-30])) < 5.5


def test_the_cloud_voice_is_evened_out_while_it_arrives():
    polish = prosody.Polish()
    polish.apply(voice(200, rate=24000), 24000, "nube")
    polish.apply(voice(200, rate=24000), 24000, "nube")
    arriving = voice(240, seconds=2.0, pauses=True, rate=24000, level=0.12)  # (~-25 dB, como la nube)
    shaper = prosody.Streaming(polish, 24000, "nube", "")
    out = []
    for start in range(0, len(arriving), 2400):  # de a 0,1 s, como llega de la nube
        out += shaper.feed(arriving[start:start + 2400])
    out += shaper.finish()
    joined = np.concatenate(out)
    assert len(joined) == len(arriving)  # no se pierde nada
    assert len(out) >= 2  # y se entrega por tramos, sin esperar la frase entera
    assert abs(semitones(prosody.median_pitch(joined, 24000), 200)) < 0.8
    assert prosody.loudness_db(joined, 24000) == pytest.approx(prosody.TARGET_DB, abs=1.5)


def test_a_voice_without_pauses_still_starts_before_the_end():
    shaper = prosody.Streaming(prosody.Polish(), 24000, "", "")
    out = []
    joined_voice = voice(200, seconds=2.0, rate=24000)  # continua, sin ningún silencio
    for start in range(0, len(joined_voice), 2400):
        out.append(len(shaper.feed(joined_voice[start:start + 2400])))
    assert sum(out) >= 1  # salió audio antes del final
    assert sum(len(piece) for piece in shaper.finish()) < len(joined_voice)


def test_written_signs_count_as_how_you_said_it():
    from bubble.voice.pipelines import written_style

    assert written_style("¿venís?") == "question" and written_style("vamos!!") == "exclaim"
    assert written_style("dale") == ""


def test_the_cloud_ends_single_words_with_your_sign():
    from bubble.cloud.speak import speakable

    assert speakable("nice") == "nice." and speakable("nice", "shout") == "nice!"
    assert speakable("vamos", "question+exclaim") == "vamos?"


def test_direct_mode_passes_your_expression_to_the_voice():
    """En el modo directo la expresión se perdía: la voz sonaba igual aunque el jugador gritara."""
    from bubble.voice.live import Caption
    from bubble.voice.pipelines import DirectVoice

    said, done = [], threading.Event()

    class Out:
        listeners = []
        output = type("O", (), {"is_cable": False})()
        hear_myself = False

        def warm_up(self, language):
            pass

        def say(self, text, language, on_ready=None, style=""):
            said.append((text, style))
            done.set()
            return True

    class Listener:
        on_caption = None
        muted_until = 0.0

        def start(self):
            pass

        def stop(self):
            pass

    direct = DirectVoice(None, Out(), lambda text, how="": (text.upper(), "en"), "es-AR", listener=Listener())
    direct.start()
    try:
        direct._caption(Caption(1, 0.0, 1.0, "vamos ya", "es", final=True, intonation="shout"))
        assert done.wait(3)
    finally:
        direct.stop()
    assert said == [("VAMOS YA", "shout")]


def test_the_translation_is_said_in_two_parts_at_most():
    """Cada pedido a la voz de la nube sale con otro tono, de modo que oración por oración parecía cambiar de persona.
    """
    from bubble.voice.pipelines import Sentences

    parts = []
    sentences = Sentences(parts.append)
    for chunk in ("Wait for me at the tower. ", "I'm coming right now. ", "Don't start without me. ", "Ok?"):
        sentences.add(chunk)
    assert parts == ["Wait for me at the tower."]  # la primera, apenas está
    sentences.finish("Wait for me at the tower. I'm coming right now. Don't start without me. Ok?")
    assert parts[1:] == ["I'm coming right now. Don't start without me. Ok?"]  # el resto, junto


# ---------------------------------------------------------------- la nube reconoce más idiomas
def _listener(transcribe):
    from bubble.cloud import deepgram

    captions, done = [], threading.Event()
    listener = deepgram.DeepgramListener("clave", lambda c: (captions.append(c), done.set()), source_factory=None,
                                         diarize=False, recheck=True)
    listener._sent = (np.random.default_rng(1).standard_normal(32000) * 0.1).astype(np.float32)
    calls = []

    def fake(self, audio, language=None, **kwargs):
        calls.append(kwargs)
        return transcribe()

    return listener, captions, done, calls, fake


def _say(listener, text, language, confidence):
    words = [{"word": w, "confidence": confidence, "language": language, "start": 0.0, "end": 2.0}
             for w in text.split()]
    listener.handle({"type": "Results", "is_final": True, "speech_final": True, "start": 0.0, "duration": 2.0,
                     "channel": {"alternatives": [{"transcript": text, "words": words}]}})


def test_a_language_the_cloud_mixes_badly_is_heard_again_with_its_language(monkeypatch):
    """Al mezclar idiomas, la nube escribía el coreano en japonés, el polaco en ruso, el vietnamita en hindi, etc."""
    from bubble.cloud import deepgram
    from bubble.voice.asr import Heard

    listener, captions, done, calls, fake = _listener(
        lambda: Heard("탑에서 기다려 지금 갈게", "ko", 1.0, 0.0, math.log(0.97), 2.0, 0.3))
    monkeypatch.setattr(deepgram.DeepgramClip, "transcribe", fake)
    _say(listener, "たべーざーきだりょう", "ja", 0.29)
    assert done.wait(3)
    assert calls == [{"detect": True}]
    assert captions[-1].text == "탑에서 기다려 지금 갈게" and captions[-1].language == "ko" and captions[-1].final


def test_a_clear_phrase_is_not_heard_twice(monkeypatch):
    from bubble.cloud import deepgram

    listener, captions, _done, calls, fake = _listener(lambda: None)
    monkeypatch.setattr(deepgram.DeepgramClip, "transcribe", fake)
    _say(listener, "wait for me at the tower", "en", 0.98)
    assert calls == [] and captions[-1].language == "en" and captions[-1].final  # no se paga dos veces
    mine = deepgram.DeepgramListener("clave", lambda c: None, source_factory=None, language="es", recheck=True)
    assert not mine.recheck  # voz del jugador: su idioma ya se conoce


def test_roblox_words_are_prioritized_for_the_cloud():
    from bubble.cloud.deepgram import GAME_TERMS, fit_keyterms

    chosen = fit_keyterms(GAME_TERMS)
    assert len(chosen) == len(GAME_TERMS)  # entran todas
    for term in ("Robux", "obby", "Bubble Gum Simulator", "Blox Fruits", "gamepass"):
        assert term in chosen


# ---------------------------------------------------------------- la ventana no se traba
def test_looking_for_roblox_does_not_walk_every_window_each_time(monkeypatch):
    """Se consulta muchas veces por segundo; con otro hilo ocupado, recorrer las ~250 ventanas tardaba 1 s."""
    from bubble import win32

    searches = []
    monkeypatch.setattr(win32, "_search_roblox_window", lambda: searches.append(1) or 1234)
    monkeypatch.setattr(win32, "_window", [None, -1e9])
    monkeypatch.setattr(win32.user32, "IsWindowVisible", lambda hwnd: True)
    monkeypatch.setattr(win32.user32, "IsIconic", lambda hwnd: False)
    assert win32.find_roblox_window() == 1234
    for _ in range(20):
        assert win32.find_roblox_window() == 1234
    assert len(searches) == 1
    monkeypatch.setattr(win32, "_window", [1234, time.monotonic() - win32.WINDOW_KEEP_S - 1])
    win32.find_roblox_window()
    assert len(searches) == 2  # se vuelve a buscar cada cierto tiempo


def test_the_heavy_parts_load_while_the_splash_is_showing(monkeypatch):
    """Cargarlas con la ventana abierta la congelaba (la conexión con Claude, 2 s)."""
    import importlib

    from bubble.ui import launch

    loaded = []
    monkeypatch.setattr(importlib, "import_module", lambda name: loaded.append(name))
    launch.preload()
    assert "claude_agent_sdk" in loaded and "faster_whisper" in loaded
    assert loaded.index("bubble.voice") < loaded.index("faster_whisper")  # (le da el módulo vacío en lugar de PyAV)


def test_tab_asks_for_the_new_language_right_away_and_the_next_one_too(monkeypatch, tmp_path):
    """Con Tab, la traducción esperaba lo mismo que al escribir (650 ms) antes de pedirse."""
    import tkinter as tk

    monkeypatch.setenv("APPDATA", str(tmp_path))
    from bubble.ui.overlays import ComposeBar

    root = tk.Tk()
    root.withdraw()
    asked = []
    # sin mostrarla ni sacar al jugador del juego
    monkeypatch.setattr(ComposeBar, "visible", property(lambda self: True))
    try:
        bar = ComposeBar(root, lambda *key: asked.append(key), lambda *a, **k: None, lambda: None)
        bar.targets, bar.index, bar.tone = ["en", "pt", "fr"], 0, 3
        bar.entry.insert(0, "vamos a farmear")
        bar._next_target()
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline and not asked:
            root.update()
        assert asked == [("vamos a farmear", "pt", 3)]  # de inmediato (650 ms con la espera de escritura)
        bar.show_preview(("vamos a farmear", "pt", 3), "let's go farm", "done")
        assert asked[-1] == ("vamos a farmear", "fr", 3)  # al recorrer con Tab, el siguiente ya se pide
    finally:
        root.destroy()
