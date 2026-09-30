"""Convierte las líneas del OCR en mensajes de chat y detecta cuáles son nuevos.

El OCR sobre el chat de Roblox comete errores típicos que aquí se toleran: - Íconos al principio de la línea (banderas,
insignias) leídos como basura: "-", "["]", "•". - Los dos puntos posteriores al nombre leídos como ".'", "'.", "," o
";". - Nombres con letras cambiadas ("CCloverx3", "claverxg" en vez de "cloverx3").
"""

from __future__ import annotations

import itertools
import re
import time
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Callable

from ..translate.base import ChatLine
from .ocr import OcrRow

# Basura inicial y etiquetas opcionales: "[Team]", "{To X}", "[🇮🇳]" leído como "[]" o "[\"]", o la bandera leída sin el
# corchete de cierre ("[S Juan: ...").
_PREFIX = r"^[^\w\[{(]{0,4}(?:[\[{(][^\]})]{0,24}[\]})][^\w\[{(]{0,3}|[\[{(][^\s\]})]{0,3}\s+)*"
# "Nombre: texto" con los dos puntos (o punto y coma) bien leídos.
_NAME = r"\w[\w.]{1,23}\w"  # 3 a 25 caracteres, sin terminar en punto (suele ser un ":" mal leído)
_STRONG = re.compile(_PREFIX + rf"\[?(?P<name>{_NAME}(?: \w[\w.]{{0,14}}\w)?)\]?\s*[:;]\s*(?P<text>\S.*)$")
# Dos puntos mal leídos por el OCR (".'", "'.", ","): solo se aceptan con nombres que parecen de usuario.
_WEAK = re.compile(_PREFIX + rf"\[?(?P<name>{_NAME})\]?[.,'`’\"]{{1,2}}[:;.,'`’]?\s+(?P<text>\S.*)$")
_SYSTEM_NAMES = ("system", "sistema", "server", "servidor", "announcement", "anuncio", "notice", "info")
# Frases típicas de avisos automáticos de los juegos, aunque el OCR haya perdido la etiqueta [SYSTEM].
_SYSTEM_CONTENT = re.compile(
    r"\(\+\d[\d,.]*\)"                                   # puntos ganados: (+25), (+3,750)
    r"|\bhas (?:added|joined|left|been|donated|earned|unlocked|received|won|reached|purchased)\b"
    r"|\b(?:joined|left) the (?:game|server)\b"
    r"|\bdonated\s+\S*\s*\d"                             # "X donated 10 to Y"
    r"|\b(?:se unió|salió) (?:al|del) (?:juego|servidor)\b",
    re.IGNORECASE,
)
_TRAILING_JUNK = " •·—–-|_~"
# Texto de la barra de escritura del chat de Roblox ("To chat click here or press / key") en varios idiomas. No es un
# mensaje y se ignora si la zona del chat lo incluye.
_INPUT_BAR = re.compile(
    r"to chat[, ]+click here|click here or press|press\s*\S{0,2}\s*key|para chatear|haz clic aqu[ií]|"
    r"presiona la tecla|pulsa la tecla|para conversar|clique aqui|pressione a tecla",
    re.IGNORECASE,
)
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)


def _is_system_name(name: str) -> bool:
    """[SYSTEM] aunque el OCR lo lea mal ("SYS TEM", "SYSTEMI", "TEM")."""
    key = re.sub(r"[^a-z]", "", _fold(name))
    if not key:
        return False
    if key in {"tem", "stem", "ystem"} or (key.startswith("sys") and len(key) <= 9):
        return True  # fragmentos de "[SYSTEM]" mal leído
    return any(
        abs(len(key) - len(word)) <= 2 and SequenceMatcher(None, key, word).ratio() >= 0.75 for word in _SYSTEM_NAMES
    )


_INNER_PUNCT = re.compile(r"[^\W\d_][.,;:!?$#%&*|/\\][^\W\d_]")  # "dom.te", "bg$d": signo entre letras
_CASE_FLIPS = re.compile(r"[a-zà-ÿ][A-ZÀ-Þ]")
_VOWELS = set("aeiouyáéíóúàèìòùâêîôûäëïöüãõ")


