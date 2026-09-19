"""Background Pod churn: what "churn_rate_per_min = r" means operationally.

`churn_rate_per_min` is one of this experiment's pre-registered variables
(`experiments/exp1-window/matrix.yaml`), and it is a *combined* rate: the
victim itself counts as the first Pod per minute, and the remaining `r -
1` are background Pods created and deleted in the trial namespace, labeled
`role=churn`. The default-deny policy's `podSelector: {}` matches those
background Pods too, so the CNI agent has to realise policy for them
concurrently with the victim — that concurrent realisation load is the
thing this variable actually manipulates. `role=churn` (as opposed to
`role=victim`) is also what keeps the prober from ever mistaking a
background Pod for the measurement target.

This module is pure: it computes *how many* background Pods a rate
implies and *when* (as offsets from a churn window's start) to create
them. It does no network I/O and spawns no subprocesses — the driver in
scripts/churn.py does the actual kubectl calls — so the scheduling
decision itself stays unit-testable without a cluster.
"""

from __future__ import annotations


def background_rate(rate_per_min: int) -> int:
    """Return how many *background* Pods per minute a churn level implies.

    The victim Pod is always the first of the `rate_per_min` Pods/minute
    that this parameter specifies, so the background count is one less.
    `rate_per_min` must be >= 1 (a churn rate that excludes even the
    victim is not a meaningful experimental condition).
    """
    if rate_per_min < 1:
        raise ValueError(f"rate_per_min must be >= 1 (the victim itself), got {rate_per_min!r}")
    return rate_per_min - 1


def churn_schedule(rate_per_min: float, duration_s: float) -> list[float]:
    """Return background-Pod creation offsets, in seconds from churn start.

    Offsets are evenly spaced at `60 / rate_per_min` seconds apart,
    starting at 0.0, and strictly less than `duration_s`. There is no
    jitter: the same `(rate_per_min, duration_s)` pair always returns the
    same list, which is what makes a trial's actually-achieved creation
    rate checkable against its configured one.

    `rate_per_min <= 0` returns an empty schedule (no background churn at
    all) rather than raising, since `background_rate` already normalizes
    the "no churn" case to 0 before it reaches here.
    """
    if rate_per_min <= 0:
        return []
    period = 60.0 / rate_per_min
    offsets: list[float] = []
    t = 0.0
    while t < duration_s:
        offsets.append(t)
        t += period
    return offsets
