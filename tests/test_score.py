"""The reward must prefer a band-hitting, varied generator over a gamed one.

The score exists to stop the loop optimising itself into a useless generator.
The load-bearing property is a comparison, not an absolute number: the classic
Goodhart case -- `<a/>` forever, 100% accepted, one shape -- must score BELOW a
generator that lands in the 70-85% band and produces varied documents. If that
ordering ever flips, the loop would learn to cheat, so it is pinned here.
"""

from __future__ import annotations

from fuzzer.campaign import CampaignStats
from fuzzer.score import TARGET_HIGH, TARGET_LOW, score_campaign


def _degenerate() -> CampaignStats:
    """`<a/>` forever: every input accepted, all identical -> no spread."""
    return CampaignStats(
        accepted=100,
        rejected=0,
        input_lengths=[4] * 100,
        nesting_depths=[0] * 100,
    )


def _band_and_varied() -> CampaignStats:
    """78% accepted, wide spread in both size and depth."""
    return CampaignStats(
        accepted=78,
        rejected=22,
        input_lengths=list(range(100)),
        nesting_depths=[i % 9 for i in range(100)],
    )


def test_varied_beats_gamed():
    """The whole point: 100% acceptance must not win by itself."""
    gamed = score_campaign(_degenerate()).value
    good = score_campaign(_band_and_varied()).value
    assert good > gamed


def test_hundred_percent_is_penalised():
    """Acceptance above the band is worse than acceptance in it."""
    in_band = score_campaign(_band_and_varied())
    assert in_band.band == 1.0
    saturated = score_campaign(_degenerate())
    assert saturated.band < 1.0  # 100% sits outside [0.70, 0.85]


def test_band_edges():
    mid = (TARGET_LOW + TARGET_HIGH) / 2
    assert score_campaign(CampaignStats(accepted=int(mid * 100),
                                        rejected=100 - int(mid * 100))).band == 1.0
    # Far below the band collapses the band term to 0.
    far_below = score_campaign(CampaignStats(accepted=30, rejected=70))
    assert far_below.band == 0.0


def test_diversity_needs_spread():
    """Same acceptance, but flat output scores less than varied output."""
    flat = CampaignStats(accepted=78, rejected=22,
                         input_lengths=[10] * 100, nesting_depths=[1] * 100)
    varied = _band_and_varied()
    assert score_campaign(varied).diversity > score_campaign(flat).diversity


def test_crash_bonus_applies():
    # Flat output so the base score is below 1.0 and the bonus is observable.
    without = CampaignStats(accepted=78, rejected=22,
                            input_lengths=[10] * 100, nesting_depths=[1] * 100)
    with_crash = CampaignStats(accepted=78, rejected=22, crashed=1,
                               input_lengths=[10] * 100, nesting_depths=[1] * 100)
    assert score_campaign(with_crash).value > score_campaign(without).value


def test_score_is_bounded():
    for stats in (_degenerate(), _band_and_varied()):
        assert 0.0 <= score_campaign(stats).value <= 1.0


if __name__ == "__main__":
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
