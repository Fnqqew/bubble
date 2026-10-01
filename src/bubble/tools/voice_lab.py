"""Laboratorio de voz: conversaciones de prueba (voces sintéticas en varios idiomas) que se pasan en tiempo real por los
subtítulos de voz de Bubble, para medir la velocidad y la calidad de la comprensión y la traducción.

No se reproduce sonido: el audio entra directamente al sistema de escucha, al mismo ritmo que si viniera del juego.

Uso:
    python -m bubble.tools.voice_lab                       # todos los escenarios, con traducción (Claude)
    python -m bubble.tools.voice_lab grupo idiomas         # algunos
    python -m bubble.tools.voice_lab --sin-claude          # solo escuchar y transcribir (gratis)
    python -m bubble.tools.voice_lab --sin-vivo            # sin traducción en vivo (solo al terminar cada frase)
    python -m bubble.tools.voice_lab --pro                 # voces reconocidas en la nube (Deepgram, unos centavos)
    python -m bubble.tools.voice_lab --directo             # voz propia, traducción directa (se habla y sale en voz)

Para cada escenario informa: tiempo hasta que aparece el texto, tiempo hasta que se ve una traducción (en vivo o final,
desde que la persona empieza a hablar), tiempo hasta la traducción definitiva, palabras mal entendidas (WER), acierto
de idioma, identificación de hablantes (voces encontradas y aciertos) y uso de CPU. Guarda capturas de los subtítulos
en %LOCALAPPDATA%\\Bubble\\voice_lab.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..voice.audio import resample
from ..voice.models import models_dir

RATE = 16000


# ---------------------------------------------------------------- escenarios
@dataclass(frozen=True)
class Speaker:
    voice: str  # modelo de Piper
    id: int | None = None  # hablante dentro del modelo (si tiene varios)
    gain_db: float = 0.0


@dataclass(frozen=True)
class Turn:
    who: str
    lang: str
    text: str
    gap: float = 0.8  # silencio previo (negativo: se superpone con la frase anterior)
    speed: float = 1.0  # >1: más rápido


@dataclass
class Scenario:
    name: str
    about: str
    cast: dict[str, Speaker]
    turns: list[Turn]
    noise_db: float | None = None  # música y ruidos del juego, relativo a la voz (None: sin ruido)


CAST = {
    "Jake": Speaker("en_US-libritts_r-medium", 77),
    "Mia": Speaker("en_US-libritts_r-medium", 400),
    "Leo": Speaker("en_US-libritts_r-medium", 0),
    "Olivia": Speaker("en_GB-vctk-medium", 3, -3),
    "Sam": Speaker("en_GB-vctk-medium", 40),
    "Pedro": Speaker("pt_BR-faber-medium"),
    "Lucas": Speaker("pt_BR-cadu-medium", None, -2),
    "Chloé": Speaker("fr_FR-siwis-medium"),
    "Jonas": Speaker("de_DE-thorsten-medium"),
    "Dima": Speaker("ru_RU-dmitri-medium"),
    "Rohan": Speaker("hi_IN-rohan-medium"),
    "Ana": Speaker("es_MX-ald-medium"),
}



RAW = False  # --sin-filtro: sin radio de escucha ni filtro de ruido (comparación)
LIVE = True  # --sin-vivo: sin traducción en vivo (comparación)
PRO = False  # --pro: las voces se reconocen en la nube (Deepgram, con la clave guardada; cuesta unos centavos)

def scenarios() -> list[Scenario]:
    return [
        Scenario("una_persona", "Una persona en inglés, a ritmo normal", CAST, [
            Turn("Jake", "en", "hey guys, does anyone know where the secret door is?", 0.5),
            Turn("Jake", "en", "i think it's behind the waterfall, but i'm not sure.", 1.4),
            Turn("Jake", "en", "can someone help me get past this jump please?", 1.2),
            Turn("Jake", "en", "never mind, i got it. thanks anyway!", 1.5),
            Turn("Jake", "en", "ok follow me, i know a shortcut to the tower.", 1.1),
            Turn("Jake", "en", "wait, don't jump yet, there's a trap right there.", 1.3),
        ]),
        Scenario("rapido", "Frases cortas y rápidas, casi sin pausas", CAST, [
            Turn("Sam", "en", "go go go!", 0.5, 1.25),
            Turn("Sam", "en", "left, left, he's behind you!", 0.3, 1.25),
            Turn("Sam", "en", "he's got the sword, run!", 0.3, 1.25),
            Turn("Sam", "en", "no way, that was so close.", 0.35, 1.25),
            Turn("Sam", "en", "who has the key?", 0.3, 1.25),
            Turn("Sam", "en", "i have it, meet me at the door.", 0.35, 1.25),
            Turn("Sam", "en", "bro, you just stole my kill.", 0.3, 1.25),
            Turn("Sam", "en", "that is not fair at all.", 0.3, 1.25),
        ]),
        Scenario("grupo", "Cuatro personas turnándose rápido, a veces pisándose", CAST, [
            Turn("Jake", "en", "alright everyone, let's split up and look for the coins.", 0.5),
            Turn("Mia", "en", "i'll take the left side.", 0.4),
            Turn("Leo", "en", "wait, where is the left side?", 0.3),
            Turn("Olivia", "en", "near the big tree, just follow the red path.", 0.5),
            Turn("Jake", "en", "has anyone found anything yet?", 0.9),
            Turn("Leo", "en", "i found three coins behind the house!", 0.4),
            Turn("Mia", "en", "nice, i only found one.", -0.4),
            Turn("Olivia", "en", "guys, the timer is almost done, hurry up!", 0.3),
            Turn("Jake", "en", "come back to the start, we're gonna win this.", 0.6),
            Turn("Leo", "en", "let's go!", 0.3),
        ]),
        Scenario("idiomas", "Varios idiomas (y uno en el tuyo, que no se subtitula)", CAST, [
            Turn("Pedro", "pt", "galera, alguém quer trocar pets comigo?", 0.5),
            Turn("Chloé", "fr", "attendez-moi à la tour, j'arrive tout de suite.", 0.8),
            Turn("Jonas", "de", "weiß jemand, wo die geheime Tür ist?", 0.7),
            Turn("Dima", "ru", "ребята, помогите мне пройти этот уровень.", 0.8),
            Turn("Rohan", "hi", "भाई, तुम कहाँ हो? मैं तुम्हें ढूंढ रहा हूँ।", 0.8),
            Turn("Mia", "en", "does anyone want to join my team?", 0.7),
            Turn("Ana", "es", "yo me sumo, ¿dónde están?", 0.6),
            Turn("Lucas", "pt", "bora pro lobby, esse servidor tá bugado.", 0.7),
            Turn("Chloé", "fr", "c'était trop bien, on refait une partie?", 0.8),
        ]),
        Scenario("ruido", "Dos personas con música y ruidos del juego fuertes", CAST, [
            Turn("Jake", "en", "can you hear me? the music is so loud in this game.", 0.8),
            Turn("Mia", "en", "yeah i can hear you, let's go to the arena.", 0.7),
            Turn("Jake", "en", "watch out, there's someone shooting from the roof.", 0.9),
            Turn("Mia", "en", "i'm going to heal, cover me.", 0.6),
            Turn("Jake", "en", "ok, i got you, go now.", 0.7),
        ], noise_db=-9),
        Scenario("largo", "Alguien explicando algo largo casi sin pausas", CAST, [
            Turn("Leo", "en", "so basically what you need to do is collect all the gems in the first area, then go "
                 "talk to the old man near the fountain, he gives you a map, and with the map you can find the hidden "
                 "cave where the boss is, but be careful because the boss has two phases and in the second one he "
                 "gets really fast.", 0.5),
        ]),
    ]


# ---------------------------------------------------------------- audio de prueba
def _cache_dir() -> Path:
    path = models_dir().parent / "voice_lab" / "clips"
    path.mkdir(parents=True, exist_ok=True)
    return path


def synthesize(voices, speaker: Speaker, text: str, speed: float) -> np.ndarray:
    key = hashlib.sha1(f"{speaker.voice}|{speaker.id}|{speed}|{text}".encode()).hexdigest()[:16]
    path = _cache_dir() / f"{key}.npy"
    if path.exists():
        return np.load(path)
    from piper.config import SynthesisConfig

    voice = voices._load_here(speaker.voice)  # (en este proceso: se necesita la voz con varios hablantes)
    config = SynthesisConfig(speaker_id=speaker.id, length_scale=1.0 / speed)
    audio = np.concatenate([chunk.audio_float_array for chunk in voice.synthesize(text, config)])
    audio = resample(audio.astype(np.float32), voice.config.sample_rate, RATE)
    np.save(path, audio)
    return audio


def roblox_voice(audio: np.ndarray, gain_db: float) -> np.ndarray:
    """Simula la voz recibida por el chat de voz: banda de teléfono ancha y algo comprimida."""
    from scipy.signal import butter, sosfilt

    sos = butter(4, [110, 7200], btype="band", fs=RATE, output="sos")
    audio = sosfilt(sos, audio).astype(np.float32)
    audio = np.tanh(audio * 1.6) / np.tanh(1.6)
    return audio * (10 ** (gain_db / 20)) * 0.5


def game_noise(seconds: float, seed: int = 1) -> np.ndarray:
    """Música de fondo (acordes con ritmo), zumbido y golpes o explosiones ocasionales."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * RATE)) / RATE
    music = np.zeros_like(t)
    chords = [(220.0, 277.2, 329.6), (196.0, 246.9, 293.7), (174.6, 220.0, 261.6), (196.0, 246.9, 311.1)]
    beat = 0.5
    for index, start in enumerate(np.arange(0, seconds, beat * 4)):
        chord = chords[index % len(chords)]
        mask = (t >= start) & (t < start + beat * 4)
        local = t[mask] - start
        env = 0.6 + 0.4 * np.exp(-((local % beat) * 6))
        music[mask] += sum(np.sin(2 * np.pi * f * local) for f in chord) * env / 3
    kick = np.zeros_like(t)
    for start in np.arange(0, seconds, beat):
        idx = int(start * RATE)
        n = min(len(t) - idx, int(0.15 * RATE))
        local = np.arange(n) / RATE
        kick[idx:idx + n] += np.sin(2 * np.pi * (60 + 80 * np.exp(-local * 30)) * local) * np.exp(-local * 25)
    hiss = rng.normal(0, 0.15, len(t))
    booms = np.zeros_like(t)
    for start in rng.uniform(0, seconds, int(seconds / 4)):
        idx = int(start * RATE)
        n = min(len(t) - idx, int(0.6 * RATE))
        booms[idx:idx + n] += rng.normal(0, 1, n) * np.exp(-np.arange(n) / RATE * 7)
    from scipy.signal import butter, sosfilt

    booms = sosfilt(butter(2, 900, fs=RATE, output="sos"), booms)
    mix = music * 0.5 + kick * 0.6 + hiss * 0.2 + booms * 0.8
    return (mix / (np.sqrt(np.mean(mix ** 2)) + 1e-9)).astype(np.float32)


