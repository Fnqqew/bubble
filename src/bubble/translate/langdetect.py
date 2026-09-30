"""Detección local de idioma (sin red) para no gastar traducciones innecesarias."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from .languages import LANGUAGES

# Mensajes que se entienden en cualquier idioma: no hace falta traducirlos.
UNIVERSAL_TOKENS = {
    "gg", "ggs", "ggwp", "gl", "hf", "glhf", "wp", "ez", "lol", "lmao", "xd", "xdd", "xddd",
    "ok", "okay", "k", "kk", "afk", "brb", "omg", "oof", "rip", "gg!", "w", "l",
    # Interjecciones que se entienden igual en cualquier idioma ("Ahhhh", "aja", "nah", "nop", "mmm").
    "ah", "aa", "oh", "eh", "uh", "ay", "hm", "hmm", "mm", "mmm", "aja", "aha", "ajá", "nah", "nop", "nope", "yep",
    "wow", "uff", "uf", "bruh", "sh", "shh", "ups", "oops", "q",
}
# Palabras de juego que se usan igual en todos los idiomas ("vamos a hacer pvp", "tengo lag", "bora farmar"). No dicen
# nada del idioma del mensaje: antes un "pvp" o un "lag" hacía que un mensaje en español pareciera inglés (y se
# traducía).
GAMING_WORDS = set("""
    pvp pve pvpear lag laggy lagueado lagueo lagea lageando noob noobs nub nubs newbie hacker hackers hack
    hacks hacking cheater cheaters cheat cheats bug bugs bugueado buguea bugeado glitch glitches op nerf nerfeo
    nerfearon
    buff buffs bufeo loot looteo lootear farm farmear farmeando farmar farming grind grindear grindeando spawn spawns
    respawn spawnear spawnkill boss bosses item items itens skin skins lvl level levels xp exp hp mp dps tank
    healer raid raids quest quests drop drops dropea craft crafting crafteo server servers lobby admin admins
    mod mods owner ban baneo baneado kick kickeo tp tpa tpear teleport obby obbies robux rbx avatar emote emotes
    gamepass gamepasses pet pets trade trades tradeo tradear tradeando trading rank ranked clan squad team teams crew
    party carry carrear combo combos speedrun camper campero camping sniper stream streamer youtuber tiktoker ping fps
    bot bots npc npcs checkpoint stage stages round rounds match lobby map maps game games gamer online offline chat vc
    mic discord user username link update updates shop coins gems tycoon simulator roleplay rp sus imposter impostor
    crush random cringe based troll trolleo trollear tryhard sweat sweaty clutch rush rushear main mains smurf ult
    ultimate cooldown cd aggro kill kills killstreak headshot hs ks respawn afk vip premium event codes code