def _odd_word(word: str) -> bool:
    core = word.strip("¡¿!?.,;:\"'()[]«»“”…-")
    if len(core) < 3:
        return False
    if _INNER_PUNCT.search(core):
        return True
    if len(_CASE_FLIPS.findall(core)) >= 2:  # "jiPCgmôtTt" (un "xXShadowXx" en el nombre no cuenta aquí)
        return True
    letters = [c for c in core.lower() if c.isalpha()]
    # Muchas letras distintas y ninguna vocal ("xkcdtrwq"); "kkkkk" o "wkwkwk" son risas, no basura.
    return len(set(letters)) >= 4 and all(c.isascii() for c in letters) and not _VOWELS & set(letters)


def looks_garbled(text: str) -> bool:
    """Indica si el OCR leyó mal el mensaje, algo frecuente en su primer instante en pantalla, mientras aparece. Se
    considera basura si la mitad o más de las palabras tienen signos intermedios o mayúsculas alternadas.
    """
    words = [word for word in text.split() if any(c.isalpha() for c in word)]
    if not words:
        return False
    odd = sum(_odd_word(word) for word in words)
    return odd >= 1 and odd * 2 >= len(words)


def is_system_message(name: str, text: str, raw: str = "") -> bool:
    if _is_system_name(name) or _SYSTEM_CONTENT.search(text):
        return True
    tag = re.match(r"^\W{0,3}\[([^\]]{2,12})\]", raw)  # "[SYSTEM]" al inicio de la línea
    return bool(tag and _is_system_name(tag.group(1)))


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def _name_key(name: str) -> str:
    return re.sub(r"[^0-9a-z]", "", _fold(name))