@dataclass
class Truth:
    who: str
    lang: str
    text: str
    start: float
    end: float


def build(scenario: Scenario) -> tuple[np.ndarray, list[Truth]]:
    from ..voice.tts import Voices

    voices = Voices()
    pieces, truths = [], []
    cursor = 0.0
    for turn in scenario.turns:
        speaker = scenario.cast[turn.who]
        audio = roblox_voice(synthesize(voices, speaker, turn.text, turn.speed), speaker.gain_db)
        # Piper deja silencio al principio y al final: se mide dónde está realmente la voz.
        loud = np.flatnonzero(np.abs(audio) > 0.02)
        lead = loud[0] / RATE if len(loud) else 0.0
        tail = (len(audio) - loud[-1]) / RATE if len(loud) else 0.0
        start = max(0.0, cursor + turn.gap)
        pieces.append((start, audio))
        truths.append(Truth(turn.who, turn.lang, turn.text, start + lead, start + len(audio) / RATE - tail))
        cursor = start + len(audio) / RATE - tail
    total = cursor + 5.0
    track = np.zeros(int(total * RATE), dtype=np.float32)
    for start, audio in pieces:
        idx = int(start * RATE)
        track[idx:idx + len(audio)] += audio[: len(track) - idx]
    if scenario.noise_db is not None:
        speech_rms = float(np.sqrt(np.mean(np.concatenate([a for _s, a in pieces]) ** 2)))
        track += game_noise(total) * speech_rms * (10 ** (scenario.noise_db / 20))
    return np.clip(track, -1, 1), truths


