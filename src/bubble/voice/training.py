"""Entrenar tu voz (opcional, en la página Pruebas): leés unas frases y contestás unas preguntas con tus palabras.

Primera parte, leer: frases como las de una partida, con voseo, jerga de juego ("pvp", "tradear", "farmear", "lag"),
nombres de juegos, preguntas, exclamaciones y un par de gritos. Con cada una Bubble aprende:
- las palabras que Whisper no te entendió (se las pasa como pistas desde ahí: la próxima vez las entiende);
- tu voz de siempre (tono y volumen), para notar cuándo exclamás o gritás;
- cuánto sube TU voz al preguntar (cada uno pregunta distinto) y cómo suena tu grito: los umbrales pasan a ser tuyos.

Segunda parte, con tus palabras: te pregunta cómo saludás, qué decís cuando ganás o perdés, cómo pedís ayuda… y
contestás como hablás. Lo que decís (corregido si hizo falta) queda como tu vocabulario y tus expresiones.

Se puede cortar en cualquier momento y seguir otro día.
"""

from __future__ import annotations

import os
import re
import statistics
import wave
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .checks import word_error_rate, words
from .speech import Melody

SAMPLE_RATE = 16000


@dataclass(frozen=True)
class Item:
    text: str  # la frase para leer, o la pregunta para contestar con tus palabras
    kind: str = "normal"  # "normal" | "question" | "exclaim" | "shout" | "soft" | "free"
    how: str = ""  # cómo decirla ("Preguntalo", "Gritalo si podés")


HOW = {"normal": "Leelo como lo dirías en el juego.", "question": "Preguntalo, como en el juego.",
       "exclaim": "Decilo con ganas.", "shout": "Gritalo (si podés; si no, saltealo).",
       "soft": "Decilo bajito, como si no quisieras que te escuchen.",
       "free": "Contestá con tus palabras, como hablás de verdad. Después podés corregir lo que entendí."}

