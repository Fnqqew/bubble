"""Punto de entrada: `python -m bubble` (ventana de prueba) o `--console`."""

from __future__ import annotations

import argparse
import asyncio
import faulthandler
import logging
import sys
from pathlib import Path

from .config import Config, load_config
from .translate.base import TONE_NAMES, clamp_tone


async def _console(config: Config) -> None:
    from .translate import build_translator

    translator = build_translator(config)
    print("Conectando con tu suscripción de Claude...")
    await translator.start()
    print(
        "Listo. Escribí:\n"
        "  Nombre: mensaje   -> mensaje entrante de otro jugador (se traduce a tu idioma)\n"
        "  > mensaje         -> lo que vos escribís (se traduce al idioma del chat)\n"
        "  >en: mensaje      -> lo que vos escribís, en un idioma a elección (en, pt-BR, es-MX...)\n"
        "  tono 1..5         -> tono al enviar (1 neutro/formal ... 5 jerga nativa)\n"
        "  salir             -> terminar\n"
    )
    try:
        while True:
            line = (await asyncio.to_thread(input, "» ")).strip().lstrip("\ufeff")
            if line.lower() in {"salir", "exit", "quit"}:
                break
            if not line:
                continue
            if line.lower().startswith("tono "):
                config.user.tone = clamp_tone(int(line.split()[1]))
                print(f"    Tono al enviar: {config.user.tone} - {TONE_NAMES[config.user.tone]}")
                continue

            streamed: list[str] = []

            def on_delta(chunk: str) -> None:
                streamed.append(chunk)
                print(chunk, end="", flush=True)

            print("   ", end="")
            if line.startswith(">"):
                # ">en: texto" elige el idioma destino; "> texto" usa el del chat.
                target, sep, rest = line[1:].partition(":")
                explicit = sep and 2 <= len(target.strip()) <= 5 and " " not in target.strip()
                text = rest if explicit else line[1:]
                result = await translator.translate_outgoing(
                    text, target.strip() if explicit else None, on_delta=on_delta
                )
            else:
                speaker, _, text = line.partition(":") if ":" in line else ("", "", line)
                result = await translator.translate_incoming(text or speaker, speaker if text else "", on_delta)
            if not streamed:
                print(result.translation, end="")
            ttft = f", primera palabra {result.ttft_s:.2f}s" if result.ttft_s is not None else ""
            print(f"\n    [{result.source_lang} → {result.target_lang}, {result.status}, {result.total_s:.2f}s{ttft}]")
    finally:
        await translator.close()


def _error_log() -> None:
    """Con la ventana (pythonw) no hay consola y los errores se perdían: quedan en %APPDATA%\\Bubble\\errores.log.
    Incluye los cierres de golpe (una librería nativa que se cae), que Python no puede atrapar."""
    from .state import state_path

    path = state_path().with_name("errores.log")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a", encoding="utf-8")
    except OSError:
        return
    faulthandler.enable(stream)
    if sys.stderr is None:
        sys.stderr = stream


def _activity_log() -> None:
    """Lo que va haciendo Bubble, para entender un problema después: %APPDATA%\\Bubble\\bubble.log (~2 MB como
    mucho). Solo lo de Bubble: ni lo que dicen en el chat ni lo que tecleás."""
    from logging.handlers import RotatingFileHandler

    from .state import state_path

    try:
        handler = RotatingFileHandler(state_path().with_name("bubble.log"), maxBytes=1_000_000, backupCount=1,
                                      encoding="utf-8")
    except OSError:
        return
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("bubble")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)


def main() -> None:
    parser = argparse.ArgumentParser(prog="bubble", description="Traductor en tiempo real para Roblox")
    parser.add_argument("--console", action="store_true", help="modo consola en vez de ventana")
    parser.add_argument("--config", type=Path, help="ruta a config.toml")
    parser.add_argument("-v", "--verbose", action="store_true", help="logs detallados")
    args = parser.parse_args()
    if not args.console:
        _error_log()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not args.console:
        if not args.verbose:
            for handler in logging.getLogger().handlers:
                handler.setLevel(logging.WARNING)  # errores.log sigue siendo solo de errores
        _activity_log()

    config = load_config(args.config)
    if args.console:
        asyncio.run(_console(config))
    else:
        from .ui.launch import launch

        launch(config)


if __name__ == "__main__":
    main()