# ---------------------------------------------------------------- fuente de audio falsa, al ritmo real
class FakeSource:
    def __init__(self, track: np.ndarray) -> None:
        self.track = track
        self.started: float | None = None

    def recorder(self, samplerate: int, channels: int, blocksize: int):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def record(self, numframes: int) -> np.ndarray:
        if self.started is None:
            self.started = time.perf_counter()
            self.position = 0
        wait = self.started + (self.position + numframes) / RATE - time.perf_counter()
        if wait > 0:
            time.sleep(wait)
        block = self.track[self.position:self.position + numframes]
        if len(block) < numframes:
            block = np.concatenate([block, np.zeros(numframes - len(block), np.float32)])
        self.position += numframes
        return block

    def wall(self, seconds: float) -> float:
        return (self.started or 0.0) + seconds


# ---------------------------------------------------------------- medición
def words(text: str) -> list[str]:
    return re.sub(r"[^\w\s']", " ", text.lower()).split()


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    row = list(range(len(hyp) + 1))
    for i in range(1, len(ref) + 1):
        prev, row[0] = row[0], i
        for j in range(1, len(hyp) + 1):
            cur = min(row[j] + 1, row[j - 1] + 1, prev + (ref[i - 1] != hyp[j - 1]))
            prev, row[j] = row[j], cur
    return row[len(hyp)] / max(1, len(ref))


