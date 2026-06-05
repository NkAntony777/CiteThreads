"""
Prompt loader stub.

The bilingual prompt templates land in Task 3 (Scout/Scribe/Signal) and
beyond. This module exposes a stable ``load_prompt(name, lang)`` API so
the phase implementations in later tasks can call it without
re-shuffling imports.

A simple file-based loader is used to keep zero new dependencies. The
``en`` and ``zh`` directories mirror each other; missing translations
fall back to English (with a logged warning) so the pipeline never
blocks on translation gaps.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

logger = logging.getLogger(__name__)

_PROMPTS_DIR: Final[Path] = Path(__file__).parent
_SUPPORTED_LANGS: Final[frozenset[str]] = frozenset({"en", "zh"})


def load_prompt(name: str, lang: str = "en") -> str:
    """
    Load a prompt template by ``name`` (without extension) and ``lang``.

    Args:
        name: e.g. ``"scout"``, ``"crafter"``, ``"factcheck"``.
        lang: ISO 639-1 code; one of ``{"en", "zh"}``.

    Returns:
        The raw markdown body of the prompt.

    Raises:
        FileNotFoundError: if the prompt does not exist in either
            ``en`` or ``zh``. Task 3+ is responsible for filling these
            in; until then callers will get a clear error.

    Notes:
        The current directory contains no ``.md`` files yet; the
        function will raise FileNotFoundError. Tests assert this
        behaviour so we know the loader works once files are added.
    """
    if lang not in _SUPPORTED_LANGS:
        raise ValueError(
            f"Unsupported prompt language: {lang!r}. Expected one of {sorted(_SUPPORTED_LANGS)}."
        )

    target = _PROMPTS_DIR / lang / f"{name}.md"
    if target.exists():
        return target.read_text(encoding="utf-8")

    fallback = _PROMPTS_DIR / "en" / f"{name}.md"
    if lang != "en" and fallback.exists():
        logger.warning(
            "Prompt %r not found for lang=%r, falling back to English",
            name,
            lang,
        )
        return fallback.read_text(encoding="utf-8")

    raise FileNotFoundError(
        f"Prompt {name!r} (lang={lang!r}) not found. "
        f"Expected {_PROMPTS_DIR / lang / (name + '.md')}. "
        "Prompts are populated in Task 3+."
    )


def list_loaded_prompts(lang: str = "en") -> list[str]:
    """Return the names of prompts currently present in ``lang/``."""
    if lang not in _SUPPORTED_LANGS:
        return []
    lang_dir = _PROMPTS_DIR / lang
    if not lang_dir.exists():
        return []
    return sorted(p.stem for p in lang_dir.glob("*.md"))
