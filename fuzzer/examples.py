"""Draw concrete examples out of a Hypothesis strategy, for eyeballing.

Hypothesis strategies are lazy descriptions, not lists, so "show me what this
generates" needs a deliberate draw. `strategy.example()` exists but is
documented as for interactive use only and reseeds itself on every call, which makes
runs hard to compare. Running a `@given` test and collecting what it receives
gives the same examples the fuzzer will actually see, and a fixed seed makes
two runs comparable.
"""

from __future__ import annotations

import random

from hypothesis import HealthCheck, given, seed, settings
from hypothesis import strategies as st


def draw_examples(
    strategy: st.SearchStrategy,
    count: int = 10,
    random_seed: int | None = 0,
) -> list:
    """Return `count` examples, drawn the way the campaign would draw them."""
    collected: list = []

    @given(value=strategy)
    @settings(
        max_examples=count,
        deadline=None,
        database=None,          # do not replay past failures; we want fresh draws
        suppress_health_check=list(HealthCheck),
        phases=[__import__("hypothesis").Phase.generate],
    )
    def collect(value):
        collected.append(value)

    if random_seed is not None:
        collect = seed(random_seed)(collect)

    collect()
    return collected[:count]