SCRIPTS: dict[str, list[Item]] = {
    "es": [
        Item("Che, estoy en el lobby esperando que arranque la partida."),
        Item("¿Alguien viene conmigo a la torre?", "question"),
        Item("Dale, vamos al jefe entre todos, yo lo distraigo y ustedes le pegan."),
        Item("¿Me pasás la espada de fuego?", "question"),
        Item("Tengo un poco de lag, bancame un toque que se me traba el juego."),
        Item("¡Qué bueno! ¡Ganamos la partida!", "exclaim"),
        Item("Posta que este obby está re difícil, ya me caí como diez veces."),
        Item("¿Vamos al boss ahora o esperamos a los demás?", "question"),
        Item("Quiero tradear mi mascota legendaria por tu skin."),
        Item("¿Tenés lugar en tu equipo?", "question"),
        Item("Vamos a farmear un rato en el mapa nuevo y después hacemos pvp."),
        Item("¡No puede ser, me mataron de nuevo!", "exclaim"),
        Item("Me quedan dos mil robux, capaz me compro el pase de juego."),
        Item("¿Ya terminaste el nivel?", "question"),
        Item("Esperame en el checkpoint que ya llego, voy saltando las plataformas."),
        Item("¡CUIDADO, ATRÁS TUYO!", "shout"),
        Item("Ese chabón es re troll, nos está matando cuando reaparecemos."),
        Item("¿Cuánto querés por esa mascota?", "question"),
        Item("Joya, me re sirve, gracias por la ayuda, sos un crack."),
        Item("¡Uh, qué buena jugada, boludo!", "exclaim"),
        Item("Tranqui, no pasa nada, la próxima la ganamos seguro."),
        Item("Che, no hagas ruido que nos van a encontrar.", "soft"),
        Item("Estoy jugando Blox Fruits, Brookhaven y Adopt Me con mis amigos."),
        Item("¿Me agregás de amigo así jugamos mañana?", "question"),
        Item("Nah, ni en pedo hago eso, es re peligroso."),
        Item("¡CORRAN, QUE VIENE EL BOSS!", "shout"),
        Item("Uy, perdón, fue sin querer, no te quería matar."),
        Item("Qué onda, gente, recién me uno al servidor, ¿todo bien?", "question"),
        Item("¿Cómo saludás cuando entrás a un servidor?", "free"),
        Item("¿Qué decís cuando ganás una partida?", "free"),
        Item("¿Y cuando perdés o te matan?", "free"),
        Item("¿Cómo le pedís ayuda a alguien?", "free"),
        Item("¿Cómo le proponés un intercambio a alguien?", "free"),
        Item("Decí los nombres de tus amigos o de los juegos que más jugás.", "free"),
        Item("¿Qué palabras o expresiones usás mucho? Decí algunas.", "free"),
    ],
    "en": [
        Item("Hey, I'm in the lobby waiting for the match to start."),
        Item("Is anyone coming with me to the tower?", "question"),
        Item("Okay, let's all go fight the boss, I'll distract it and you guys hit it."),
        Item("Can you pass me the fire sword?", "question"),
        Item("I'm lagging a bit, hold on, my game keeps freezing."),
        Item("Yes! We won the match!", "exclaim"),
        Item("This obby is honestly so hard, I've already fallen like ten times."),
        Item("Are we doing the boss now or waiting for the others?", "question"),
        Item("I wanna trade my legendary pet for your skin."),
        Item("Do you have room on your team?", "question"),
        Item("Let's farm for a bit on the new map and then do some PvP."),
        Item("No way, they killed me again!", "exclaim"),
        Item("I've got two thousand robux left, I might buy the game pass."),
        Item("Did you finish the level already?", "question"),
        Item("Wait for me at the checkpoint, I'm jumping across the platforms."),
        Item("WATCH OUT, BEHIND YOU!", "shout"),
        Item("That guy is such a troll, he keeps spawn killing us."),
        Item("How much do you want for that pet?", "question"),
        Item("Nice, that really helps, thanks bro, you're a legend."),
        Item("Oh, what a play, that was insane!", "exclaim"),
        Item("Chill, it's fine, we'll win the next one for sure."),
        Item("Shh, be quiet or they're gonna find us.", "soft"),
        Item("I'm playing Blox Fruits, Brookhaven and Adopt Me with my friends."),
        Item("Can you add me as a friend so we can play tomorrow?", "question"),
        Item("Nah, no way I'm doing that, it's way too risky."),
        Item("RUN, THE BOSS IS COMING!", "shout"),
        Item("Oops, sorry, that was an accident, I didn't mean to kill you."),
        Item("What's up everyone, I just joined the server, how's it going?", "question"),
        Item("How do you say hi when you join a server?", "free"),
        Item("What do you say when you win a match?", "free"),
        Item("And when you lose or get killed?", "free"),
        Item("How do you ask someone for help?", "free"),
        Item("How do you offer someone a trade?", "free"),
        Item("Say the names of your friends or the games you play the most.", "free"),
        Item("What words or expressions do you use a lot? Say a few.", "free"),
    ],
}


def script_for(language: str) -> list[Item]:
    """Las frases para tu idioma (por ahora español e inglés; si no, vacío)."""
    return SCRIPTS.get(language.split("-")[0].lower(), [])


@dataclass
class Result:
    item: Item
    heard: str
    error: float  # palabras mal entendidas (0 a 1)
    missed: list[str] = field(default_factory=list)  # palabras de la frase que Whisper no entendió
    melody: Melody | None = None


def missed_words(expected: str, heard: str) -> list[str]:
    """Palabras de la frase que no aparecen en lo que entendió Whisper (las que más conviene enseñarle)."""
    got = set(words(heard))
    return [word for word in dict.fromkeys(words(expected)) if word not in got and len(word) > 2]


def check(item: Item, heard: str, melody: Melody | None) -> Result:
    if item.kind == "free":
        return Result(item, heard, 0.0, [], melody)
    return Result(item, heard, word_error_rate(item.text, heard), missed_words(item.text, heard), melody)