def _text_key(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", _fold(text)).split())


def _looks_like_username(name: str) -> bool:
    return any(c.isdigit() or c == "_" for c in name)


@dataclass
class ChatItem:
    """Mensaje del chat con su ubicación en la captura (para dibujar la traducción encima)."""

    speaker: str
    text: str
    rows: list[OcrRow] = field(default_factory=list)
    text_left: float = 0.0  # x donde empieza el texto (después de "Nombre:") en la primera línea
    kind: str = "player"  # "player" | "system" (avisos automáticos del juego: no se traducen)
    # Mensaje tal como se leyó la primera vez (el que se tradujo), aunque ahora el OCR lo lea distinto.
    origin: ChatLine | None = None
    # Apareció solo en la lectura de respaldo (fondo del chat desvanecido) y puede ser texto mal leído. Un mensaje nuevo
    # así se confirma con otra lectura antes de traducirlo.
    uncertain: bool = False
    uid: int = 0  # identidad estable del mensaje en el seguidor del chat

    @property
    def line(self) -> ChatLine:
        return ChatLine(self.speaker, self.text)

    @property
    def top(self) -> float:
        return min(r.top for r in self.rows)

    @property
    def bottom(self) -> float:
        return max(r.bottom for r in self.rows)

    @property
    def right(self) -> float:
        return max(r.right for r in self.rows)


def _x_at(row: OcrRow, char_index: int) -> float:
    """Coordenada x aproximada del carácter `char_index` del texto armado con las palabras de la fila."""
    pos = 0
    for word in row.words:
        end = pos + len(word.text)
        if char_index <= end:
            frac = (char_index - pos) / max(1, len(word.text))
            return word.left + max(0.0, min(1.0, frac)) * word.width
        pos = end + 1
    return row.words[-1].right if row.words else row.right


def _text_start(row: OcrRow, name: str, body: str) -> float:
    """Posición donde empieza el mensaje en la fila: solo se cubre el texto y el nombre queda visible."""
    joined = " ".join(w.text for w in row.words) if row.words else row.text
    folded = joined.casefold()
    name_at = folded.find(name.casefold())
    search_from = name_at + len(name) if name_at >= 0 else 0
    body_at = folded.find(body[:6].casefold(), search_from) if body else -1
    if body_at < 0:
        body_at = min(len(joined), search_from + 1)
    if not row.words:
        return row.left + row.width * body_at / max(1, len(joined))
    return _x_at(row, body_at)


def parse_chat(
    rows: list[OcrRow], is_known_name: Callable[[str], bool] | None = None, frame_width: float = 0
) -> list[ChatLine]:
    """Mensajes de jugadores (sin los avisos del sistema)."""
    return [item.line for item in parse_chat_items(rows, is_known_name, frame_width) if item.kind == "player"]


CONTINUATION_INDENT = 14  # px: una continuación empieza en el borde (la bandera de un mensaje desplaza ~35 px)
FAR_FROM_COLUMN = 150  # px: lo que empieza más a la derecha que esto respecto del borde izquierdo no es chat


def column_rows(rows: list[OcrRow]) -> list[OcrRow]:
    """Solo las líneas de la columna del chat. Lo que empieza lejos a la derecha (la burbuja de otro jugador, un nombre
    sobre una cabeza, un cartel del juego) se pegaría al mensaje o se tomaría como su continuación.
    """
    starts = [row.left for row in rows if _STRONG.match(row.text.strip())]
    if not starts:
        return rows
    column = min(starts)
    return [row for row in rows if row.left - column <= FAR_FROM_COLUMN]


_ICON_JUNK = re.compile(r"^\d{1,4}$")


def _drop_icon_junk(name: str, body: str) -> tuple[str, str]:
    """La bandera o el ícono previo al nombre, leído como números: "1151 melofruits" o "313: smegladon40: hola"."""
    first, _, rest = name.partition(" ")
    if rest and _ICON_JUNK.match(first):
        name = rest
    if _ICON_JUNK.match(name):
        inner = _STRONG.match(body)
        if inner:
            return inner.group("name").strip(), inner.group("text").strip(_TRAILING_JUNK)
    return name, body


def parse_chat_items(
    rows: list[OcrRow],
    is_known_name: Callable[[str], bool] | None = None,
    frame_width: float = 0,
    wrap_right: float = 0,
    frame_height: float = 0,
) -> list[ChatItem]:
    """Arma los mensajes 'Nombre: texto' con su ubicación.

    Una línea sin nombre solo continúa el mensaje anterior si este llegaba al borde derecho del chat
    (`frame_width`), es decir, si Roblox lo partió en dos líneas. En caso contrario es basura del OCR y se descarta,
    porque pegarla al mensaje anterior producía mensajes mezclados y traducciones sin sentido.
    """
    is_known_name = is_known_name or (lambda _name: False)
    rows = column_rows(rows)
    # La continuación de un mensaje largo empieza en el borde izquierdo del chat; un mensaje nuevo, después de la
    # bandera. Una línea desplazada hacia la derecha es un mensaje al que el OCR le perdió los ":", no una continuación.
    edge = min((row.left for row in rows if _LETTER.search(row.text)), default=0.0)
    # Una línea "llena" llega casi al borde donde Roblox corta el texto. Ese borde (`wrap_right`) se aprende
    # de los mensajes largos vistos; sin ese dato se usa el ancho calibrado.
    widest = wrap_right or frame_width
    messages: list[ChatItem] = []
    last_row: OcrRow | None = None
    for row in rows:
        text = row.text.strip()
        bar = _INPUT_BAR.search(text)
        if bar:
            # Barra de escritura: se corta ahí y nada de lo que siga continúa un mensaje.
            text = text[:bar.start()].strip()
            if not text:
                last_row = None
                continue
        if len(_LETTER.findall(text)) < 1 or row.top <= 1:
            # Sin letras, o cortada contra el borde superior (el OCR la leería distinto en cada captura).
            last_row = None
            continue
        match = _STRONG.match(text)
        if not match:
            weak = _WEAK.match(text)
            if weak and (_looks_like_username(weak.group("name")) or is_known_name(weak.group("name"))):
                match = weak
        if match:
            name, body = _drop_icon_junk(match.group("name").strip(), match.group("text").strip(_TRAILING_JUNK))
            if not _LETTER.search(body + name):
                last_row = None
                continue
            kind = "system" if is_system_message(name, body, text) else "player"
            messages.append(ChatItem(name, body, [row], _text_start(row, name, body), kind))
            last_row = row
            continue
        wrapped = last_row is not None and widest and last_row.right >= 0.85 * widest
        if wrapped and not text.startswith(("[", "{")) and row.left <= edge + CONTINUATION_INDENT:
            item = messages[-1]
            item.text = f"{item.text} {text.strip(_TRAILING_JUNK)}".strip()
            item.rows.append(row)
            if item.kind == "player" and _SYSTEM_CONTENT.search(item.text):
                item.kind = "system"
            last_row = row
        else:
            last_row = None
    items = [item for item in messages if item.text]
    if items and widest and frame_height:
        last = items[-1].rows[-1]
        if last.right >= 0.85 * widest and last.bottom >= frame_height - 1.3 * last.height:
            # Llega al borde inferior y ocupa todo el ancho: su segunda línea puede haber quedado fuera. Se confirma en
            # otra captura (el chat sube o se lee completo) en lugar de traducir solo la mitad.
            items[-1].uncertain = True
    return items


class SpamFilter:
    """Detecta spam para no traducirlo: letras repetidas, el mismo jugador repitiendo casi el mismo texto o un jugador
    enviando muchos mensajes seguidos.
    """

    def __init__(self, window_s: float = 12.0, max_messages: int = 4, clock: Callable[[], float] = time.monotonic):
        self.window_s = window_s
        self.max_messages = max_messages
        self.clock = clock
        self._recent: deque[tuple[str, str, float]] = deque(maxlen=200)  # (nombre, texto, instante)

    def check(self, line: ChatLine) -> str | None:
        """Motivo del spam ("repetición de letras", "mensaje repetido", "demasiados mensajes") o None."""
        now = self.clock()
        while self._recent and now - self._recent[0][2] > 60:
            self._recent.popleft()
        speaker = _name_key(line.speaker)
        mine = [(t, when) for s, t, when in self._recent if similar(s, speaker, 0.8)]
        self._recent.append((speaker, line.text, now))
        letters = re.sub(r"\s", "", line.text)
        if len(letters) >= 12 and len(set(letters.casefold())) <= 3:
            return "repetición de letras"
        if re.search(r"(.)\1{9,}", line.text):
            return "repetición de letras"
        text_key = _text_key(line.text)
        if sum(1 for t, _ in mine if SequenceMatcher(None, _text_key(t), text_key).ratio() >= 0.75) >= 2:
            return "mensaje repetido"
        if sum(1 for _, when in mine if now - when <= self.window_s) >= self.max_messages:
            return "demasiados mensajes seguidos"
        return None


def similar(a: str, b: str, threshold: float = 0.85) -> bool:
    a, b = a.casefold(), b.casefold()
    return a == b or SequenceMatcher(None, a, b).ratio() >= threshold


class NameBook:
    """Agrupa las variantes que el OCR produce de un mismo nombre y devuelve la más frecuente."""

    def __init__(self, threshold: float = 0.72) -> None:
        self.threshold = threshold
        self._clusters: list[dict[str, int]] = []

    def _find(self, name: str) -> dict[str, int] | None:
        key = _name_key(name)
        if not key:
            return None
        best, best_ratio = None, self.threshold
        for cluster in self._clusters:
            ratio = max(SequenceMatcher(None, key, _name_key(v)).ratio() for v in cluster)
            if ratio >= best_ratio:
                best, best_ratio = cluster, ratio
        return best

    def canonical(self, name: str) -> str:
        cluster = self._find(name)
        if cluster is None:
            cluster = {}
            self._clusters.append(cluster)
        cluster[name] = cluster.get(name, 0) + 1
        if self._clusters[-1] is not cluster:  # el último que habló, al final (ver `recent`)
            self._clusters.remove(cluster)
            self._clusters.append(cluster)
        return max(cluster.items(), key=lambda item: item[1])[0]

    def recent(self, limit: int = 20) -> list[str]:
        """Nombres de los jugadores del chat, del que habló más recientemente al más antiguo."""
        return [max(cluster.items(), key=lambda item: item[1])[0] for cluster in reversed(self._clusters)][:limit]

    def is_known(self, name: str) -> bool:
        cluster = self._find(name)
        return cluster is not None and sum(cluster.values()) >= 2


_ENTRY_IDS = itertools.count(1)


@dataclass(eq=False)
class _Entry:
    """Mensaje del historial del chat (en el orden del chat)."""

    line: ChatLine
    first_seen: float
    last_seen: float
    name_key: str
    text_key: str
    frames: int = 1
    announce: bool = False  # mensaje nuevo: se avisa apenas se confirma
    announced: bool = False
    needed: int = 1  # capturas en que debe aparecer antes de avisarlo (2 si viene de una lectura dudosa)
    uid: int = field(default_factory=lambda: next(_ENTRY_IDS))  # identidad estable (aunque el texto se repita)


LONG_TEXT = 10  # letras a partir de las cuales un texto casi igual identifica al mensaje aunque el nombre cambie
MISREAD_NAME = 0.3  # parecido mínimo entre un nombre y su mala lectura ("Ana" y "Bruno" dan 0.25: otra persona)


class ChatTracker:
    """Decide qué mensajes son nuevos.

    Guarda el historial del chat en orden y ubica cada captura dentro de ese historial alineando la
    secuencia de mensajes visibles, en lugar de buscar cada texto por separado. Así:
    - Lo que aparece debajo del último mensaje ubicado es nuevo, aunque repita un texto ya visto
      ("Plss donate" dos veces, o el mismo "gg" de hace un rato).
    - Lo que aparece arriba de lo conocido es historial (el jugador subió en el chat): no se traduce.
    - Un mensaje ENTRE dos conocidos es uno que el OCR no había leído: si es reciente se traduce
      igualmente, porque en las ráfagas se perdían.
    - Una línea que ocupa el lugar de un mensaje conocido y se le parece es ese mismo mensaje mal leído.
    - Lo ya visto no se repite aunque el OCR lo lea distinto, y no se olvida mientras siga visible.
    - Un mensaje se confirma apenas aparece (`confirm_frames=1`) o tras varias capturas seguidas.
    - Al empezar, lo que ya estaba en el chat no se traduce, salvo los últimos `keep_on_start` mensajes.
    - Los mensajes propios (por nombre o por lo recién enviado) se ignoran.
    """

    GAP_RECENT_S = 20.0  # un mensaje omitido por el OCR se traduce si lo de abajo llegó hace menos que esto
    MAX_HISTORY = 500
    WINDOW = 60  # mensajes del historial, arriba de lo visto en la captura anterior, donde se busca el nuevo

    def __init__(
        self,
        memory_s: float = 1800.0,
        username: str = "",
        clock: Callable[[], float] = time.monotonic,
        keep_on_start: int = 2,
        confirm_frames: int = 1,
    ) -> None:
        self.memory_s = memory_s
        self.username = username
        self.clock = clock
        self.keep_on_start = keep_on_start
        self.confirm_frames = confirm_frames
        self.names = NameBook()
        self._history: list[_Entry] = []
        self._sent: deque[tuple[str, float]] = deque(maxlen=20)
        self._started = False
        self._last_lines: list[ChatLine] = []
        self._last_entries: list[_Entry] = []
        # Mensajes visibles en la última captura, con el nombre ya unificado (en el orden del chat).
        self.visible: list[ChatLine] = []

    @property
    def visible_origins(self) -> list[ChatLine]:
        """Para cada mensaje visible, cómo se leyó la primera vez (con eso se pidió su traducción)."""
        if len(self._last_entries) != len(self.visible):
            return list(self.visible)
        return [entry.line for entry in self._last_entries]

    @property
    def visible_ids(self) -> list[int]:
        """Identidad estable de cada mensaje visible: dos mensajes iguales (spam, "gg" dos veces) son distintos."""
        if len(self._last_entries) != len(self.visible):
            return [0] * len(self.visible)
        return [entry.uid for entry in self._last_entries]

    def is_known_name(self, name: str) -> bool:
        return self.names.is_known(name)

    def mark_sent(self, text: str) -> None:
        self._sent.append((text, self.clock()))

    # ------------------------------------------------------------ comparación
    @staticmethod
    def _text_match(ka: str, kb: str) -> bool:
        if ka == kb:
            return True
        if not ka or not kb:
            return False
        shorter, longer = sorted((ka, kb), key=len)
        if len(shorter) >= 6 and shorter in longer:
            return True  # misma línea leída cortada
        matcher = SequenceMatcher(None, ka, kb)
        return matcher.real_quick_ratio() >= 0.8 and matcher.quick_ratio() >= 0.8 and matcher.ratio() >= 0.8

    @staticmethod
    def _name_match(a: str, b: str) -> bool:
        return a == b or SequenceMatcher(None, a, b).ratio() >= 0.7

    @classmethod
    def _same_text(cls, a: str, b: str) -> bool:
        return cls._text_match(_text_key(a), _text_key(b))

    def _same(self, a: ChatLine, b: ChatLine) -> bool:
        return self._name_match(_name_key(a.speaker), _name_key(b.speaker)) and self._same_text(a.text, b.text)

    def same_message(self, a: ChatLine, b: ChatLine) -> bool:
        return self._same(a, b)

    def _matches(self, entry: _Entry, key: tuple[str, str]) -> bool:
        if not self._text_match(entry.text_key, key[1]):
            return False
        # Con el chat sin fondo, el nombre (de color, sobre el juego) se lee muy distinto en cada captura
        # ("Silleqlac101140" por "smegladon40"): bastan un texto largo casi idéntico y un nombre algo parecido.
        return self._name_match(entry.name_key, key[0]) or (
            SequenceMatcher(None, entry.name_key, key[0]).ratio() >= MISREAD_NAME
            and self._same_long_text(entry.text_key, key[1]))

    @staticmethod
    def _same_long_text(a: str, b: str, ratio: float = 0.9) -> bool:
        return min(len(a), len(b)) >= LONG_TEXT and SequenceMatcher(None, a, b).ratio() >= ratio

    @staticmethod
    def _resembles(entry: _Entry, key: tuple[str, str]) -> bool:
        """Parecido suficiente para ser el mismo mensaje leído bastante mal, en el mismo lugar del chat."""
        name = SequenceMatcher(None, entry.name_key, key[0]).ratio()
        text = SequenceMatcher(None, entry.text_key, key[1]).ratio()
        if name < 0.45:
            # Otro jugador (aunque diga lo mismo), salvo un texto largo casi igual: en ese caso es el nombre mal leído.
            return name >= MISREAD_NAME and min(len(entry.text_key), len(key[1])) >= LONG_TEXT and text >= 0.85
        return text >= 0.6 or (name >= 0.7 and text >= 0.4)

    @staticmethod
    def _align(entries: list[_Entry], keys: list[tuple[str, str]], same: Callable) -> list[tuple[int, int]]:
        """Pares (historial, visible) en el mismo orden, con la mayor cantidad posible de coincidencias.

        En los empates quedan sin ubicar las líneas de más abajo (un texto repetido al final es un mensaje
        nuevo) y cada línea se asocia al mensaje más reciente posible.
        """
        n, m = len(entries), len(keys)
        if not n or not m:
            return []
        match = [[same(entry, key) for key in keys] for entry in entries]
        dp = [[0] * (m + 1) for _ in range(n + 1)]
        for i in range(1, n + 1):
            row, prev, hits = dp[i], dp[i - 1], match[i - 1]
            for j in range(1, m + 1):
                row[j] = prev[j - 1] + 1 if hits[j - 1] else max(prev[j], row[j - 1])
        pairs = []
        i, j = n, m
        while i > 0 and j > 0:
            if dp[i][j - 1] == dp[i][j]:
                j -= 1
            elif match[i - 1][j - 1]:
                pairs.append((i - 1, j - 1))
                i, j = i - 1, j - 1
            else:
                i -= 1
        pairs.reverse()
        return pairs

    ECHO_S = 90.0  # un mensaje leído mal se reconoce si se vio hace menos que esto

    def _recent_echo(self, key: tuple[str, str], now: float, taken: set[int], repeat_ok: bool = True) -> _Entry | None:
        """Mensaje ya traducido del que esta línea es una lectura rota ("its soltoxic101Vt_here" por "its so toxic
        on there"). Un texto idéntico al final del chat (`repeat_ok`) no lo es: los jugadores repiten mensajes
        ("could u donate pls" dos veces) y llegan abajo. Un texto idéntico en el medio del chat es el mismo
        mensaje, que esta captura ubicó mal.
        """
        name_key, text_key = key
        if len(text_key) < 8:
            return None
        for entry in reversed(self._history[-40:]):
            if id(entry) in taken or not entry.announced or now - entry.last_seen > self.ECHO_S:
                continue
            if entry.text_key == text_key and repeat_ok:
                continue  # repetido; se descarta
            text = SequenceMatcher(None, entry.text_key, text_key).ratio()
            if text < 0.6:
                continue
            name = SequenceMatcher(None, entry.name_key, name_key).ratio()
            if name >= 0.6 or (name >= MISREAD_NAME and min(len(entry.text_key), len(text_key)) >= LONG_TEXT):
                return entry
        return None

    def _is_mine(self, line: ChatLine) -> bool:
        if self.username and similar(_name_key(line.speaker), _name_key(self.username), 0.8):
            return True
        return any(self._same_text(sent, line.text) for sent, _ in self._sent)

    def _entry(self, line: ChatLine, now: float, announce: bool = False, doubtful: bool = False) -> _Entry:
        return _Entry(line, now, now, _name_key(line.speaker), _text_key(line.text), announce=announce,
                      needed=2 if doubtful else 1)

    # ------------------------------------------------------------ captura nueva
    def update(self, lines: list[ChatLine], uncertain: list[bool] | None = None) -> list[ChatLine]:
        """`uncertain[i]`: la línea i proviene de una lectura dudosa; si es un mensaje nuevo, se confirma en otra
        captura.
        """
        now = self.clock()
        doubtful = uncertain or [False] * len(lines)
        while self._sent and now - self._sent[0][1] > self.memory_s:
            self._sent.popleft()
        canonical = [ChatLine(self.names.canonical(raw.speaker), raw.text) for raw in lines]
        self.visible = canonical

        # Caso más frecuente: el chat no cambió desde la captura anterior.
        if canonical == self._last_lines and all(e.announced or not e.announce for e in self._last_entries):
            for entry in self._last_entries:
                entry.last_seen = now
            return []

        self._history = [e for e in self._history if now - e.last_seen <= self.memory_s][-self.MAX_HISTORY:]
        if not self._started:
            if not canonical:
                return []
            # Primera lectura: el contenido previo del chat queda como historial sin traducir, salvo los últimos
            # `keep_on_start`, que se traducen de inmediato.
            self._started = True
            fresh_from = len(canonical) - self.keep_on_start if self.keep_on_start else len(canonical)
            entries = [self._entry(line, now, i >= fresh_from and not self._is_mine(line), doubtful[i])
                       for i, line in enumerate(canonical)]
            self._history.extend(entries)
            return self._finish(canonical, entries)

        keys = [(_name_key(line.speaker), _text_key(line.text)) for line in canonical]
        index = {id(e): i for i, e in enumerate(self._history)}
        before = [index[id(e)] for e in self._last_entries if id(e) in index]
        start = max(0, (min(before) if before else len(self._history)) - self.WINDOW)
        window = self._history[start:]
        assigned: list[_Entry | None] = [None] * len(canonical)
        for i, j in self._align(window, keys, self._matches):
            assigned[j] = window[i]

        created: set[int] = set()
        j = 0
        while j < len(canonical):
            if assigned[j] is not None:
                j += 1
                continue
            first = j
            while j < len(canonical) and assigned[j] is None:
                j += 1
            above = assigned[first - 1] if first > 0 else None
            below = assigned[j] if j < len(canonical) else None
            # Mensajes conocidos que no se reconocieron en esta captura dentro de este tramo: las líneas parecidas a
            # ellos se consideran esos mismos mensajes mal leídos.
            if above is not None and below is not None:
                candidates = self._history[index[id(above)] + 1:index[id(below)]]
            elif above is not None:
                candidates = self._history[index[id(above)] + 1:index[id(above)] + 3 + j - first]
            elif below is not None:
                candidates = self._history[max(0, index[id(below)] - 2 - j + first):index[id(below)]]
            else:
                candidates = []
            for ci, k in self._align(candidates, keys[first:j], self._resembles):
                assigned[first + k] = candidates[ci]
            for k in range(first, j):
                if assigned[k] is not None:
                    continue
                echo = self._recent_echo(keys[k], now, {id(e) for e in assigned if e is not None},
                                         repeat_ok=above is not None and below is None)
                if echo is not None:
                    assigned[k] = echo  # mensaje reciente mal leído (sin fondo, sobre el juego): no es nuevo
                    continue
                if above is not None and below is None:
                    announce = True  # debajo del último mensaje conocido: mensaje nuevo
                elif above is not None:
                    announce = now - below.first_seen <= self.GAP_RECENT_S  # omitido por el OCR
                else:
                    announce = False  # arriba de lo conocido: historial
                line = canonical[k]
                assigned[k] = self._entry(line, now, announce and not self._is_mine(line), doubtful[k])
                created.add(k)

        for k, entry in enumerate(assigned):
            if k not in created:
                entry.last_seen = now
                entry.frames += 1
        self._insert(assigned, created)
        return self._finish(canonical, assigned)

    def _insert(self, assigned: list[_Entry], created: set[int]) -> None:
        """Agrega los mensajes nuevos al historial en su posición (después del anterior visible que ya existía)."""
        if not created:
            return
        after: dict[int, list[_Entry]] = {}
        before: dict[int, list[_Entry]] = {}
        waiting: list[_Entry] = []
        previous: _Entry | None = None
        for k, entry in enumerate(assigned):
            if k in created:
                if previous is not None:
                    after.setdefault(id(previous), []).append(entry)
                else:
                    waiting.append(entry)
            else:
                if waiting:
                    before.setdefault(id(entry), []).extend(waiting)
                    waiting = []
                previous = entry
        # Sin ningún mensaje conocido a la vista: es historial antiguo (o un chat desconocido) y va al principio.
        rebuilt = list(waiting)
        for entry in self._history:
            rebuilt.extend(before.get(id(entry), ()))
            rebuilt.append(entry)
            rebuilt.extend(after.get(id(entry), ()))
        self._history = rebuilt

    def _finish(self, canonical: list[ChatLine], assigned: list[_Entry]) -> list[ChatLine]:
        new = []
        for line, entry in zip(canonical, assigned):
            if entry.announce and not entry.announced and entry.frames >= max(self.confirm_frames, entry.needed):
                if looks_garbled(line.text):
                    continue  # lectura defectuosa: se espera una lectura correcta para no traducir texto basura
                entry.announced = True
                # Se conserva la lectura de la confirmación (si la primera fue dudosa, esta suele ser mejor).
                entry.line, entry.name_key, entry.text_key = line, _name_key(line.speaker), _text_key(line.text)
                new.append(line)
        # Mensajes nuevos sin confirmar que dejaron de verse: eran ruido del OCR.
        visible = {id(e) for e in assigned}
        if any(e.announce and not e.announced for e in self._history):
            self._history = [e for e in self._history if e.announced or not e.announce or id(e) in visible]
        self._last_lines, self._last_entries = canonical, assigned
        return new
