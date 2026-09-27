"""Cache LRU de traducciones para mensajes cortos y repetidos ("gg", "hi", "wanna trade?")."""

from __future__ import annotations

import re
from collections import OrderedDict

_SPACES = re.compile(r"\s+")


def normalize(text: str) -> str:
    return _SPACES.sub(" ", text.strip())


class TranslationCache:
    def __init__(self, max_size: int = 2000, max_words: int = 6) -> None:
        self.max_size = max_size
        self.max_words = max_words
        self._items: OrderedDict[tuple[str, str], str] = OrderedDict()

    def cacheable(self, text: str) -> bool:
        # Los mensajes largos dependen del contexto: no se cachean.
        return 0 < len(normalize(text).split(" ")) <= self.max_words

    def get(self, text: str, target_lang: str) -> str | None:
        key = (normalize(text), target_lang)
        value = self._items.get(key)
        if value is not None:
            self._items.move_to_end(key)
        return value

    def put(self, text: str, target_lang: str, translation: str) -> None:
        if not self.cacheable(text):
            return
        key = (normalize(text), target_lang)
        self._items[key] = translation
        self._items.move_to_end(key)
        while len(self._items) > self.max_size:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)
