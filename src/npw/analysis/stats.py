"""Distribution-free confidence intervals for window statistics.

Window measurements (see docs/methodology.md) are expected to be
heavy-tailed: a handful of slow CNI reconciliations can dominate the
distribution, and pooled samples span multiple CNIs and churn conditions.
A normal-theory interval (mean +/- z * SE) assumes a shape this data is
not expected to have, so this module reports a percentile bootstrap
interval instead, which makes no assumption about the underlying
distribution's shape.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
from scipy import stats

# A bootstrap CI is only useful if it is reproducible: re-running the same
# analysis script must yield the same interval, or two "identical" runs
# could silently disagree and no one would notice without diffing raw
# resamples. `bootstrap_ci`'s `seed` therefore never falls through to OS
# entropy: leaving it at the default `None` substitutes this fixed,
# documented seed rather than an unseeded one. Pass an explicit seed to
# get a different (still reproducible) resample stream.
_DEFAULT_SEED = 0


def bootstrap_ci(
    xs: Sequence[float],
    statistic: Callable[..., float] = np.median,
    n_resamples: int = 10000,
    alpha: float = 0.05,
    seed: int | None = None,
) -> tuple[float, float]:
    """Return a `(1 - alpha)` percentile bootstrap CI for `statistic(xs)`.

    Resamples `xs` with replacement `n_resamples` times, recomputes
    `statistic` on each resample, and returns the `alpha / 2` and
    `1 - alpha / 2` percentiles of that resampled distribution. This is
    the plain percentile method (not bias-corrected/accelerated): it is
    the more direct read of "where does this statistic land across
    plausible resamples of the data I actually have," which is the
    question this project needs answered, not a tighter-but-more-assumed
    alternative.

    `seed` defaults to `None`, which is resolved to a fixed internal
    default (not OS entropy) so that `bootstrap_ci` is never silently
    non-reproducible -- see the module-level comment on `_DEFAULT_SEED`.
    Pass an explicit `seed` to pin a specific resample stream, e.g. for a
    result that must be reproduced exactly in a written-up analysis.

    Raises `ValueError` if `xs` is empty or `alpha` is not in `(0, 1)`.
    """
    xs_arr = np.asarray(xs, dtype=float)
    if xs_arr.size == 0:
        raise ValueError("xs must be non-empty")
    if not 0 < alpha < 1:
        raise ValueError(f"alpha must be in (0, 1), got {alpha!r}")

    rng = np.random.default_rng(seed if seed is not None else _DEFAULT_SEED)
    result = stats.bootstrap(
        (xs_arr,),
        statistic,
        n_resamples=n_resamples,
        confidence_level=1 - alpha,
        method="percentile",
        random_state=rng,
    )
    return float(result.confidence_interval.low), float(result.confidence_interval.high)
