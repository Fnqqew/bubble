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


def lexical_share(text: str, lang: str) -> float:
    """Fracción de las palabras del mensaje que son comunes en `lang` (0 si no hay lista para ese idioma)."""
    words = COMMON_WORDS.get(lang)
    tokens = _WORD_TOKEN.findall(text)
    if not words or not tokens:
        return 0.0
    return sum(bool(_chat_forms(t) & words) for t in tokens) / len(tokens)


def words_in(text: str) -> list[str]:
    return _WORD_TOKEN.findall(text)


def known_anywhere(token: str) -> bool:
    """La palabra está en alguna de las listas (o es universal)."""
    forms = _chat_forms(token)
    return bool(forms & UNIVERSAL_TOKENS) or any(forms & words for words in COMMON_WORDS.values())


def foreign_words(text: str, lang: str) -> list[str]:
    """Palabras típicas de OTRO idioma que no existen en `lang` (ej. "você" o "the" en un mensaje en español)."""
    own = COMMON_WORDS.get(lang, set())
    tokens = {_fold(t) for t in _WORD_TOKEN.findall(text)}
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
    """True si el mensaje no tiene palabras o solo tiene abreviaturas e interjecciones universales ("Ahhhh")."""
    return all(_chat_forms(w) & UNIVERSAL_TOKENS for w in _WORD.findall(text))


@dataclass(frozen=True)
class Detection:
    lang: str
    confidence: float
    # Confianza del segundo idioma más probable: sirve para medir qué tan claro es el ganador.
    runner_up: float = 0.0

    def is_confident(self, min_confidence: float, min_lead: float = 2.5, floor: float = 0.3) -> bool:
        """Seguro si supera el umbral, o si le saca amplia ventaja al segundo idioma."""
        if self.confidence >= min_confidence:
            return True
        return self.confidence >= floor and self.confidence >= min_lead * self.runner_up


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

            langs = [Language.from_iso_code_639_1(getattr(IsoCode639_1, c.upper())) for c in self._codes]
            self._detector = LanguageDetectorBuilder.from_languages(*langs).with_preloaded_language_models().build()

    def detect(self, text: str) -> Detection | None:
        self.load()
        values = self._detector.compute_language_confidence_values(text)
        if not values:
            return None
        top = values[0]
        runner_up = float(values[1].value) if len(values) > 1 else 0.0
        return Detection(top.language.iso_code_639_1.name.lower(), float(top.value), runner_up)