@dataclass
class Record:
    id: int
    start: float
    end: float
    first_seen: float  # reloj (perf_counter)
    final_at: float | None = None
    text: str = ""
    language: str = ""
    speaker: int = 0
    partials: int = 0
    translation_asked: float | None = None
    asks: int = 0
    translation_first: float | None = None
    translation_done: float | None = None
    translation: str = ""
    live_asks: int = 0
    shown_first: float | None = None  # primera traducción a la vista (en vivo o final)
    definitive_at: float | None = None  # traducción definitiva (pedida al final o la última en vivo)


@dataclass
class Result:
    name: str
    about: str
    lines: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def percentile(values: list[float], q: float) -> float | None:
    return float(np.percentile(values, q)) if values else None


def fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}s"


def run(scenario: Scenario, pipeline, translate_fn, my_language: str, shots_dir: Path) -> Result:
    from ..voice.captions import CaptionBoard
    from ..voice.live import LiveListener

    track, truths = build(scenario)
    source = FakeSource(track)
    records: dict[int, Record] = {}
    lock = threading.Lock()

    def translate(text, language, speaker, on_piece, on_done, intonation="", live=False, *, _records=records):
        with lock:
            # (las traducciones en vivo pueden pedirse para una frase que no es la última que llegó)
            caption_id = next((cid for cid, r in _records.items() if r.text == text), current_caption[0])
            rec = _records.get(caption_id)
            if live:
                if rec:
                    rec.live_asks += 1
            elif rec:
                rec.translation_asked = time.perf_counter()
                rec.translation_first = rec.translation_done = None
                rec.asks += 1
            ask_number = rec.asks if rec else 0

        def shown():
            rec = _records.get(caption_id)
            if rec and rec.shown_first is None:
                rec.shown_first = time.perf_counter()

        if live:
            def live_done(result, native=False):
                if result and not native:
                    with lock:
                        shown()
                on_done(result, native=native)

            translate_fn(text, language, speaker, lambda _piece: None, live_done, "", live=True)
            return

        def piece(chunk):
            with lock:
                rec = _records.get(caption_id)
                if rec and rec.asks == ask_number and rec.translation_first is None:
                    rec.translation_first = time.perf_counter()
                shown()
            on_piece(chunk)

        def done(result, native=False):
            with lock:
                rec = _records.get(caption_id)
                if rec and rec.asks == ask_number:
                    rec.translation_done = time.perf_counter()
                    rec.translation = result or ""
                if result:
                    shown()
            on_done(result, native=native)

        translate_fn(text, language, speaker, piece, done, intonation)

    def definitive(line):
        with lock:
            rec = records.get(line.id)
            if rec and rec.definitive_at is None:
                rec.definitive_at = time.perf_counter()

    board = CaptionBoard(my_language, translate, live=LIVE, on_translated=definitive)
    current_caption = [0]

    def on_caption(caption):
        now = time.perf_counter()
        with lock:
            rec = records.get(caption.id)
            if rec is None:
                rec = records[caption.id] = Record(caption.id, caption.start, caption.end, now)
            rec.start, rec.end = caption.start, caption.end
            rec.text, rec.language, rec.speaker = caption.text, caption.language, caption.speaker
            if caption.final:
                rec.final_at = now
            else:
                rec.partials += 1
        current_caption[0] = caption.id
        board.caption(caption)

    final_asr, partial_asr, speakers = pipeline
    speakers.voices.clear()
    from ..voice.hearing import Earshot

    if PRO:
        from ..cloud.deepgram import GAME_HOLD_S, GAME_TAIL_S, GAME_TERMS, DeepgramListener
        from ..cloud.keys import load_key

        listener = DeepgramListener(load_key(), on_caption, source_factory=lambda: source,
                                    earshot=None if RAW else Earshot("normal"), noise_filter=not RAW,
                                    speakers=speakers, keyterms=list(GAME_TERMS), recheck=True,
                                    hold_s=GAME_HOLD_S, tail_s=GAME_TAIL_S)
    else:
        listener = LiveListener(final_asr, on_caption, partial_asr=partial_asr, speakers=speakers,
                                source_factory=lambda: source, earshot=None if RAW else Earshot("normal"),
                                noise_filter=not RAW)
    cpu0 = time.process_time()
    wall0 = time.perf_counter()
    listener.start()
    duration = len(track) / RATE
    shots, shot_times = [], sorted({t.end + 2.2 for t in truths})
    while time.perf_counter() - wall0 < duration + 1.0:
        if shot_times and source.started and time.perf_counter() >= source.wall(shot_times[0]):
            shot_times.pop(0)
            lines = board.visible()
            if lines:
                shots.append(lines)
        time.sleep(0.05)
    time.sleep(3.0)  # esperar las últimas traducciones
    listener.stop()
    cpu = (time.process_time() - cpu0) / (time.perf_counter() - wall0)

    # ---- métricas
    result = Result(scenario.name, scenario.about)
    finals = sorted((r for r in records.values() if r.final_at and r.text), key=lambda r: r.start)
    mine = my_language.split("-")[0]
    expected = [t for t in truths if t.lang != mine]

    def overlap(r: Record, t: Truth) -> float:
        return max(0.0, min(r.end, t.end) - max(r.start, t.start))

    def related(r: Record, t: Truth) -> bool:
        return overlap(r, t) > min(0.25 * (t.end - t.start), 0.5 * (r.end - r.start))

    first_text, final_lat, tr_first, tr_done, visible, before_end, long_ones = [], [], [], [], [], 0, 0
    for truth in truths:
        matches = [r for r in records.values() if related(r, truth)]
        if not matches:
            continue
        starting = [r for r in matches if r.start >= truth.start - 0.5]  # frases que comienzan con esta
        if starting:
            first_text.append(min(r.first_seen for r in starting) - source.wall(truth.start))
        if truth.lang != mine:
            seen = [r.shown_first for r in matches if r.shown_first]
            if seen:
                visible.append(min(seen) - source.wall(truth.start))
            if truth.end - truth.start >= 2.5:
                long_ones += 1
                before_end += bool(seen) and min(seen) < source.wall(truth.end)
        closing = [r for r in matches if r.final_at and r.end >= truth.end - 0.3]
        if closing:
            rec = min(closing, key=lambda r: r.final_at)
            final_lat.append(rec.final_at - source.wall(truth.end))
            if truth.lang != mine and rec.translation_first:
                tr_first.append(rec.translation_first - source.wall(truth.end))
            if truth.lang != mine and rec.definitive_at:
                tr_done.append(rec.definitive_at - source.wall(truth.end))
    detected = sum(1 for t in truths if any(related(r, t) for r in finals))
    reference = " ".join(t.text for t in truths)
    hypothesis = " ".join(r.text for r in finals)
    total_wer = wer(reference, hypothesis)
    # idioma y voz de cada frase final: los de la frase real con la que más se superpone
    lang_ok, pairs = 0, []
    for rec in finals:
        truth = max(truths, key=lambda t: overlap(rec, t))
        lang_ok += rec.language == truth.lang
        pairs.append((truth.who, rec.speaker))
    real = {who for who, _n in pairs}
    found = {n for _w, n in pairs if n}
    # acierto de voz: cada voz encontrada se asigna a la persona que más habla; se cuentan las frases bien atribuidas
    by_number: dict[int, list[str]] = {}
    for who, number in pairs:
        by_number.setdefault(number, []).append(who)
    right = sum(max(whos.count(w) for w in set(whos)) for number, whos in by_number.items() if number)
    speaker_ok = right / max(1, len(pairs))
    translated = sum(1 for r in finals if r.translation or r.definitive_at)
    claude_first = [r.translation_first - r.translation_asked for r in finals
                    if r.translation_first and r.translation_asked]
    claude_done = [r.translation_done - r.translation_asked for r in finals
                   if r.translation_done and r.translation_asked and r.translation]
    false_alarms = sum(1 for r in finals if not any(overlap(r, t) > 0 for t in truths))

    result.metrics = {
        "frases": len(truths), "detectadas": detected, "texto_aparece_p50": percentile(first_text, 50),
        "final_p50": percentile(final_lat, 50), "final_p90": percentile(final_lat, 90),
        "traduccion_1a_p50": percentile(tr_first, 50), "traduccion_1a_p90": percentile(tr_first, 90),
        "traduccion_completa_p50": percentile(tr_done, 50), "wer": total_wer,
        "idioma_ok": f"{lang_ok}/{len(finals)}", "voces_reales": len(real), "voces_encontradas": len(found),
        "voz_ok": speaker_ok, "traducidas": f"{translated}/{len(expected)}", "falsas": false_alarms,
        "cpu_nucleos": cpu, "claude_1a_p50": percentile(claude_first, 50),
        "claude_completa_p50": percentile(claude_done, 50),
        "traduccion_visible_p50": percentile(visible, 50), "traduccion_visible_p90": percentile(visible, 90),
        "antes_de_terminar": f"{before_end}/{long_ones}",
        "pedidos_en_vivo": sum(r.live_asks for r in records.values()),
    }
    result.lines.append(f"  frases {detected}/{len(truths)} detectadas · texto aparece p50 {fmt(percentile(first_text, 50))}"
                        f" (desde que empieza a hablar)")
    result.lines.append(f"  texto final {fmt(percentile(final_lat, 50))} p50 / {fmt(percentile(final_lat, 90))} p90 · "
                        f"traducción: 1ª palabra {fmt(percentile(tr_first, 50))} p50 / {fmt(percentile(tr_first, 90))}"
                        f" p90, completa {fmt(percentile(tr_done, 50))} (desde que termina de hablar)")
    result.lines.append(f"  palabras mal entendidas {total_wer:.1%} · idioma {lang_ok}/{len(finals)} · voces "
                        f"{len(found)} encontradas de {len(real)} · atribución {speaker_ok:.0%} · traducidas "
                        f"{translated}/{len(expected)} · falsas {false_alarms} · CPU {cpu:.2f} núcleos · "
                        f"pedidos a Claude {sum(r.asks for r in records.values())}")
    result.lines.append(f"  traducción a la vista {fmt(percentile(visible, 50))} p50 / "
                        f"{fmt(percentile(visible, 90))} p90 (desde que empieza a hablar) · ya visible antes de que termine {before_end}/{long_ones} "
                        f"frases largas · pedidos en vivo {sum(r.live_asks for r in records.values())}")
    if claude_first:
        result.lines.append(f"  Claude (desde que se le pide): 1ª palabra {fmt(percentile(claude_first, 50))} p50, "
                            f"completa {fmt(percentile(claude_done, 50))} p50")
    for rec in finals:
        truth = max(truths, key=lambda t: overlap(rec, t))
        mark = "" if wer(truth.text, rec.text) < 0.25 else "  ✗"
        result.lines.append(f"    [{truth.who}→Voz {rec.speaker} {rec.language}] {rec.text}{mark}")
        if rec.translation:
            result.lines.append(f"        → {rec.translation}")
    _save_shots(shots, shots_dir / scenario.name)
    return result


