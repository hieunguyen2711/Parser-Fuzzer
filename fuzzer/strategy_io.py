"""Get a runnable Hypothesis strategy out of a model response.

Two steps that are easy to do sloppily: pulling the code out of the reply, and
turning that text into a live strategy object.

A note on what this does: it executes code written by a language model. The
seed prompt forbids I/O, network and imports beyond hypothesis and the
standard library, but a prompt is a request, not a sandbox. The generated
module is written to disk before it is imported precisely so there is always a
file to read when something behaves oddly -- never a strategy that existed
only in memory.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

from hypothesis import strategies as st

# ```python ... ``` or a bare ``` ... ``` fence.
_FENCE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


class ExtractionError(RuntimeError):
    pass


def extract_code(response: str) -> str:
    """Pull the single code block the prompt asked for."""
    blocks = _FENCE.findall(response)
    if not blocks:
        # Some models answer with bare code when the reply is only code.
        if "import" in response and "STRATEGY" in response:
            return response.strip()
        raise ExtractionError(
            "no fenced code block in the response; the model did not follow "
            "the output contract"
        )
    if len(blocks) > 1:
        # Keep the one that actually defines the entry point rather than
        # guessing by position -- models often precede it with a short example.
        defining = [b for b in blocks if "STRATEGY" in b]
        if len(defining) == 1:
            return defining[0].strip()
        raise ExtractionError(
            f"{len(blocks)} code blocks, {len(defining)} defining STRATEGY; "
            "cannot tell which is the module"
        )
    return blocks[0].strip()


def load_strategy(path: Path, attr: str = "STRATEGY") -> st.SearchStrategy:
    """Import a generated module from disk and return its strategy object."""
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ExtractionError(f"cannot import {path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise ExtractionError(
            f"generated module failed to import: {type(exc).__name__}: {exc}"
        ) from exc

    if not hasattr(module, attr):
        raise ExtractionError(
            f"module defines no {attr}; it has: "
            f"{', '.join(n for n in vars(module) if not n.startswith('_'))[:200]}"
        )

    strategy = getattr(module, attr)
    if not isinstance(strategy, st.SearchStrategy):
        raise ExtractionError(f"{attr} is {type(strategy).__name__}, not a strategy")
    return strategy
