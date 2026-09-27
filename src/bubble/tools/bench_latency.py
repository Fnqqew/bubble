"""Mide la latencia real de traducción por modelo con frases típicas de Roblox.

Uso:  python -m bubble.tools.bench_latency --models opus sonnet haiku
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time
from dataclasses import replace

from ..config import load_config
from ..translate.base import TranslationRequest
from ..translate.claude_subscription import ClaudeSubscriptionProvider

PHRASES: list[tuple[str, str]] = [
    ("gg ez noob, wanna trade my dragon?", "es"),
    ("che alguien me ayuda con el obby?", "en"),
    ("afk 5 min brb", "pt"),
    ("mano me passa robux pfv", "es"),
    ("who wants to team up for the boss raid", "es"),
    ("no cap this game is lowkey fire", "es"),
    ("quién me regala un pet legendario", "en"),
    ("bro stop spawn killing me", "fr"),
    ("tu veux échanger ton épée ?", "es"),
    ("ich bin neu hier, wie funktioniert das?", "es"),
    ("вы откуда ребята", "es"),
    ("siapa mau main bareng", "es"),
    ("dale vamos a la base, rápido", "en"),
    ("ratio + L + you fell off", "es"),
    ("pls dont report me it was an accident", "es"),
]


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


async def _bench(model: str, effort: str) -> None:
    config = load_config()
    provider = ClaudeSubscriptionProvider(replace(config.claude, model=model, effort=effort, pool_size=1))
    start = time.perf_counter()
    await provider.start()
    print(f"\n=== {model} (effort {effort}) — sesión lista en {time.perf_counter() - start:.2f}s ===")
    ttfts: list[float] = []
    totals: list[float] = []
    try:
        for text, target in PHRASES:
            t0 = time.perf_counter()
            ttft = None
            chunks: list[str] = []
            async for chunk in provider.stream(TranslationRequest(text, target, "incoming")):
                ttft = ttft if ttft is not None else time.perf_counter() - t0
                chunks.append(chunk)
            total = time.perf_counter() - t0
            totals.append(total)
            if ttft is not None:
                ttfts.append(ttft)
            print(f"  {total:5.2f}s  [{target}] {text!r} → {''.join(chunks).strip()!r}")
    finally:
        await provider.close()
    if totals:
        print(
            f"  primera palabra p50={statistics.median(ttfts):.2f}s p95={_percentile(ttfts, 95):.2f}s | "
            f"total p50={statistics.median(totals):.2f}s p95={_percentile(totals, 95):.2f}s"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark de latencia de traducción")
    parser.add_argument("--models", nargs="+", default=["opus"])
    parser.add_argument("--effort", default="low")
    args = parser.parse_args()
    for model in args.models:
        asyncio.run(_bench(model, args.effort))


if __name__ == "__main__":
    main()