def _save_shots(shots, prefix: Path) -> None:
    from PIL import Image

    from ..ui.subtitles import render_subtitles

    prefix.parent.mkdir(parents=True, exist_ok=True)
    for old in prefix.parent.glob(f"{prefix.name}_vista_*.png"):
        old.unlink()
    for index, lines in enumerate(shots[:8]):
        card = render_subtitles(lines)
        canvas = Image.new("RGBA", (960, card.height + 80), (58, 96, 70, 255))
        canvas.alpha_composite(card, ((960 - card.width) // 2, 40))
        canvas.convert("RGB").save(f"{prefix}_vista_{index + 1:02d}.png")


# ---------------------------------------------------------------- tu voz: traducción directa
DIRECT = [
    Turn("Ana", "es", "che, ¿alguien me ayuda con el jefe final?", 0.6),
    Turn("Ana", "es", "dale, espérame en la torre que ya voy.", 2.5),
    Turn("Ana", "es", "no, pará, hay una trampa ahí adelante.", 2.5),
    Turn("Ana", "es", "¿quién tiene la llave?", 2.5),
    Turn("Ana", "es", "buenísimo, ganamos. ¿vamos de nuevo?", 2.5),
]


class _LabOut:
    """Como VoiceOut, pero en lugar de reproducir el audio registra cuándo comenzaría a sonar la voz traducida."""

    def __init__(self, voices) -> None:
        from ..voice.audio import Output

        self.voices = voices
        self.output = Output(object(), True)
        self.hear_myself = False
        self.listeners = []

    def warm_up(self, language: str) -> None:
        self.voices.synthesize("ok", language)
        self.said: list[tuple[float, float, str, str]] = []  # (pedido, lista para sonar, texto, idioma)

    def say(self, text: str, language: str) -> bool:
        asked = time.perf_counter()
        speech = self.voices.synthesize(text, language)
        if speech is None:
            return False
        self.said.append((asked, time.perf_counter(), text, language))
        time.sleep(len(speech.audio) / speech.sample_rate)  # mientras "suena", la siguiente espera
        return True


def run_direct(pipeline, translate_out) -> list[str]:
    from ..voice.pipelines import DirectVoice
    from ..voice.tts import Voices

    scenario = Scenario("directo", "Hablás en español y sale en voz en inglés", CAST, DIRECT)
    track, truths = build(scenario)
    source = FakeSource(track)
    final, partial, _speakers = pipeline
    out = _LabOut(Voices())
    heard = []
    direct = DirectVoice(final, out, translate_out, "es", partial_asr=partial,
                         on_event=lambda kind, text: heard.append((time.perf_counter(), kind, text)),
                         mic_factory=lambda: source)
    direct.start()
    time.sleep(len(track) / RATE + 4.0)
    direct.stop()
    lines, ready, texts = [], [], [t for _w, kind, t in heard if kind == "entendi"]
    for index, (truth, (asked, synthesized, text, language)) in enumerate(zip(truths, out.said)):
        ready.append(synthesized - source.wall(truth.end))
        understood = texts[index] if index < len(texts) else "?"
        lines.append(f"    {truth.text}\n        entendió: {understood}\n        → [{language}] {text}   (suena "
                     f"{synthesized - source.wall(truth.end):.2f}s después de que terminás; la voz tardó "
                     f"{synthesized - asked:.2f}s en armarse)")
    summary = (f"  frases {len(out.said)}/{len(truths)} dichas · tu voz traducida empieza a sonar "
               f"{fmt(percentile(ready, 50))} p50 / {fmt(percentile(ready, 90))} p90 después de que terminás de hablar")
    wer_total = wer(" ".join(t.text for t in truths), " ".join(texts))
    return [summary, f"  palabras mal entendidas {wer_total:.1%}", *lines]


# ---------------------------------------------------------------- armado
def make_pipeline(final_model: str, partial_model: str, final_threads: int, partial_threads: int):
    from ..voice.asr import FastWhisper
    from ..voice.speakers import SpeakerTracker

    final = FastWhisper(final_model, final_threads)
    partial = FastWhisper(partial_model, partial_threads) if partial_model else None
    return final, partial, SpeakerTracker()


def make_translate(use_claude: bool, outgoing: bool = False):
    if not use_claude:
        return None, (lambda text, language, speaker, on_piece, on_done, *_a, **_k: on_done(None))
    from ..async_runner import AsyncRunner
    from ..config import load_config
    from ..translate import build_translator

    config = load_config()
    config.voice.subtitles = True  # (abre los mismos carriles que la ventana con los subtítulos prendidos)
    translator = build_translator(config)
    runner = AsyncRunner()
    runner.submit(translator.start()).result(timeout=90)
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and not (translator._voice_ready() and (
            translator.live_router is None or translator._live_ready())):
        time.sleep(0.2)  # se mide con los carriles rápidos ya abiertos, como durante una partida

    def translate(text, language, speaker, on_piece, on_done, intonation="", live=False):
        # Igual que en la ventana (ui/voice_panel.py): carril rápido, con la entonación y en vivo si corresponde.
        async def work():
            try:
                result = await translator.translate_incoming(text, f"Voz {speaker}", on_delta=on_piece,
                                                             from_speech=True, intonation=intonation, live=live)
                if result.status == "same_language":
                    on_done(None, native=True)
                else:
                    on_done(result.translation if result.status != "error" else None)
            except Exception:  # noqa: BLE001
                on_done(None)

        runner.submit(work())

    if outgoing:
        def translate_out(text):
            result = runner.submit(translator.translate_outgoing(text, "en")).result(timeout=25)
            return None if result.status == "error" else (result.translation, result.target_lang or "en")

        return (translator, runner), translate_out
    return (translator, runner), translate


def main() -> None:
    parser = argparse.ArgumentParser(description="Laboratorio de voz de Bubble")
    parser.add_argument("escenarios", nargs="*")
    parser.add_argument("--sin-claude", action="store_true", help="no traducir (gratis): solo escuchar y transcribir")
    parser.add_argument("--sin-vivo", action="store_true", help="sin traducción en vivo: solo al terminar cada frase")
    parser.add_argument("--pro", action="store_true", help="reconocer las voces en la nube (Deepgram)")
    parser.add_argument("--final", default="small")
    parser.add_argument("--parcial", default="base")
    parser.add_argument("--hilos-final", type=int, default=4)
    parser.add_argument("--hilos-parcial", type=int, default=2)
    parser.add_argument("--idioma", default="", help="tu idioma (por defecto, el de tu configuración)")
    parser.add_argument("--directo", action="store_true", help="probar tu voz: traducción directa a voz")
    parser.add_argument("--sin-filtro", action="store_true",
                        help="sin radio de escucha ni filtro de ruido (como antes de la 3.0)")
    args = parser.parse_args()
    global RAW, LIVE, PRO
    RAW = args.sin_filtro
    LIVE = not args.sin_vivo
    PRO = args.pro
    if args.directo:
        held, translate_out = make_translate(True, outgoing=True)
        pipeline = make_pipeline(args.final, "" if args.parcial == "no" else args.parcial, args.hilos_final,
                                 args.hilos_parcial)
        print("=== directo: hablás en español y sale en voz en inglés")
        print("\n".join(run_direct(pipeline, translate_out)))
        translator, runner = held
        runner.submit(translator.close()).result(timeout=30)
        return

    chosen = [s for s in scenarios() if not args.escenarios or s.name in args.escenarios]
    held, translate = make_translate(not args.sin_claude)
    if args.idioma:
        my_language = args.idioma
    elif held:
        my_language = held[0].my_locale[0]
    else:
        from ..config import load_config

        my_language = load_config().user.language
    pipeline = make_pipeline(args.final, "" if args.parcial == "no" else args.parcial, args.hilos_final,
                             args.hilos_parcial)
    out = models_dir().parent / "voice_lab"
    results = []
    print(f"Modelos: final {args.final} ({args.hilos_final} hilos), parcial {args.parcial} ({args.hilos_parcial} hilos)"
          f" · tu idioma: {my_language} · KMP_BLOCKTIME={os.environ.get('KMP_BLOCKTIME', '-')}")
    for scenario in chosen:
        print(f"\n=== {scenario.name}: {scenario.about}")
        result = run(scenario, pipeline, translate, my_language, out)
        print("\n".join(result.lines))
        results.append(result)
    summary = {r.name: r.metrics for r in results}
    (out / "resultado.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== Resumen")
    keys = ["detectadas", "texto_aparece_p50", "final_p50", "traduccion_1a_p50", "traduccion_completa_p50", "wer",
            "voz_ok", "cpu_nucleos"]
    print("escenario    " + " ".join(f"{k[:12]:>12}" for k in keys))
    for r in results:
        cells = []
        for k in keys:
            v = r.metrics.get(k)
            cells.append(f"{v:12.2f}" if isinstance(v, float) else f"{str(v):>12}")
        print(f"{r.name:12s} " + " ".join(cells))
    print(f"\nCapturas y resultados en {out}")
    if held:
        translator, runner = held
        runner.submit(translator.close()).result(timeout=30)


if __name__ == "__main__":
    main()
