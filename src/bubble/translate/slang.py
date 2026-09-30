"""Jerga, abreviaturas y expresiones regionales por idioma.

Se usa de dos formas:
- Como referencia en el prompt de Claude, para interpretar los mensajes con precisión.
- Localmente: los términos marcados como `hint` identifican el idioma o país sin ambigüedad y
  evitan errores del detector de idioma en mensajes cortos con mucha jerga.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Slang:
    term: str
    lang: str
    meaning: str
    region: str = ""
    # True si el término identifica el idioma o país con certeza (no es una palabra común de otro idioma).
    hint: bool = False


def _entries(lang: str, region: str, rows: list[tuple[str, str, bool]]) -> list[Slang]:
    return [Slang(term, lang, meaning, region, hint) for term, meaning, hint in rows]


LEXICON: tuple[Slang, ...] = tuple(
    # ---------- Roblox / gamer (todos los idiomas) ----------
    _entries("en", "", [
        ("obby", "obstacle course game", False),
        ("robux / rbx", "Roblox currency (never translate)", False),
        ("bacon hair", "player with the default avatar; mild insult for a newbie", False),
        ("oder", "'online dater' (people looking for romance, against Roblox rules)", False),
        ("lf / lf2", "looking for", False),
        ("ft / nft", "for trade / not for trade", False),
        ("fr / nfr / mfr", "in pet-trading games: fly-ride / neon fly-ride / mega neon fly-ride pet", False),
        ("w / l / f (in trades)", "win / loss / fair trade", False),
        ("admin abuse", "admins using commands on players", False),
        ("tryhard / sweat", "player who plays extremely seriously", False),
        ("griefing", "ruining other players' game on purpose", False),
        ("camping / spawn kill", "waiting in one spot / killing players as they respawn", False),
        ("carry", "help a weaker player progress", False),
        ("gg / ggwp / ez", "good game / good game well played / easy (taunt)", False),
        ("afk / brb / gtg / g2g", "away / be right back / got to go", False),
        ("op / nerf / buff", "overpowered / weaken / strengthen", False),
        ("noob / nub / newbie", "beginner, often an insult", False),
        ("oof", "Roblox death sound, expresses failure", False),
        ("npc", "someone acting mindless or scripted (insult)", False),
    ])
    # ---------- Inglés de internet ----------
    + _entries("en", "US", [
        ("fr", "for real", False), ("ngl", "not gonna lie", True), ("istg", "I swear to God", True),
        ("smh", "shaking my head (disapproval)", True), ("tbh / imo", "to be honest / in my opinion", True),
        ("idk / idc / ikr", "I don't know / I don't care / I know, right", True),
        ("rn / wyd / hbu / lmk", "right now / what are you doing / how about you / let me know", True),
        ("ty / tysm / np", "thanks / thank you so much / no problem", False),
        ("sus", "suspicious", False), ("cap / no cap", "lie / for real, no lie", False),
        ("bet", "okay, deal", False), ("lowkey / highkey", "kind of, secretly / very, openly", True),
        ("mid", "mediocre", False), ("goat", "greatest of all time", False),
        ("ratio", "taunt: my reply got more support than yours", False),
        ("rizz", "charm, flirting skill", True), ("slay", "did great", False),
        ("ong", "on God, I swear", True), ("bussin", "very good", True), ("delulu", "delusional", True),
        ("skibidi / sigma / gyat / fanum tax", "brainrot memes with little literal meaning", True),
        ("bruh", "exasperation", False), ("copium", "denial, coping with a loss", True),
        ("pog / poggers", "awesome, hype", True),
        ("2tf / asf / af (after a word)", "intensifier: extremely (funny 2tf = hilarious; hard asf = really hard)", True),
        ("tf", "the f*** (what tf = what the hell); intensifier of surprise or anger", False),
        ("stfu / gtfo", "shut up / get out (rude)", True), ("ts / pmo", "this sh*t / pisses me off", False),
        ("icl / ion", "I can't lie / I don't", True), ("js", "just saying", False), ("hella", "very", True),
        ("gng / twin", "friends, bro (address)", True), ("fym", "what do you mean?! (rude)", True),
        ("wsg / wsp", "what's good / what's up", True), ("sm", "so much", False),
        ("tuff", "cool, impressive", True), ("aura", "coolness points (lost/gained aura)", False),
        ("crash out / crashout", "angry, reckless outburst", True), ("glaze / glazing", "overpraise someone", True),
        ("opp", "enemy, rival", True), ("unc", "old person (teasing)", True), ("cooked", "doomed, done for", False),
        ("yap / yapping", "talking too much", True), ("mog / mogging", "outshine someone", True),
        ("chat", "addressing everyone watching or the server", False), ("goofy", "silly, clownish (insult)", False),
        ("based", "admirably honest, not caring what others think", False),
    ])
    # ---------- Portugués (Brasil) ----------
    + _entries("pt", "BR", [
        ("kkkk / rsrs", "laughter", True), ("vc / vcs", "você(s), you", True), ("blz", "beleza: ok, cool", True),
        ("vlw", "valeu: thanks", True), ("tmj", "tamo junto: we're together / thanks, bro", True),
        ("mds", "meu Deus: oh my God", True), ("pfv / pf", "por favor: please", True),
        ("tb / tbm", "também: also", True), ("oq / pq", "o que / por que: what / why", True),
        ("cmg / ctg", "comigo / contigo: with me / with you", True), ("dnv", "de novo: again", True),
        ("sla", "sei lá: I don't know", True), ("slk", "sem loucura: are you crazy?! / wow", True),
        ("tlgd", "tá ligado: you know?", True), ("pdp", "pode pá: sure, okay", True),
        ("fds", "foda-se: whatever / screw it (rude)", True), ("mano / mn", "bro", False),
        ("suave", "cool, fine", False), ("top", "great", False), ("zoar", "to mess with, tease", False),
        ("bora / bó", "let's go", True), ("tô / tá", "estou / está: I'm / it's", True),
    ])
    # ---------- Español: general ----------
    + _entries("es", "", [
        ("xd / jaja", "laughter", False), ("ntp", "no te preocupes: don't worry", True),
        ("tqm", "te quiero mucho: love you (friendly)", True), ("xq / pq", "por qué / porque: why / because", True),
        ("q / k", "que: that / what", False), ("bn / tmb", "bien / también: good / also", True),
    ])
    # ---------- Español: Argentina / Uruguay ----------
    + _entries("es", "AR", [
        ("che", "hey (to get attention)", True), ("boludo / bolu", "dude (friendly) or idiot (insult), by tone", True),
        ("re", "very (re bueno = very good)", False), ("ahre", "just kidding / lol", True),
        ("posta", "for real, seriously", True), ("zarpado", "awesome or over the top", True),
        ("chabón", "guy, dude", True), ("joya", "great, cool", False), ("de una", "sure, right away", False),
        ("bardear", "to trash-talk, insult", True), ("manija", "hyped, eager", True),
        ("ortiva", "killjoy, snitch", True), ("fiaca", "laziness", True), ("guita", "money", True),
        ("laburo", "work, job", True), ("mina", "girl", False), ("bondi", "bus", True),
        ("mal (as intensifier)", "really, a lot (me gustó mal = I loved it)", False),
    ])
    # ---------- Español: México ----------
    + _entries("es", "MX", [
        ("wey / güey / we", "dude (can be insult)", True), ("neta", "for real, truth", True),
        ("chido / padre", "cool", True), ("no mames", "no way! (vulgar)", True), ("órale", "wow / ok, let's go", True),
        ("chale", "damn, bummer", True), ("qué onda", "what's up", False), ("carnal", "bro", True),
        ("fresa", "posh, snobby", False), ("chamba", "work", True),
    ])
    # ---------- Español: España ----------
    + _entries("es", "ES", [
        ("tío / tía", "dude", False), ("mola", "it's cool", True), ("guay", "cool", True), ("flipar", "to be amazed", True),
        ("vale", "okay", False), ("pringao", "loser", True), ("mazo", "a lot, very", True), ("hostia", "damn (exclamation)", False),
        ("vosotros", "you all", False),
    ])
    # ---------- Español: Colombia / Chile / Venezuela ----------
    + _entries("es", "CO", [
        ("parce / parcero", "bro", True), ("bacano", "cool", True), ("chimba", "awesome (vulgar in some contexts)", True),
    ])
    + _entries("es", "CL", [
        ("weón / weon / wn", "dude (friendly) or idiot", True), ("cachai", "you get it?", True),
        ("bacán", "cool", True), ("po", "filler for emphasis (sí po = yeah)", False),
    ])
    + _entries("es", "VE", [("pana", "buddy", False), ("chamo", "kid, dude", True), ("burda", "a lot, very", True)])
    # ---------- Francés ----------
    + _entries("fr", "FR", [
        ("mdr / ptdr", "laughing out loud", True), ("jsp", "je sais pas: I don't know", True),
        ("stp / svp", "please", True), ("wsh", "hey (greeting)", True), ("frr / frère", "bro", True),
        ("tkt", "don't worry", True), ("cc / slt", "hi", True), ("jpp", "I can't anymore (laughing)", True),
        ("osef", "who cares", True), ("ntm", "strong insult", True), ("wesh", "hey", True),
    ])
    # ---------- Alemán ----------
    + _entries("de", "DE", [
        ("digga / alter", "dude, bro", True), ("hdl", "hab dich lieb: love you (friendly)", True),
        ("kp", "kein Plan: no idea", True), ("vllt", "vielleicht: maybe", True), ("gg ez", "good game, easy", False),
        ("lol", "laughter", False),
    ])
    # ---------- Italiano ----------
    + _entries("it", "IT", [
        ("raga", "guys", True), ("cmq", "comunque: anyway", True), ("nn", "non: not", False),
        ("xke / xché", "perché: why / because", True), ("tvb", "love you (friendly)", True), ("ahahah", "laughter", False),
    ])
    # ---------- Ruso ----------
    + _entries("ru", "RU", [
        ("спс", "спасибо: thanks", True), ("пж / плз", "please", True), ("го", "let's go", True),
        ("норм", "okay, fine", True), ("хаха / ахах", "laughter", True), ("кек", "lol", True),
        ("изи", "easy", True), ("нуб", "noob", True),
    ])
    # ---------- Turco ----------
    + _entries("tr", "TR", [
        ("slm", "selam: hi", True), ("tmm", "tamam: okay", True), ("knk / kanka", "buddy, bro", True),
        ("aq", "vulgar curse (like wtf)", True), ("sjsjsj / ahsjdh", "laughter (keyboard smash)", True),
    ])
    # ---------- Indonesio / Malayo ----------
    + _entries("id", "ID", [
        ("wkwk", "laughter", True), ("gw / gue", "I, me", True), ("lu / lo", "you", False),
        ("anjir / anjay", "wow / damn (exclamation)", True), ("gan", "bro", True), ("otw", "on the way", False),
        ("bang", "bro (older male)", False), ("mabar", "main bareng: play together", True),
    ])
    # ---------- Tagalo / Filipinas ----------
    + _entries("tl", "PH", [
        ("lods / idol", "bro, idol", True), ("pre / pare", "buddy", False), ("charot / char", "just kidding", True),
        ("sana all", "I wish I had that too", True), ("petmalu", "awesome", True), ("hahaha", "laughter", False),
    ])
    # ---------- Polaco ----------
    + _entries("pl", "PL", [
        ("nwm", "nie wiem: I don't know", True), ("spk", "spoko: okay", True), ("elo", "hi", False), ("xD", "laughter", False),
    ])
    # ---------- Hindi en letras latinas (Hinglish), muy común en servidores de India ----------
    + _entries("hi", "IN", [
        ("bhai / bro", "bro", False), ("yaar / yar", "dude, friend", True), ("kya / kia", "what", True),
        ("nahi / nhi / nai", "no / not", True), ("hai / h", "is / are", False),
        ("kidhar / kidher / kaha / kahan", "where", True), ("kro / karo / kr", "do (imperative)", True),
        ("isse / isko / usko", "to him/her, with him/her", True), ("baat", "talk, conversation", True),
        ("abey / abe", "hey you (rude)", True), ("bola / bol", "said / say", False),
        ("dekh / dekho / dekh rhi / dekh raha", "look / watching", True), ("aaj kal", "these days", True),
        ("acha / accha", "okay / I see", True), ("haan / ha", "yes", False), ("kaise / kese", "how", True),
        ("kyu / kyun", "why", True), ("bhi", "also, too", True), ("mat", "don't", False),
        ("sahi", "right, cool", True), ("pagal", "crazy", True), ("chup", "shut up", True),
        ("chal / chalo", "come on, let's go / okay", True), ("mujhe / mera / meri", "me / my", True),
        ("tu / tum / aap", "you (informal / neutral / respectful)", False), ("samjha", "got it?", True),
        ("gand / bc / mc", "strong vulgar insults", True), ("op bolte", "legendary, awesome", True),
    ])
    # ---------- Asia ----------
    + _entries("ko", "KR", [("ㅋㅋㅋ", "laughter", True), ("ㅠㅠ / ㅜㅜ", "crying, sad", True), ("ㄱㄱ", "let's go", True)])
    + _entries("ja", "JP", [("www / 草", "laughter", True), ("おつ", "good job / thanks for playing", True)])
    + _entries("zh", "CN", [("哈哈", "laughter", True), ("666", "awesome, well played", False), ("88", "bye", False)])
)

# ---------- detección local ----------
def _fold(text: str) -> str:
    """Devuelve el texto en minúsculas y sin acentos (güey -> guey), para comparar jerga escrita de formas muy
    variadas.
    """
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


_HINTS: dict[str, list[Slang]] = {}
for _entry in LEXICON:
    if _entry.hint:
        for _variant in _entry.term.split("/"):
            _HINTS.setdefault(_fold(_variant.strip()), []).append(_entry)

# Risas: se reconocen por patrón y se traducen localmente, sin llamar a Claude.
_LAUGH_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^k{3,}$"), "pt", "BR"),
    (re.compile(r"^(?:rs){2,}$"), "pt", "BR"),
    (re.compile(r"^(?:wk){2,}w?$"), "id", "ID"),
    (re.compile(r"^(?:[jJ][aeiAEI]){2,}[jJ]?$"), "es", ""),
    (re.compile(r"^(?:mdr|ptdr)$"), "fr", "FR"),
    (re.compile(r"^ㅋ{2,}$"), "ko", "KR"),
    (re.compile(r"^w{3,}$|^草+$"), "ja", "JP"),
    (re.compile(r"^(?:哈){2,}$"), "zh", "CN"),
    (re.compile(r"^(?:[хx][аa]){2,}$|^(?:а?ха){2,}$"), "ru", "RU"),
    (re.compile(r"^(?:[ah]*h[ah]*a[ah]*)$"), "en", ""),  # haha, ahaha, hahahah
    (re.compile(r"^(?:lol|lmao|lmfao|rofl)$"), "en", ""),
    (re.compile(r"^x+d+$"), "", ""),
]
LAUGH_BY_LANG = {
    "es": "jajaja", "pt": "kkkkk", "en": "hahaha", "fr": "mdr", "id": "wkwkwk", "ko": "ㅋㅋㅋ",
    "ja": "www", "zh": "哈哈哈", "ru": "хахаха", "de": "hahaha", "it": "ahahah", "tr": "hahaha", "pl": "hahaha",
    "tl": "hahaha", "vi": "hahaha", "th": "555", "ar": "ههههه", "nl": "hahaha", "hi": "hahaha",
}
_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text)


def scan(text: str) -> list[Slang]:
    """Devuelve los términos de jerga que identifican idioma o país presentes en el mensaje."""
    found: list[Slang] = []
    words = [_fold(t) for t in tokens(text)]
    for i, word in enumerate(words):
        found.extend(_HINTS.get(word, ()))
        if i + 1 < len(words):  # expresiones de dos palabras ("no mames", "sana all")
            found.extend(_HINTS.get(f"{word} {words[i + 1]}", ()))
    for token in tokens(text):
        for pattern, lang, region in _LAUGH_PATTERNS:
            if lang and pattern.match(token.casefold()):
                found.append(Slang(token, lang, "laughter", region, True))
                break
    return found


def laugh_only(text: str) -> bool:
    """Indica si el mensaje está formado solo por risas (kkkk, jajaja, wkwk, ㅋㅋㅋ, mdr...)."""
    words = tokens(text)
    return bool(words) and all(any(p.match(w.casefold()) for p, _, _ in _LAUGH_PATTERNS) for w in words)


def laugh_for(lang: str) -> str:
    return LAUGH_BY_LANG.get(lang, "hahaha")


def prompt_reference() -> str:
    """Devuelve un glosario compacto para el system prompt, agrupado por idioma o país."""
    from .languages import LANGUAGES

    groups: dict[tuple[str, str], list[str]] = {}
    for entry in LEXICON:
        groups.setdefault((entry.lang, entry.region), []).append(f"{entry.term} = {entry.meaning}")
    lines = []
    for (lang, region), items in groups.items():
        label = LANGUAGES.get(lang, lang) + (f" ({region})" if region else "")
        if (lang, region) == ("en", ""):
            label = "Roblox / gamer (all languages)"
        lines.append(f"{label}: " + "; ".join(items))
    return "\n".join(lines)
