"""Los textos de la interfaz y sus traducciones (Bubble en el idioma de cada uno; ver i18n.py).

    python -m bubble.tools.ui_strings                     # junta los textos → src/bubble/locales/_textos.json
    python -m bubble.tools.ui_strings --idiomas en,pt,fr  # y los traduce con Claude → src/bubble/locales/<idioma>.json

Los textos se buscan en el código (los literales y los f-strings que ve la persona: con variables quedan como
plantillas con {0}, {1}…). Al traducir se conserva lo ya traducido y se piden solo los textos nuevos.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent
LOCALES = PACKAGE / "locales"
SOURCES = LOCALES / "_textos.json"
# Dónde están los textos que ve la persona (la ventana, los avisos, la revisión de tu PC…).
FILES = [*sorted((PACKAGE / "ui").glob("*.py")), *(PACKAGE / name for name in (
    "system.py", "support.py", "install.py", "update.py", "uninstall.py", "pro.py", "win32.py", "cloud/errors.py",
    "voice/checks.py", "translate/base.py"))]
SKIP_IN = {"rtl.py"}  # (sin textos para traducir)
_WORD = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúñÑüÜ¿¡]{3,}")
_TECHNICAL = ("$s.", "\r\n", "tasklist", "rmdir", "irm https", "install -e", "Content-Disposition", "\\Scripts\\")
BATCH = 120
MODEL = "sonnet"


def _skeleton(node: ast.JoinedStr) -> str:
    parts, index = [], 0
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value).replace("{", "{{").replace("}", "}}"))
        else:
            parts.append("{%d}" % index)
            index += 1
    return "".join(parts)


def _visible(text: str) -> bool:
    if not _WORD.search(text) or text.startswith(("http", "#", "<", "%", "bubble-", "-")) or any(
            mark in text for mark in _TECHNICAL):
        return False
    if re.fullmatch(r"[\w.\-:/]+", text):  # identificadores, rutas, nombres de archivo…
        return bool(re.fullmatch(r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+", text))  # …pero sí los rótulos ("Voz", "Tamaño")
    return " " in text.strip() or text[:1].isupper() or any(c in text for c in "áéíóúñ¿¡…·→")


def extract() -> list[str]:
    """Todos los textos de la interfaz, en español (sin repetir, en orden)."""
    found: dict[str, None] = {}
    for path in FILES:
        if path.name in SKIP_IN:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)) and node.body \
                    and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                skip.add(id(node.body[0].value))  # documentación
            if isinstance(node, ast.JoinedStr):
                skip |= {id(value) for value in node.values}
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                    "debug", "info", "warning", "exception", "error") and isinstance(node.func.value, ast.Name) \
                    and node.func.value.id in ("log", "logging"):
                skip |= {id(arg) for arg in node.args}  # lo que va al registro
            if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in (
                    "SAMPLES", "SAMPLE", "PHRASES") for target in node.targets):
                skip |= {id(sub) for sub in ast.walk(node.value)}  # frases de prueba en cada idioma
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = _skeleton(node)
            else:
                continue
            if _visible(text):
                found[text] = None
    from ..translate.languages import DISPLAY_NAMES, LOCALE_CHOICES

    for name in [*DISPLAY_NAMES.values(), *(name for _code, name in LOCALE_CHOICES)]:
        found[name] = None  # (los nombres de los idiomas, en las listas)
    return list(found)


# ---------------------------------------------------------------- traducir con Claude
PROMPT = """You are localizing the interface of Bubble, a Windows app that translates Roblox chat and voice in real time
for players (mostly kids and teens). The source is casual, friendly Rioplatense Spanish (voseo). Translate every JSON
key into {language}: the same friendly, casual, human tone a good app in that language would use, short and clear.
Rules:
- Keep placeholders like {{0}} {{1}} exactly as they are (you may reorder them if the grammar needs it).
- Keep line breaks, symbols (✦ • → · ↑ ↓ ⚠ ✓ ♀ ♂ 🔒), keyboard keys (Enter, Ctrl+Enter, Tab, Shift, Esc, Ctrl+P,
  Ctrl+G, Win + Shift + S) and names (Bubble, Bubble Pro, Basic, Pro, Claude, Claude Code, Claude Pro, Max, Deepgram,
  Roblox, Windows, Discord, OBS, Soundpad, VB-Audio Virtual Cable, CABLE Output, FormSubmit, GitHub, US$) unchanged.
