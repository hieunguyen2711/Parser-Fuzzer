"""Turn one campaign's numbers into a single comparable reward.

The refinement loop needs to answer "did this iteration get better?" and,
with no coverage instrumentation available, it has to answer it from blackbox
signals alone. This module defines what "better" means, and the definition is
deliberately NOT "higher acceptance".

Two failure modes it has to resist, both of which maximise a naive score:

  100% ACCEPTANCE IS A TRAP. A generator that emits `<a/>` forever is accepted
  every time and tests nothing. So acceptance is rewarded only inside a target
  BAND (70-85%, the same band the seed prompt asks for); drifting above it is
  penalised just like drifting below it. A deliberate malformed minority is
  what keeps the parser's error paths under test.

  UNIFORM OUTPUT IS A TRAP. Even inside the band, a generator can repeat one
  shape. So a DIVERSITY term rewards spread in nesting depth and input size.
  The `<a/>`-forever generator has zero spread in both, so diversity collapses
  to 0 and its score stays low even at 100% acceptance -- which is the whole
  point.

`CampaignStats` does not record distinct outputs, so diversity is measured from
the spread of what it does record (depth, size). This under-measures variety --
two differently-shaped documents of the same length and depth read as one --
but it never over-measures it, which is the safe direction for a guard.
"""

from __future__ import annotations

from dataclasses import dataclass

from fuzzer.campaign import CampaignStats

# The acceptance band the score rewards -- matches the seed prompt's target.
TARGET_LOW = 0.70
TARGET_HIGH = 0.85
# Acceptance this far outside the band scores 0 on the band term.
BAND_FALLOFF = 0.30

# Spread (p90 - p10) that counts as "fully varied" for each signal.
DEPTH_SPREAD_TARGET = 6
SIZE_SPREAD_TARGET = 60

BAND_WEIGHT = 0.6
DIVERSITY_WEIGHT = 0.4
CRASH_BONUS = 0.1


@dataclass(frozen=True)
class Score:
    """The reward and its parts, so a log can show *why* a run scored as it did."""

    value: float
    acceptance: float
    band: float
    diversity: float
    crash_bonus: float

    def brief(self) -> str:
        return (
            f"score {self.value:.2f}  "
            f"(band {self.band:.2f} @ acceptance {self.acceptance:.0%}, "
            f"diversity {self.diversity:.2f}, crash +{self.crash_bonus:.1f})"
        )


def _percentile(values: list[int], fraction: float) -> int:
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return ordered[index]


def _spread(values: list[int], target: int) -> float:
    """Normalized p90-p10 spread in [0, 1]; 0 when everything is identical."""
    if not values or target <= 0:
        return 0.0
    spread = _percentile(values, 0.9) - _percentile(values, 0.1)
    return min(1.0, spread / target)


def _band(acceptance: float) -> float:
    """1.0 inside [TARGET_LOW, TARGET_HIGH], decaying to 0 at BAND_FALLOFF out."""
    if acceptance < TARGET_LOW:
        distance = TARGET_LOW - acceptance
    elif acceptance > TARGET_HIGH:
        distance = acceptance - TARGET_HIGH
    else:
        distance = 0.0
    return 1.0 - min(1.0, distance / BAND_FALLOFF)


def score_campaign(stats: CampaignStats) -> Score:
    """Reward in [0, 1]: band-targeted acceptance, plus diversity, plus a crash nudge."""
    acceptance = stats.acceptance_rate
    band = _band(acceptance)
    diversity = 0.5 * _spread(stats.nesting_depths, DEPTH_SPREAD_TARGET) + 0.5 * _spread(
        stats.input_lengths, SIZE_SPREAD_TARGET
    )
    crash_bonus = CRASH_BONUS if stats.crashed else 0.0
    value = min(1.0, BAND_WEIGHT * band + DIVERSITY_WEIGHT * diversity + crash_bonus)
    return Score(
        value=value,
        acceptance=acceptance,
        band=band,
        diversity=diversity,
        crash_bonus=crash_bonus,
    )