""".split())
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _fold(text: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


# Palabras muy comunes (sin acentos) por idioma. Sirven para saltear al instante los mensajes cortos que ya
# están en tu idioma ("hola", "dale, voy", "todo bien?"), donde el detector estadístico no está seguro.
COMMON_WORDS: dict[str, set[str]] = {
    "es": set("""
        el la los las un una unos unas de del al a y o u pero que quien quienes como cuando donde adonde por
        para con sin sobre entre hasta desde mi mis tu tus su sus yo vos tu usted ustedes nosotros ellos ellas
        ella me te se le les lo nos es son soy sos eres esta estas estoy estamos estan era fue ser estar hay
        hace hacer hago tengo tenes tienes tiene tienen quiero queres quieres quiere puedo podes puedes puede
        vamos voy va van ven veni vení dale si no nada algo alguien todo todos toda todas muy mas menos bien
        mal bueno buena buenas buenos malo mala hola chau adios gracias porfa favor jaja jajaja jajajaja jeje
        che boludo bolu re posta joya genial ahora ya aca aqui alla ahi eso esto esa ese este esos esas cuanto
        cual porque entonces tambien nunca siempre otra otro otros juego juegos jugar jugando juga amigo amiga
        amigos pibe pibes mina gente bro anda andas dame decime dime mira espera bancame nose se cosa cosas
        casa tiempo hoy manana ayer noche dia equipo mapa donde onda sabes sabe se ves ver vi mismo mucho mucha
        poco poca tanto nadie ningun ninguno alguno algun igual claro obvio okey vale oye wey pues bueno perdon
        disculpa listo espera esperen vengan vayan corran vamo aca tipo medio capo crack loco loca pa pal
        q k xq pq porq tmb tb tbm x d xfa pls sip nel simon chido neta wey guey pedo alv ptm vdd ntp msj bn ns
        tal vez feliz felices viaje viajes ano anos visito visita hembra macho gato gata perro peso pesos plata
        dinero comprar compra vender cuenta hermano hermana mama papa novio novia chica chico nena nene sisi
        nono jsjs jsjsjs jajs jaj jsj bueh buah ahre alta mala onda qonda ke kiero xk yaya yayaya
        pasame esperame ayudame fijate quedate seguime sigueme toma agarra atras izquierda derecha cuidado
    """.split()),
    "pt": set("""
        o a os as um uma de do da dos das no na nos nas em e ou mas que quem como quando onde por para pra pro
        com sem eu voce vc vcs ele ela eles elas nos meu minha meus minhas teu tua seu sua e sou somos sao
        esta estou estamos estao era foi ser estar tem tenho temos quero quer posso pode vamos vou vai nao sim
        muito muita bom boa obrigado obrigada valeu vlw blz beleza oi tchau agora aqui isso esse essa isto
        gente jogo jogar jogando cara mano galera ne fala falou kkkk kkk rs demais legal tudo nada ai la cade
        tambem entao sei tipo aki ta to tô
    """.split()),
    "en": set("""
        the a an and or but i you he she it we they me my your his her its our their is are was were be been
        am do does did dont have has had will would can could should not no yes yeah yea ok okay what why how
        when where who which this that these those there here to of in on at for with from by about up down
        out get got go going come want wanna gonna gotta know think like just really very so too also now then
        some any all one two lol lmao bro dude guys man pls please thanks thx ty sorry hi hello hey bye game
        play playing trade im u ur r its lets let see look help need
        wait same stop run follow here coming came give take gave took tell told say said ask where whats
        whos wheres hows thats theres dont cant wont didnt doesnt isnt arent wasnt ive youre hes shes were theyre
        yall yo sup nice cool good bad best worst better more most much many never always again still already
        maybe sure fine yep nope nah idk ikr imo tbh brb omw rn wtf smh fr ngl btw pog lets go gonna because
        cause if than then back over after before first last next new old big small little right left fast slow
        friend friends team win won lose lost kill killed dead die died spawn buy sell free money give me carry
    """.split()),
}
# Palabras que delatan cada idioma: comunes en él pero que no existen en los otros de la lista.
_DISTINCTIVE = {
    lang: words - set().union(*(other for other_lang, other in COMMON_WORDS.items() if other_lang != lang))
    for lang, words in COMMON_WORDS.items()
}
_WORD_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)


def _chat_forms(token: str) -> set[str]:
    """Cómo puede estar escrita una palabra en el chat: "Siii" es "si", "Ahhhh" es "ah", "felicesxd" es "felices"."""
    folded = _fold(token)
    forms = {folded}
    collapsed = re.sub(r"(.)\1+", r"\1", folded)
    if len(collapsed) >= 2:  # "kkkkkk" es una risa, no la letra "k"
        forms.add(collapsed)
    if folded.endswith("xd") and len(folded) > 4:
        forms |= _chat_forms(folded[:-2])
    return forms


def is_gaming(token: str) -> bool:
    return bool(_chat_forms(token) & GAMING_WORDS)


def lexical_share(text: str, lang: str) -> float:
    """Fracción de las palabras del mensaje que son comunes en `lang` (0 si no hay lista para ese idioma). Las palabras
    de juego ("pvp", "lag") no cuentan para ningún lado, siempre que quede con qué decidir: con una sola palabra más
    ("trade me": "me" también es español) sí cuentan, como palabras de otro idioma (antes se tomaba como español)."""
    words = COMMON_WORDS.get(lang)
    every = _WORD_TOKEN.findall(text)
    tokens = [t for t in every if not is_gaming(t) or _chat_forms(t) & (words or set())]
    if len(tokens) < 2 and len(tokens) < len(every) and not (tokens and _chat_forms(tokens[0]) & _DISTINCTIVE.get(
            lang, set())):
        tokens = every  # queda muy poco (y nada exclusivo de ese idioma): las de juego cuentan
    if not words or not tokens:
        return 0.0
    return sum(bool(_chat_forms(t) & words) for t in tokens) / len(tokens)


def native_by_words(text: str, lang: str, share: float = 0.6) -> bool:
    """¿Las palabras dicen que ya está en `lang`? Casi todas comunes en ese idioma Y (alguna exclusiva de él, o ninguna
    exclusiva de otro). Sin esto "carry me pls" parecía español ("me" y "pls" también se usan en español)."""
    if lexical_share(text, lang) < share:
        return False
    forms = [_chat_forms(token) for token in _WORD_TOKEN.findall(text)]
    if any(form & _DISTINCTIVE.get(lang, set()) for form in forms):
        return True
    others = set().union(*(words for other, words in _DISTINCTIVE.items() if other != lang))
    return not any(form & others for form in forms)


def without_gaming(text: str) -> str:
    """El mensaje sin las palabras de juego, para detectar el idioma de lo demás ("hagamos pvp en el lobby" → "hagamos
    en el"). Si queda menos de dos palabras, el mensaje entero (con una sola, el detector adivina)."""
    kept = [t for t in text.split() if not all(is_gaming(w) for w in _WORD_TOKEN.findall(t) or ["x"])]
    return " ".join(kept) if len(_WORD_TOKEN.findall(" ".join(kept))) >= 2 else text


def words_in(text: str) -> list[str]:
    return _WORD_TOKEN.findall(text)


# Palabras sueltas muy comunes de los otros idiomas (gracias, hola, sí, por favor, esperá, ayuda…). Una palabra sola
# que no está en ninguna lista se toma como un nombre o un error de tipeo y no se traduce: sin esto, "merci", "danke",
# "grazie" o "salamat" quedaban sin traducir.
OTHER_WORDS: dict[str, set[str]] = {
    "fr": set("merci bonjour salut bonsoir oui non stp svp pardon desole attends viens aide ami bravo mdr ptdr wesh "
              "frere allez pourquoi comment quoi ouais".split()),
    "de": set("danke hallo nein bitte tschuss hilfe warte komm freund alter digga warum genau doch".split()),
    "it": set("grazie ciao prego scusa aiuto aspetta vieni amico andiamo perche raga boh bene".split()),
    "nl": set("dankje bedankt hoi doei nee alsjeblieft wacht vriend waarom hoe".split()),
    "tl": set("salamat oo sige tara tulong hintay kaibigan paano bakit pre lodi petmalu ingat po".split()),
    "id": set("makasih terimakasih iya tidak gak nggak tolong tunggu ayo teman kenapa gimana wkwk anjir".split()),
    "pl": set("dzieki dziekuje czesc tak nie prosze pomocy czekaj chodz dobra czemu".split()),
    "tr": set("tesekkurler sagol merhaba selam evet hayir lutfen yardim bekle gel kanka tamam neden".split()),
    "vi": set("cam on xin chao vang khong giup doi ban".split()),
    "sv": set("tack hej nej hjalp vanta kompis varfor".split()),
    "no": set("takk hei nei hjelp vent venn hvorfor".split()),
    "da": set("tak hej nej hjaelp vent ven hvorfor".split()),
    "fi": set("kiitos moi hei kylla ei apua odota kaveri miksi".split()),
    "cs": set("diky dekuji ahoj ano pomoc pockej kamarad proc".split()),
    "sk": set("dakujem ahoj ano nie pomoc pockaj kamarat preco".split()),
    "hu": set("koszi koszonom szia igen nem segits varj haver miert".split()),
    "ro": set("mersi multumesc salut da nu ajutor asteapta prieten".split()),
    "hr": set("hvala bok da ne pomoc cekaj prijatelj zasto".split()),
    "sl": set("hvala zivjo ja ne pomoc pocakaj prijatelj zakaj".split()),
    "lt": set("aciu labas taip ne padek palauk draugas kodel".split()),
    "lv": set("paldies sveiki ja ne palidzi pagaidi draugs kapec".split()),
    "et": set("aitah tere jah ei appi oota sober miks".split()),
    "ca": set("gracies hola si adeu ajuda espera amic perque".split()),
    "eu": set("eskerrik kaixo bai ez lagundu itxaron lagun zergatik".split()),
    "cy": set("diolch helo ie na help aros ffrind pam".split()),
    "is": set("takk hallo ja nei hjalp bidu vinur hvers".split()),
    "sq": set("faleminderit pershendetje po jo ndihme prit shok pse".split()),
    "af": set("dankie hallo ja nee help wag vriend hoekom".split()),
    "sw": set("asante habari ndiyo hapana msaada subiri rafiki kwa".split()),
    "az": set("sagol salam beli xeyr komek gozle dost niye".split()),
    "ms": set("terima kasih hai ya tidak tolong tunggu kawan kenapa".split()),
}


# La escritura de cada idioma (los demás, letras latinas): una palabra sola en otra escritura que la tuya no es tuya.
SCRIPTS = {"ru": "cyrillic", "uk": "cyrillic", "bg": "cyrillic", "mk": "cyrillic", "be": "cyrillic", "kk": "cyrillic",
           "sr": "latin", "el": "greek", "he": "hebrew", "ar": "arabic", "fa": "arabic", "ur": "arabic", "hi": "indic",
           "mr": "indic", "bn": "indic", "te": "indic", "ta": "indic", "gu": "indic", "pa": "indic", "th": "thai",
           "ka": "georgian", "hy": "armenian", "ja": "cjk", "zh": "cjk", "ko": "hangul"}
_SCRIPT_RANGES = [("cyrillic", 0x0400, 0x052F), ("greek", 0x0370, 0x03FF), ("hebrew", 0x0590, 0x05FF),
                  ("arabic", 0x0600, 0x06FF), ("armenian", 0x0530, 0x058F), ("indic", 0x0900, 0x0DFF),
                  ("thai", 0x0E00, 0x0E7F), ("georgian", 0x10A0, 0x10FF), ("hangul", 0xAC00, 0xD7AF),
                  ("cjk", 0x3040, 0x9FFF)]


def script_of(text: str) -> str:
    """La escritura en la que está `text` ("latin", "cyrillic"…): la de la mayoría de sus letras."""
    counts: dict[str, int] = {}
    for char in text:
        if not char.isalpha():
            continue
        code = ord(char)
        name = next((script for script, low, high in _SCRIPT_RANGES if low <= code <= high), "latin")
        counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else "latin"


def known_anywhere(token: str) -> bool:
    """La palabra está en alguna de las listas (o es universal)."""
    forms = _chat_forms(token)
    return bool(forms & UNIVERSAL_TOKENS) or any(forms & words for words in COMMON_WORDS.values()) or any(
        forms & words for words in OTHER_WORDS.values())


def foreign_words(text: str, lang: str) -> list[str]:
    """Palabras típicas de OTRO idioma que no existen en `lang` (ej. "você" o "the" en un mensaje en español)."""
    own = COMMON_WORDS.get(lang, set())
    tokens = {_fold(t) for t in _WORD_TOKEN.findall(text) if not is_gaming(t)}
    return sorted(
        t for other_lang, words in _DISTINCTIVE.items() if other_lang != lang for t in tokens & words if t not in own
    )


_FILTER_CHARS = set("*#")


def is_filtered(text: str, threshold: float = 0.5) -> bool:
    """True si Roblox tapó la mayor parte del mensaje con su filtro (**** o ####): está prácticamente borrado."""
    visible = [c for c in text if not c.isspace()]
    if not visible:
        return False
    return sum(c in _FILTER_CHARS for c in visible) / len(visible) >= threshold


def is_universal(text: str) -> bool:
    """True si el mensaje no tiene palabras o solo tiene abreviaturas, interjecciones universales ("Ahhhh") y
    palabras de juego que se entienden en cualquier idioma ("gg noob", "pvp?", "lag")."""
    return all(_chat_forms(w) & (UNIVERSAL_TOKENS | GAMING_WORDS) for w in _WORD.findall(text))


@dataclass(frozen=True)
class Detection:
    lang: str
    confidence: float
    # Confianza del segundo idioma más probable: sirve para medir qué tan claro es el ganador.
    runner_up: float = 0.0
    # Los más probables con su confianza (para saber si un idioma dado quedó casi empatado arriba).
    scores: tuple[tuple[str, float], ...] = ()

    def score(self, lang: str) -> float:
        return next((value for code, value in self.scores if code == lang), 0.0)

    def is_confident(self, min_confidence: float, min_lead: float = 2.5, floor: float = 0.3) -> bool:
        """Seguro si supera el umbral, o si le saca amplia ventaja al segundo idioma."""
        if self.confidence >= min_confidence:
            return True
        return self.confidence >= floor and self.confidence >= min_lead * self.runner_up


# Códigos que lingua llama distinto (el noruego escrito es el bokmål).
_TO_LINGUA = {"no": "nb"}
_FROM_LINGUA = {lingua: ours for ours, lingua in _TO_LINGUA.items()}


class LanguageDetector:
    """Envuelve lingua; el modelo se carga en segundo plano la primera vez."""

    def __init__(self, languages: list[str] | None = None) -> None:
        self._codes = languages or list(LANGUAGES)
        self._detector = None
        self._lock = threading.Lock()

    def load(self) -> None:
        with self._lock:
            if self._detector is not None:
                return
            from lingua import IsoCode639_1, Language, LanguageDetectorBuilder

            langs = [Language.from_iso_code_639_1(getattr(IsoCode639_1, _TO_LINGUA.get(c, c).upper()))
                     for c in self._codes]
            self._detector = LanguageDetectorBuilder.from_languages(*langs).with_preloaded_language_models().build()

    def detect(self, text: str) -> Detection | None:
        self.load()
        values = self._detector.compute_language_confidence_values(text)
        if not values:
            return None
        top = values[0]
        runner_up = float(values[1].value) if len(values) > 1 else 0.0
        scores = tuple((_FROM_LINGUA.get(code, code), float(value.value)) for value in values[:8]
                       for code in [value.language.iso_code_639_1.name.lower()])
        return Detection(scores[0][0], float(top.value), runner_up, scores)
