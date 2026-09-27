"""Motor de traducción."""

from __future__ import annotations

from ..config import Config
from .engine import Translator
from .router import Router


def build_translator(config: Config) -> Translator:
    from .claude_subscription import ClaudeSubscriptionProvider
    from .prompt import build_system_prompt

    provider = ClaudeSubscriptionProvider(config.claude, build_system_prompt(config.translation.explain_slang))
    router = Router([provider], timeout_s=config.translation.timeout_s)
    return Translator(config, router)


__all__ = ["Translator", "Router", "build_translator"]