def feedback(result: Result) -> str:
    """Lo que se le dice después de cada frase."""
    if result.item.kind == "free":
        return "Revisá lo que entendí: si algo está mal, corregilo y tocá «Guardar»."
    if not result.heard:
        return "No te escuché. Tocá «Grabar» y leela de nuevo, un poco más fuerte."
    if result.error == 0:
        return "✓ Te entendí perfecto."
    if result.error <= 0.2:
        return f"✓ Te entendí casi todo. Aprendí: {', '.join(result.missed)}." if result.missed else "✓ Casi perfecto."
    listed = ", ".join(result.missed[:8])
    return f"Entendí «{result.heard}». Ya aprendí cómo decís: {listed}." if listed else f"Entendí «{result.heard}»."


# ---------------------------------------------------------------- tu voz grabada: para elegir con qué entenderte
def clips_dir(language: str) -> Path:
    """Tus grabaciones del entrenamiento (solo en tu PC): con ellas se mide cómo te entiende Bubble con tu voz real
    (ver tools/my_voice.py)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "Bubble" / "tu_voz" / language.split("-")[0].lower()


def save_clip(audio: np.ndarray, text: str, language: str, index: int) -> None:
    folder = clips_dir(language)
    folder.mkdir(parents=True, exist_ok=True)
    samples = (np.clip(np.asarray(audio, dtype=np.float32), -1, 1) * 32767).astype(np.int16)
    with wave.open(str(folder / f"{index:02d}.wav"), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.tobytes())
    (folder / f"{index:02d}.txt").write_text(text, encoding="utf-8")


def saved_clips(language: str) -> list[tuple[np.ndarray, str]]:
    clips = []
    for path in sorted(clips_dir(language).glob("*.wav")):
        text_path = path.with_suffix(".txt")
        if not text_path.exists():
            continue
        with wave.open(str(path), "rb") as source:
            audio = np.frombuffer(source.readframes(source.getnframes()), np.int16).astype(np.float32) / 32767
        clips.append((audio, text_path.read_text(encoding="utf-8")))
    return clips


def calibrate(results: list[Result], usual: tuple[float, float, float] | None) -> dict[str, float]:
    """Tus umbrales: cuánto sube tu voz al preguntar y cuánto más fuerte suena tu grito o tu exclamación.

    - Pregunta: a mitad de camino entre cómo terminan tus afirmaciones y tus preguntas de sí o no (las que empiezan
      con "qué/cuánto/cómo" suelen bajar al final aunque sean preguntas: no se cuentan).
    - Grito y exclamación: un poco menos de lo que subió tu volumen al gritar o exclamar (para notarlo aunque no
      grites tan fuerte en el juego)."""
    found: dict[str, float] = {}

    def rises(kind: str) -> list[float]:
        return [r.melody.rise for r in results if r.item.kind == kind and r.melody is not None
                and not re.match(r"[¿]?(qu[eé]|cu[aá]nt|c[oó]mo|d[oó]nde|how|what|where|who|why)\b", r.item.text,
                                 re.IGNORECASE)]

    statements, questions = rises("normal"), rises("question")
    if len(statements) >= 3 and len(questions) >= 2:
        low, high = statistics.median(statements), statistics.median(questions)
        if high - low >= 1.0:  # tus preguntas se distinguen: el umbral pasa a ser el tuyo
            # Pero una pregunta tiene que SUBIR: si tus afirmaciones bajan mucho, la mitad quedaba negativa (-1,2) y todo
            # parecía pregunta.
            found["question_rise"] = round(min(4.0, max(0.5, low + (high - low) / 2)), 2)
    if usual:
        _pitch, level, _effort = usual
        for kind, key, floor in (("shout", "shout_db", 4.0), ("exclaim", "exclaim_db", 2.5)):
            louder = [r.melody.level - level for r in results if r.item.kind == kind and r.melody is not None]
            if louder and statistics.median(louder) > floor:
                found[key] = round(max(floor, 0.6 * statistics.median(louder)), 1)
    return found
