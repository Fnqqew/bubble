"""Reproduce la voz traducida de forma homogénea entre frases y con la expresión del jugador.

Las voces de la nube se solicitan frase por frase y el modelo introduce variaciones: la misma voz puede salir con un
tono medio de 100 Hz en una frase y de 250 Hz en la siguiente, hasta 10 dB más baja, y la misma frase pedida dos veces
difiere en el tono (±20 %). El resultado suena como hablantes distintos.

Por eso cada frase se lleva al tono y al volumen habituales de esa voz (su referencia, que se aprende frase a frase) y
luego se le aplica la expresión del jugador: más aguda y más fuerte si gritó, más suave si habló bajo, con la melodía
más marcada si habló animado y con subida final si preguntó. El tono se modifica sin alterar la velocidad ni el timbre
(TD-PSOLA: se extraen los ciclos de la voz uno por uno y se vuelven a colocar más juntos o más separados).
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

HOP_S = 0.01
FRAME_S = 0.04
MIN_HZ, MAX_HZ = 60.0, 500.0
# Volumen de la voz como lo percibe el oído (ver loudness_db), igual para todas las voces y frases. Equivale a unos
# -17,5 LUFS: 1,5 dB más que el promedio anterior (algunas voces se escuchaban bajitas).
TARGET_DB = -15.0
MAX_GAIN_DB = 14.0
MAX_CORRECTION = 8.0  # máximo de semitonos que se corrige una frase (la nube varió más de una octava)
NOT_THE_VOICE = 12.0  # más allá de este valor se trata de un error de medición, no de la voz (no se corrige)
PULL = 0.8  # fracción que se acerca a la referencia; del todo sonaría plano (parte es expresión)
DEADBAND = 0.6  # semitonos: por debajo no se percibe, así que una voz pareja casi no se toca
MEMORY = 12  # frases que forman la referencia de cada voz
# Timbre de las voces de Piper (mediana de todas): parte de la energía entre 2 y 5 kHz (presencia) y entre 5 y 8 kHz
# (brillo), en dB. Una voz bastante por debajo en las dos suena apagada, como si murmurara (la japonesa: 8 dB menos de
# presencia y 21 dB menos de brillo); se le sube la presencia (ver presence).
PRESENCE_SHARE_DB = -16.7
BRIGHTNESS_SHARE_DB = -19.6
MAX_PRESENCE_DB = 8.0
SEED_WEIGHT = 3  # el tono medido de antemano (voces de la nube) pesa como estas frases


@dataclass(frozen=True)
class Expression:
    """Transformaciones que se aplican a la voz según la forma de decir algo."""

    semitones: float = 0.0  # más aguda o más grave que lo habitual
    gain_db: float = 0.0  # más fuerte o más suave
    contour: float = 1.0  # melodía: >1 más marcada (animada), <1 más pareja
    rise: float = 0.0  # si no sube al final (pregunta), semitonos que se le agregan


EXPRESSIONS = {
    "shout": Expression(semitones=2.5, gain_db=4.0, contour=1.3),
    "exclaim": Expression(semitones=1.3, gain_db=2.0, contour=1.2),
    "soft": Expression(semitones=-1.0, gain_db=-5.0, contour=0.85),
    "animated": Expression(contour=1.2),
    "flat": Expression(contour=0.85),
    "question": Expression(rise=2.5),
}


def expression(style: str) -> Expression:
    """Combina en una sola las marcas de expresión ("question+exclaim", "shout"…)."""
    marks = [EXPRESSIONS[mark] for mark in (style or "").split("+") if mark in EXPRESSIONS]
    if not marks:
        return Expression()
    return Expression(semitones=max((m.semitones for m in marks), key=abs, default=0.0),
                      gain_db=max((m.gain_db for m in marks), key=abs, default=0.0),
                      contour=float(np.prod([m.contour for m in marks])),
                      rise=max(m.rise for m in marks))


# ---------------------------------------------------------------- ayudas (con numpy: scipy.signal tarda 2 s en
# cargarse y mientras tanto la ventana no responde)
def _median5(values: np.ndarray) -> np.ndarray:
    padded = np.pad(values, 2, mode="edge")
    return np.median(sliding_window_view(padded, 5), axis=1)


def _lowpass(audio: np.ndarray, rate: int, cutoff: float) -> np.ndarray:
    """Conserva solo los graves (hasta `cutoff` Hz), sin desplazar la señal en el tiempo."""
    taps = int(rate * 0.004) | 1
    n = np.arange(taps) - taps // 2
    kernel = np.sinc(2 * cutoff / rate * n) * np.hamming(taps)
    return np.convolve(audio, (kernel / kernel.sum()).astype(np.float32), mode="same")


def _resample(audio: np.ndarray, length: int) -> np.ndarray:
    """La misma señal con otra cantidad de muestras (equivale a reproducirla más lenta o más rápida)."""
    spectrum = np.fft.rfft(audio)
    kept = np.zeros(length // 2 + 1, dtype=spectrum.dtype)
    size = min(len(spectrum), len(kept))
    kept[:size] = spectrum[:size]
    return (np.fft.irfft(kept, length) * (length / len(audio))).astype(np.float32)


# ---------------------------------------------------------------- el tono, ciclo por ciclo
def pitch_track(audio: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Tono (Hz) cada 10 ms y si hay voz en ese instante (vocal, no ruido ni silencio). Usa YIN: toma el primer período
    que se repite casi igual, no el más parecido, porque la autocorrelación sola a veces detectaba el doble del tono
    en voces graves y la corrección lo interpretaba como otra voz.
    """
    frame, hop = int(FRAME_S * rate), int(HOP_S * rate)
    count = 1 + (len(audio) - frame) // hop
    if count < 3:
        return np.zeros(0), np.zeros(0, dtype=bool)
    index = np.arange(frame)[None, :] + hop * np.arange(count)[:, None]
    frames = audio[index].astype(np.float64)
    frames -= frames.mean(axis=1, keepdims=True)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    power = np.cumsum(frames ** 2, axis=1)
    spectrum = np.fft.rfft(frames, n=2 * frame)
    corr = np.fft.irfft(spectrum * np.conj(spectrum))[:, :frame]
    lags = np.arange(frame)
    head = power[:, frame - 1 - lags]  # energía de la parte comparada al principio…
    tail = power[:, -1:] - np.concatenate([np.zeros((count, 1)), power[:, :-1]], axis=1)  # …y al final
    diff = np.maximum(head + tail - 2 * corr, 0.0)
    running = np.cumsum(diff[:, 1:], axis=1)
    cmnd = np.ones_like(diff)
    cmnd[:, 1:] = diff[:, 1:] * lags[1:] / np.maximum(running, 1e-12)
    low, high = int(rate / MAX_HZ), min(frame - 2, int(rate / MIN_HZ))
    window = cmnd[:, low:high]
    below = window < 0.15
    first = np.where(below.any(axis=1), below.argmax(axis=1), window.argmin(axis=1))
    rows = np.arange(count)
    for _ in range(12):  # hasta el fondo de ese valle
        step = np.minimum(first + 1, window.shape[1] - 1)
        better = window[rows, step] < window[rows, first]
        if not better.any():
            break
        first = np.where(better, step, first)
    lag = first + low
    left, mid, right = cmnd[rows, lag - 1], cmnd[rows, lag], cmnd[rows, np.minimum(lag + 1, frame - 1)]
    bend = left - 2 * mid + right
    shift = np.where(np.abs(bend) > 1e-9, 0.5 * (left - right) / np.where(np.abs(bend) > 1e-9, bend, 1.0), 0.0)
    f0 = rate / (lag + np.clip(shift, -0.5, 0.5))
    voiced = (mid < 0.3) & (rms > max(float(rms.max()) * 0.06, 1e-4))
    if voiced.sum() >= 3:
        smooth = _median5(np.where(voiced, f0, 0.0))
        f0 = np.where(voiced & (smooth > 0), smooth, f0)
    return f0, voiced


