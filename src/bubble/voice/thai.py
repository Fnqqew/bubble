"""TLTK para la voz tailandesa, sin sus paquetes pesados.

La voz tailandesa de Piper necesita TLTK para pasar el texto a fonemas (separa las palabras, que en tailandés van
juntas, y marca el tono de cada sílaba). TLTK importa al cargarse pandas, nltk, scikit-learn 1.2, sklearn-crfsuite y
gensim, que usa para otras tareas (etiquetar, vectores de palabras) y que no tienen versión para este Python. La
función que usa Piper (th2ipa) no los necesita: TLTK se instala sin ellos y, solo mientras se carga, recibe módulos
vacíos en su lugar. Después se quitan, para que nada más en Bubble los confunda con los reales.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
import threading
import types

HEAVY = ("pandas", "nltk", "sklearn", "sklearn_crfsuite", "gensim", "matplotlib")
_lock = threading.Lock()


class _Anything:
    """Cualquier clase o función de esos paquetes: se puede crear y llamar, y no hace nada."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __call__(self, *args, **kwargs):
        return _Anything()

    def __getattr__(self, name: str):
        return _Anything()


class _Empty(types.ModuleType):
    def __getattr__(self, name: str):
        if name.startswith("__"):
            raise AttributeError(name)
        return _Anything


class _StandIn(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in HEAVY:
            return importlib.machinery.ModuleSpec(name, self, is_package=True)
        return None

    def create_module(self, spec):
        return _Empty(spec.name)

    def exec_module(self, module) -> None:
        module.__path__ = []


def load() -> None:
    """Carga TLTK (una vez por proceso, ~2,5 s: lee sus diccionarios). Antes de que la voz tailandesa hable."""
    with _lock:
        if "tltk.nlp" in sys.modules:
            return
        finder = _StandIn()
        sys.meta_path.insert(0, finder)
        try:
            import tltk.nlp  # noqa: F401 - Piper la importa después y la encuentra ya cargada
        finally:
            sys.meta_path.remove(finder)
            for name in [name for name, module in sys.modules.items() if isinstance(module, _Empty)]:
                del sys.modules[name]


def needed(voice) -> bool:
    """Indica si esa voz de Piper (ya cargada) lee con TLTK."""
    return str(getattr(getattr(voice, "config", None), "phoneme_type", "")).lower().endswith("thai")
