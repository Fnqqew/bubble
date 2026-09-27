import numpy as np

from bubble.voice import audio as audio_io
from bubble.voice.pipelines import VoiceListener, VoiceSpeaker
from bubble.voice.segmenter import SAMPLE_RATE, SpeechSegmenter
from bubble.voice.stt import Transcript, is_hallucination, pick_model
from bubble.voice.tts import Speech

RNG = np.random.default_rng(3)


def noise(seconds: float, level: float = 0.002) -> np.ndarray:
    return (RNG.standard_normal(int(seconds * SAMPLE_RATE)) * level).astype(np.float32)


def voice_like(seconds: float) -> np.ndarray:
    """Algo con la forma de una frase: tono con sílabas (energía que sube y baja rápido)."""
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    syllables = 0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)
    return (0.2 * syllables * np.sin(2 * np.pi * 180 * t)).astype(np.float32) + noise(seconds)


def feed_in_blocks(segmenter: SpeechSegmenter, stream: np.ndarray) -> list[np.ndarray]:
    phrases = []
    for start in range(0, len(stream), 480):
        phrases += segmenter.feed(stream[start:start + 480])
    return phrases


def test_segmenter_cuts_phrases_at_pauses():
    stream = np.concatenate([noise(1.0), voice_like(1.6), noise(1.0), voice_like(2.2), noise(1.2)])
    phrases = feed_in_blocks(SpeechSegmenter(), stream)
    assert len(phrases) == 2
    assert 1.4 <= len(phrases[0]) / SAMPLE_RATE <= 2.3  # la frase entera, con un poco de antes y sin el silencio
    assert 2.0 <= len(phrases[1]) / SAMPLE_RATE <= 2.9


def test_segmenter_ignores_steady_background_and_short_clicks():
    music = (0.02 * np.sin(2 * np.pi * 220 * np.arange(5 * SAMPLE_RATE) / SAMPLE_RATE)).astype(np.float32)
    click = np.concatenate([noise(0.5), 0.3 * np.ones(int(0.06 * SAMPLE_RATE), dtype=np.float32), noise(1.0)])
    segmenter = SpeechSegmenter()
    assert feed_in_blocks(segmenter, np.concatenate([music, click])) == []  # fondo parejo y un golpe de 60 ms


def test_segmenter_splits_very_long_speech():
    phrases = feed_in_blocks(SpeechSegmenter(max_seconds=5), np.concatenate([voice_like(12.0), noise(1.0)]))
    assert len(phrases) >= 2 and all(len(p) <= 5 * SAMPLE_RATE + 480 for p in phrases)


def test_whisper_inventions_are_dropped():
    assert is_hallucination("Thank you for watching!")
    assert is_hallucination("Subtítulos realizados por la comunidad de Amara.org")
    assert not is_hallucination("thank you bro, that was sick")
    assert pick_model(12) == "base" and pick_model(24) == "small"


class FakeTranscriber:
    def __init__(self, text="hola a todos"):
        self.text = text
        self.calls = []

    def transcribe(self, audio, language=None):
        self.calls.append(language)
        return Transcript(self.text, language or "en", 0.99, len(audio) / SAMPLE_RATE) if self.text else None


def test_listener_turns_phrases_into_transcripts_and_mutes_itself():
    heard = []
    listener = VoiceListener(FakeTranscriber("anyone wanna trade?"), heard.append)
    segmenter = SpeechSegmenter()
    listener.feed(np.concatenate([noise(0.8), voice_like(1.5), noise(1.0)]), segmenter)
    assert listener._phrases.qsize() == 1
    listener.muted_until = 1e18  # suena tu voz traducida por los parlantes: no se escucha como si fuera de otro
    listener.feed(np.concatenate([noise(0.8), voice_like(1.5), noise(1.0)]), SpeechSegmenter())
    assert listener._phrases.qsize() == 1


def test_speaker_says_the_translation_in_the_other_language(monkeypatch):
    played, events = [], []
    monkeypatch.setattr(audio_io, "play", lambda output, audio, rate: played.append((len(audio), rate)))

    class FakeVoices:
        def synthesize(self, text, language):
            return Speech(np.zeros(22050, dtype=np.float32), 22050) if language == "en" else None

    transcriber = FakeTranscriber("hola a todos, alguien quiere cambiar mascotas?")
    speaker = VoiceSpeaker(transcriber, FakeVoices(), lambda text: ("hey everyone, anyone wanna trade pets?", "en"),
                           push_to_talk_vk=0x06, my_language="es-AR",
                           on_event=lambda kind, text: events.append((kind, text)))
    speaker.speak(voice_like(2.0))
    assert transcriber.calls == ["es-AR"]  # tu voz: se le dice a Whisper en qué idioma hablás
    assert [kind for kind, _ in events] == ["entendi", "traduccion"]
    assert played == [(22050, 22050)]