def median_pitch(audio: np.ndarray, rate: int) -> float | None:
    f0, voiced = pitch_track(audio, rate)
    return float(np.median(f0[voiced])) if voiced.sum() >= 8 else None


def _marks(audio: np.ndarray, rate: int, f0: np.ndarray, voiced: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inicio de cada ciclo de la voz (una marca por período) y si la marca corresponde a voz. Sin voz, una marca cada
    5 ms (ahí no se modifica nada).
    """
    hop = int(HOP_S * rate)
    smooth = _lowpass(audio, rate, 900.0)
    marks: list[int] = []
    kinds: list[bool] = []
    position, total = 0, len(audio)
    fixed = max(1, int(0.005 * rate))
    while position < total:
        frame = min(len(f0) - 1, position // hop) if len(f0) else 0
        if len(f0) and voiced[frame] and f0[frame] > 0:
            period = rate / f0[frame]
            start = int(position)
            stop = min(total, int(position + period))
            if marks and kinds[-1]:  # siguiente ciclo: cerca de un período después del anterior
                expected = marks[-1] + period
                start = int(max(marks[-1] + 0.6 * period, expected - 0.25 * period))
                stop = int(min(total, expected + 0.25 * period))
            if stop - start < 2:
                break
            peak = start + int(np.argmax(smooth[start:stop]))
            marks.append(peak)
            kinds.append(True)
            position = peak + max(1, int(0.75 * period))
        else:
            marks.append(position)
            kinds.append(False)
            position += fixed
    return np.asarray(marks, dtype=np.int64), np.asarray(kinds, dtype=bool)


@lru_cache(maxsize=512)
def _window(left: int, right: int) -> np.ndarray:
    """Ventana de un ciclo: sube en `left` muestras y baja en `right` (los ciclos vecinos pueden medir distinto)."""
    return np.concatenate([np.hanning(2 * left + 1)[:left], np.hanning(2 * right + 1)[right:-1]]).astype(np.float32)


def psola(audio: np.ndarray, rate: int, factor, f0: np.ndarray | None = None, voiced: np.ndarray | None = None,
          stretch: float = 1.0) -> np.ndarray:
    """La misma voz con el tono multiplicado por `factor` (un número, o una función del tiempo en segundos que devuelve
    el factor en ese instante), sin cambiar el timbre. `stretch`: alargamiento (1 = igual).
    """
    audio = np.asarray(audio, dtype=np.float32)
    if f0 is None or voiced is None:
        f0, voiced = pitch_track(audio, rate)
    if len(f0) == 0 or not voiced.any():
        return audio
    marks, kinds = _marks(audio, rate, f0, voiced)
    if len(marks) < 3:
        return audio
    length = int(round(len(audio) * stretch))
    out = np.zeros(length + rate // 10, dtype=np.float32)
    at = float(marks[0]) * stretch
    out[:int(at)] = np.interp(np.arange(int(at)) / stretch, np.arange(len(audio)), audio)
    k = 0
    last = len(marks) - 1
    while at < length:
        source = at / stretch
        # La marca original más cercana a este instante.
        while k < last and abs(marks[k + 1] - source) <= abs(marks[k] - source):
            k += 1
        left = marks[k] - marks[k - 1] if k > 0 else (marks[1] - marks[0])
        right = marks[k + 1] - marks[k] if k < last else left
        grain_start, grain_stop = marks[k] - left, marks[k] + right
        if grain_start < 0 or grain_stop > len(audio) or left < 2 or right < 2:
            at += max(1, right)
            continue
        grain = audio[grain_start:grain_stop] * _window(int(left), int(right))
        place = int(round(at)) - left
        if place >= 0 and place + len(grain) <= len(out):
            out[place:place + len(grain)] += grain
        if kinds[k]:
            value = factor(source / rate) if callable(factor) else factor
            step = right / max(0.5, min(2.0, float(value)))
        else:
            step = right
        at += max(1.0, step)
    return out[:length]


def change_gender(audio: np.ndarray, rate: int, to: str) -> np.ndarray:
    """La otra voz, para un idioma que tiene una sola: de mujer a hombre (o al revés), como el «Change gender» de
    Praat. Se desplazan los formantes (el timbre, según el largo de la garganta) remuestreando, y luego se lleva el
    tono al de una voz de ese género sin cambiar la duración.
    """
    audio = np.asarray(audio, dtype=np.float32)
    pitch = median_pitch(audio, rate)
    if pitch is None:
        return audio
    male = to.startswith("m")
    target = 112.0 if male else 215.0  # tono medio típico
    # Remuestrear: los formantes quedan multiplicados por down/up (~14 % más graves para un hombre, 16 % más agudos
    # para una mujer).
    up, down = (50, 43) if male else (25, 29)
    shifted = _resample(audio, int(round(len(audio) * up / down)))  # más larga y más grave (o al revés)
    actual = down / up
    factor = target / (pitch * actual)
    return psola(shifted, rate, factor, stretch=len(audio) / max(1, len(shifted)))


def level_db(audio: np.ndarray, rate: int) -> float | None:
    """Volumen de lo que suena (sin contar los silencios), en dB."""
    frame = int(0.02 * rate)
    count = len(audio) // frame
    if count < 3:
        return None
    rms = np.sqrt(np.mean(audio[:count * frame].reshape(count, frame) ** 2, axis=1))
    loud = rms[rms > max(float(rms.max()) * 0.1, 1e-4)]
    return float(20 * np.log10(np.sqrt(np.mean(loud ** 2)) + 1e-12)) if len(loud) else None


@lru_cache(maxsize=8)
def _k_weighting(rate: int, size: int) -> np.ndarray:
    """Cuánto pesa cada frecuencia para el oído (ponderación K de BS.1770: un estante de +4 dB desde ~1,7 kHz y un
    corte por debajo de ~38 Hz), para una FFT de `size` muestras. Coeficientes de pyloudnorm, para cualquier
    frecuencia de muestreo.
    """
    z = np.exp(2j * np.pi * np.fft.rfftfreq(size, 1 / rate) / rate)

    def response(b, a):
        return (b[0] + b[1] / z + b[2] / z ** 2) / (a[0] + a[1] / z + a[2] / z ** 2)

    gain, q, cutoff = 3.999843853973347, 0.7071752369554196, 1681.974450955533
    k = np.tan(np.pi * cutoff / rate)
    high, band = 10 ** (gain / 20), 10 ** (gain / 20) ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    shelf = response([(high + band * k / q + k * k) / a0, 2 * (k * k - high) / a0, (high - band * k / q + k * k) / a0],
                     [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    q, cutoff = 0.5003270373238773, 38.13547087602444
    k = np.tan(np.pi * cutoff / rate)
    a0 = 1 + k / q + k * k
    low_cut = response([1, -2, 1], [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    return np.abs(shelf * low_cut)


def loudness_db(audio: np.ndarray, rate: int) -> float | None:
    """Volumen de lo que suena como lo percibe el oído (ponderación K), en dB. Igualar el volumen eléctrico (RMS)
    dejaba las voces graves y la japonesa hasta 5 dB más bajas al oído que las demás.
    """
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) < int(0.06 * rate):
        return None
    size = 1 << int(np.ceil(np.log2(len(audio))))
    weighted = np.fft.irfft(np.fft.rfft(audio, size) * _k_weighting(rate, size), size)[:len(audio)]
    return level_db(weighted.astype(np.float32), rate)


def timbre(audio: np.ndarray, rate: int) -> tuple[float, float] | None:
    """(presencia, brillo) de la frase: parte de su energía entre 2 y 5 kHz y entre 5 y 8 kHz, en dB."""
    audio = np.asarray(audio, dtype=np.float32)
    if len(audio) < rate // 4:
        return None
    power = np.abs(np.fft.rfft(audio * np.hanning(len(audio)))) ** 2
    frequencies = np.fft.rfftfreq(len(audio), 1 / rate)
    total = power[(frequencies > 80) & (frequencies < 8000)].sum()
    if total <= 0:
        return None
    def share(low: float, high: float) -> float:
        return float(10 * np.log10(power[(frequencies >= low) & (frequencies < high)].sum() / total + 1e-12))

    return share(2000, 5000), share(5000, 8000)


def presence(audio: np.ndarray, rate: int, gain_db: float) -> np.ndarray:
    """Más presencia: sube de forma suave lo que está por encima de 1 kHz, hasta `gain_db` desde los 3 kHz (ahí está
    la claridad de la voz). Para las voces opacas, que sonaban apagadas, como si murmuraran.
    """
    audio = np.asarray(audio, dtype=np.float32)
    if gain_db <= 0 or len(audio) < 64:
        return audio
    size = 1 << int(np.ceil(np.log2(len(audio) + 2048)))  # con margen: sin que el final se mezcle con el principio
    frequencies = np.fft.rfftfreq(size, 1 / rate)
    rise = np.clip(np.log2(np.maximum(frequencies, 1.0) / 1000.0) / np.log2(3.0), 0.0, 1.0)
    curve = 10 ** (gain_db * (3 * rise ** 2 - 2 * rise ** 3) / 20)  # transición suave, sin resonancias
    return np.fft.irfft(np.fft.rfft(audio, size) * curve, size)[:len(audio)].astype(np.float32)


def _limit(audio: np.ndarray, ceiling: float = 0.97) -> np.ndarray:
    """Sin saturar: por encima de 0,8 se redondea suavemente hasta el techo, en lugar de recortar."""
    knee = 0.8
    magnitude = np.abs(audio)
    over = magnitude > knee
    if not over.any():
        return audio
    squeezed = knee + (ceiling - knee) * np.tanh((magnitude[over] - knee) / (ceiling - knee))
    audio = audio.copy()
    audio[over] = np.sign(audio[over]) * squeezed
    return audio


def _ramp(audio: np.ndarray, rate: int, seconds: float = 0.004) -> np.ndarray:
    """Evita el clic al empezar y al terminar."""
    size = min(len(audio) // 2, int(seconds * rate))
    if size < 2:
        return audio
    audio = audio.copy()
    ramp = np.linspace(0.0, 1.0, size, dtype=np.float32)
    audio[:size] *= ramp
    audio[-size:] *= ramp[::-1]
    return audio


class Polish:
    """Iguala cada frase con la referencia de su voz y le aplica la expresión del jugador. Una única instancia para
    todas las voces.
    """

    def __init__(self) -> None:
        self._pitches: dict[str, deque] = {}
        self._seeds: dict[str, float] = {}
        self._timbres: dict[str, deque] = {}
        self._lock = threading.Lock()

    def seed(self, pitches: dict[str, float]) -> None:
        """Tono habitual de voces conocidas (las de la nube): se igualan desde la primera frase."""
        with self._lock:
            self._seeds.update(pitches)

    def reference(self, voice: str) -> float | None:
        with self._lock:
            known = list(self._pitches.get(voice, ()))
            seed = self._seeds.get(voice)
        if seed:
            known += [seed] * SEED_WEIGHT
        return float(np.median(known)) if len(known) >= 2 else None

    def learn(self, voice: str, pitch: float) -> None:
        with self._lock:
            self._pitches.setdefault(voice, deque(maxlen=MEMORY)).append(pitch)

    def correction(self, voice: str, pitch: float | None) -> float:
        """Semitonos que hay que mover esta frase para que suene con el tono habitual de su voz (0 si ya coincide).
        La frase pasa a formar parte de la referencia.
        """
        wanted = self.wanted(voice, pitch)
        if pitch is not None:
            self.learn(voice, pitch)
        return wanted

    def wanted(self, voice: str, pitch: float | None) -> float:
        """Lo mismo, sin aprender (para los tramos de una frase que todavía está llegando)."""
        reference = self.reference(voice) if pitch is not None else None
        if reference is None:
            return 0.0
        wrong = 12 * np.log2(reference / pitch)
        if abs(wrong) < DEADBAND or abs(wrong) > NOT_THE_VOICE:
            return 0.0
        return float(np.clip(wrong * PULL, -MAX_CORRECTION, MAX_CORRECTION))

    def brightening(self, voice: str, audio: np.ndarray, rate: int) -> float:
        """Cuánta presencia le falta a esta voz (dB, 0 si no es opaca). Se aprende de sus frases, como el tono."""
        measured = timbre(audio, rate)
        with self._lock:
            known = self._timbres.setdefault(voice, deque(maxlen=MEMORY))
            if measured is not None:
                known.append(measured)
            if not known:
                return 0.0
            share, bright = np.median(np.array(known), axis=0)
        missing = min(PRESENCE_SHARE_DB - share, BRIGHTNESS_SHARE_DB - bright)  # opaca: le faltan las dos cosas
        return float(np.clip(missing * 0.8, 0.0, MAX_PRESENCE_DB))

    def apply(self, audio: np.ndarray, rate: int, voice: str = "", style: str = "", correct: float | None = None,
              gain_db: float | None = None, tone: bool = True, bright: bool = True) -> np.ndarray:
        """La frase entera, lista para reproducir. `correct`/`gain_db`: valores ya decididos (ver Streaming).
        `tone`: con False no se modifica el tono (la voz ya lo fijó según la expresión del jugador: ver
        cloud/speak.py, Flux).
        """
        audio = np.asarray(audio, dtype=np.float32)
        if len(audio) < rate // 20:
            return audio
        how = expression(style)
        if tone:
            f0, voiced = pitch_track(audio, rate)
            pitch = float(np.median(f0[voiced])) if voiced.sum() >= 8 else None
            if correct is None:
                correct = self.correction(voice, pitch) if voice else 0.0
            shift = correct + how.semitones
            factor = self._factor(f0, voiced, rate, shift, how, len(audio))
            if factor is not None:
                audio = psola(audio, rate, factor, f0, voiced)
        if voice and bright:
            audio = presence(audio, rate, self.brightening(voice, audio, rate))
        if gain_db is None:
            level = loudness_db(audio, rate)
            gain_db = 0.0 if level is None else float(np.clip(TARGET_DB - level, -MAX_GAIN_DB, MAX_GAIN_DB))
            gain_db += how.gain_db
        audio = audio * np.float32(10 ** (gain_db / 20))
        return _ramp(_limit(audio), rate)

    @staticmethod
    def _factor(f0: np.ndarray, voiced: np.ndarray, rate: int, shift: float, how: Expression, length: int):
        """Factor de tono en cada instante (None si no hay nada que cambiar)."""
        if voiced.sum() < 8:
            return None
        pitch = float(np.median(f0[voiced]))
        needs_rise = 0.0
        if how.rise:
            # ¿Ya sube al final? (la voz de la nube casi siempre sube con "?"; las locales, menos)
            voiced_index = np.flatnonzero(voiced)
            tail = voiced_index[-max(4, len(voiced_index) // 4):]
            body = voiced_index[:-len(tail)] if len(voiced_index) > len(tail) else voiced_index
            rise_now = 12 * np.log2(np.median(f0[tail]) / np.median(f0[body]))
            needs_rise = max(0.0, how.rise - rise_now) if rise_now < 1.5 else 0.0
        if abs(shift) < 0.05 and abs(how.contour - 1.0) < 0.01 and not needs_rise:
            return None
        hop = HOP_S
        times = np.arange(len(f0)) * hop + FRAME_S / 2
        semis = np.where(voiced & (f0 > 0), 12 * np.log2(np.maximum(f0, 1.0) / pitch), 0.0)
        end = length / rate
        last_voice = times[np.flatnonzero(voiced)[-1]] if voiced.any() else end
        ramp_s = 0.35

        def factor(t: float) -> float:
            frame = min(len(f0) - 1, max(0, int((t - FRAME_S / 2) / hop)))
            move = shift + (how.contour - 1.0) * semis[frame]  # la melodía, más o menos marcada
            if needs_rise and t > last_voice - ramp_s:
                move += needs_rise * min(1.0, (t - (last_voice - ramp_s)) / ramp_s)
            return float(2 ** (np.clip(move, -8, 8) / 12))

        return factor


POLISH = Polish()  # una sola para todo Bubble: cada voz aprende su tono habitual una única vez


class Streaming:
    """La voz de la nube llega en fragmentos: se entrega igualada, cortando en las pausas para que cada tramo se
    procese completo. El tono y el volumen se corrigen tramo a tramo, porque la nube también varía dentro de una
    misma frase (159 Hz al principio, 231 Hz al final), pero de forma gradual (en una pausa un cambio pequeño no se
    nota). Decidir todo con el primer medio segundo fallaba: si la frase empezaba suave, quedaba 10 dB más fuerte.
    """

    FIRST_S = 0.45  # mínimo para entregar el primer tramo (llega en ~0,2 s: la nube genera más rápido)
    LONGEST_S = 0.8  # sin pausas, se entrega igual cada cierto tiempo
    GAIN_STEP_DB = 3.0  # variación máxima de volumen entre un tramo y el siguiente
    PITCH_STEP = 2.0  # y de tono (semitonos)
    TYPICAL_DB = -18.5  # volumen habitual de la voz de la nube, al oído (punto de partida)

    def __init__(self, polish: Polish, rate: int, voice: str, style: str, tone: bool = True) -> None:
        self.polish, self.rate, self.voice, self.style = polish, rate, voice, style
        self.tone = tone
        self._pending = np.zeros(0, dtype=np.float32)
        self._raw: list[np.ndarray] = []
        self._correct: float | None = None
        self._gain = float(np.clip(TARGET_DB - self.TYPICAL_DB, -MAX_GAIN_DB, MAX_GAIN_DB)) +             expression(style).gain_db

    def feed(self, piece: np.ndarray) -> list[np.ndarray]:
        self._pending = np.concatenate([self._pending, np.asarray(piece, dtype=np.float32)])
        if not self._raw and len(self._pending) < self.FIRST_S * self.rate:
            return []
        cut = self._pause(self._pending)
        if cut is None:
            return []
        ready, self._pending = self._pending[:cut], self._pending[cut:]
        return [self._process(ready, last=False)]

    def finish(self) -> list[np.ndarray]:
        out = []
        if len(self._pending):
            ready, self._pending = self._pending, np.zeros(0, dtype=np.float32)
            out.append(self._process(ready, last=True))
        if self._raw and self.voice and self.tone:
            pitch = median_pitch(np.concatenate(self._raw), self.rate)
            if pitch is not None:
                self.polish.learn(self.voice, pitch)  # la frase entera, a la referencia de su voz
        return out

    def _pause(self, audio: np.ndarray) -> int | None:
        """Último silencio (pausa entre palabras) donde se puede cortar sin que se note. Si hace tiempo que no hay
        ninguno (voz muy ligada), el punto más bajo de la segunda mitad; si no, la frase entera quedaría
        esperando.
        """
        frame = int(0.01 * self.rate)
        count = len(audio) // frame
        if count < 30:
            return None
        rms = np.sqrt(np.mean(audio[:count * frame].reshape(count, frame) ** 2, axis=1))
        quiet = np.flatnonzero(rms[10:-5] < max(float(rms.max()) * 0.03, 1e-4)) + 10
        if len(quiet):
            return int(quiet[-1] * frame)
        if count * frame < self.LONGEST_S * self.rate:
            return None
        return int((count // 2 + int(np.argmin(rms[count // 2:-5]))) * frame)

    def _process(self, audio: np.ndarray, last: bool) -> np.ndarray:
        self._raw.append(audio)
        seconds = len(audio) / self.rate
        pitch = median_pitch(audio, self.rate) if self.voice and self.tone else None
        if pitch is not None:
            wanted = self.polish.wanted(self.voice, pitch)
            if self._correct is None:
                self._correct = wanted
            else:
                self._correct += float(np.clip(wanted - self._correct, -self.PITCH_STEP, self.PITCH_STEP))
        level = loudness_db(audio, self.rate)
        if level is not None and seconds >= 0.15:
            wanted = float(np.clip(TARGET_DB - level, -MAX_GAIN_DB, MAX_GAIN_DB)) + expression(self.style).gain_db
            step = self.GAIN_STEP_DB * min(1.0, seconds / 0.4)  # un tramo corto se mueve menos
            self._gain += float(np.clip(wanted - self._gain, -step, step))
        style = self.style if last else "+".join(mark for mark in self.style.split("+") if mark != "question")
        return self.polish.apply(audio, self.rate, self.voice, style, correct=self._correct or 0.0,
                                 gain_db=self._gain, tone=self.tone)
