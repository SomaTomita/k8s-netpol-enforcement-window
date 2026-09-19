"""Tests for npw.analysis.stats: percentile bootstrap confidence intervals.

Window distributions are expected to be heavy-tailed (see
docs/methodology.md), so these tests deliberately check bootstrap
behavior against a known synthetic distribution rather than asserting
anything about normal-theory intervals.
"""

import numpy as np
import pytest

from npw.analysis.stats import bootstrap_ci


def test_bootstrap_ci_brackets_true_median():
    # Population median of N(100, 10) is 100.
    xs = np.random.default_rng(0).normal(100, 10, 500)
    low, high = bootstrap_ci(xs, seed=0)
    assert low < 100 < high


def test_bootstrap_ci_reproducible_given_same_seed():
    xs = np.random.default_rng(1).normal(50, 5, 200)
    first = bootstrap_ci(xs, seed=42)
    second = bootstrap_ci(xs, seed=42)
    assert first == second


def test_bootstrap_ci_reproducible_without_explicit_seed():
    # seed=None must fall back to a fixed, documented default -- not to
    # OS entropy -- so two calls with no seed given still agree.
    xs = np.random.default_rng(2).normal(0, 1, 100)
    first = bootstrap_ci(xs)
    second = bootstrap_ci(xs)
    assert first == second


def test_bootstrap_ci_different_seeds_can_differ():
    xs = np.random.default_rng(4).normal(0, 1, 100)
    default_seed_result = bootstrap_ci(xs, seed=0)
    other_seed_result = bootstrap_ci(xs, seed=123)
    assert default_seed_result != other_seed_result


def test_bootstrap_ci_narrows_as_sample_size_grows():
    rng = np.random.default_rng(3)
    small = rng.normal(100, 10, 30)
    large = rng.normal(100, 10, 3000)

    small_low, small_high = bootstrap_ci(small, seed=7)
    large_low, large_high = bootstrap_ci(large, seed=7)

    assert (large_high - large_low) < (small_high - small_low)


def test_bootstrap_ci_rejects_invalid_alpha():
    xs = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    with pytest.raises(ValueError):
        bootstrap_ci(xs, alpha=1.5)


def test_bootstrap_ci_rejects_empty_input():
    with pytest.raises(ValueError):
        bootstrap_ci(np.array([]))