- Language names become their name in {language}. Roblox game words (pvp, lag, noob, robux) stay as gamers say them.
Output ONLY one JSON object that maps each original key to its translation, nothing else."""


def _claude() -> str:
    from ..claude_cli import find_claude_cli

    cli = find_claude_cli()
    if not cli:
        raise RuntimeError("no encontré Claude Code")
    return str(cli)


def _ask(batch: list[str], language: str, timeout: float = 300) -> dict[str, str]:
    payload = json.dumps({text: "" for text in batch}, ensure_ascii=False)
    result = subprocess.run([_claude(), "-p", PROMPT.format(language=language), "--model", MODEL],
                            input=payload, capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    data = _objects(result.stdout)
    if not data:
        raise RuntimeError(f"Claude no devolvió JSON ({result.stdout.strip()[:120]!r})")
    return {key: value for key, value in data.items() if key in batch and _same_slots(key, value)}


def _objects(reply: str) -> dict:
    """Los objetos JSON de la respuesta, juntos (a veces Claude la parte en dos, o mete un salto de línea crudo)."""
    decoder, data, at = json.JSONDecoder(strict=False), {}, 0
    while (at := reply.find("{", at)) >= 0:
        try:
            value, end = decoder.raw_decode(reply, at)
        except ValueError:
            at += 1
            continue
        if isinstance(value, dict):
            data.update(value)
        at = end
    return data


def _same_slots(source: str, translation: str) -> bool:
    """La traducción conserva las variables ({0}, {1}…) y no vino vacía."""
    slots = re.compile(r"(?<!\{)\{\d+\}(?!\})")
    return isinstance(translation, str) and bool(translation.strip()) and \
        sorted(slots.findall(source)) == sorted(slots.findall(translation))


def translate(texts: list[str], language: str, known: dict[str, str] | None = None, workers: int = 3,
              progress=lambda _done, _total: None) -> dict[str, str]:
    """Las traducciones de `texts` a `language` (nombre en inglés, ej. "Portuguese"). Pide solo las que faltan; si
    Claude se saltea alguna, la vuelve a pedir (hasta dos veces más, en tandas más chicas)."""
    done = dict(known or {})
    for attempt in range(3):
        missing = [text for text in texts if text not in done]
        if not missing:
            break
        size = BATCH if attempt == 0 else max(20, BATCH // 3)  # (lo que falló, en tandas más chicas)
        batches = [missing[i:i + size] for i in range(0, len(missing), size)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(lambda batch: _safe_ask(batch, language), batches):
                done.update(result)
                progress(len([t for t in texts if t in done]), len(texts))
    return {text: done[text] for text in texts if text in done}


def _safe_ask(batch: list[str], language: str) -> dict[str, str]:
    try:
        return _ask(batch, language)
    except (RuntimeError, ValueError, subprocess.SubprocessError, OSError) as exc:
        print(f"  (un pedido falló: {exc})")
        return {}


def main() -> None:
    from ..translate.languages import LANGUAGES

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--idiomas", default="", help="códigos separados por coma (ej. en,pt,fr)")
    args = parser.parse_args()
    LOCALES.mkdir(exist_ok=True)
    texts = extract()
    SOURCES.write_text(json.dumps(texts, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{len(texts)} textos → {SOURCES}")
    for code in filter(None, args.idiomas.split(",")):
        path = LOCALES / f"{code}.json"
        known = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        result = translate(texts, LANGUAGES[code], known,
                           progress=lambda done, total: print(f"  {code}: {done}/{total}", flush=True))
        path.write_text(json.dumps(result, ensure_ascii=False, indent=0), encoding="utf-8")
        print(f"{code}: {len(result)} de {len(texts)} → {path}", flush=True)


if __name__ == "__main__":
    main()
