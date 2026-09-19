"""Deterministic expansion of an experiment matrix spec into concrete Runs.

Experiment conditions (CNI, churn rate, policy set, repetitions) are
pre-registered in YAML data files under `experiments/`, not hardcoded here.
`expand()` is the only place the cartesian product is taken, so every
parameter combination that is pre-registered maps 1:1 to what actually
runs -- there is no code path that could silently drop, reorder, or
duplicate a condition relative to what was declared ahead of time.

`expand()` must never depend on wall-clock time or randomness: the same
spec always produces the same list of Runs in the same order, with the
same run_ids, so re-running the expansion (e.g. to resume or audit a
prior experiment) is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

_REQUIRED_KEYS = ("cni", "churn_rate_per_min", "policy_set", "repetitions")


@dataclass(frozen=True)
class Run:
    """A single concrete experiment run: one point in the matrix."""

    run_id: str
    cni: str
    churn_rate_per_min: int
    policy_set: str
    repetition: int


def expand(spec: dict) -> list[Run]:
    """Expand a matrix spec into the list of Runs it describes.

    `spec` must contain `cni`, `churn_rate_per_min`, and `policy_set`
    (each a list of values) and `repetitions` (a positive int). Missing
    keys raise `KeyError`; `repetitions <= 0` raises `ValueError`.

    The cartesian product of `cni` x `churn_rate_per_min` x `policy_set` is
    taken in the order the spec lists them, then repeated `repetitions`
    times. `run_id` is derived from the index of each (condition,
    repetition) pair in that fixed order, so it is unique and identical
    across calls on the same spec.
    """
    for key in _REQUIRED_KEYS:
        if key not in spec:
            raise KeyError(key)

    repetitions = spec["repetitions"]
    if repetitions <= 0:
        raise ValueError(f"repetitions must be a positive int, got {repetitions!r}")

    conditions = list(product(spec["cni"], spec["churn_rate_per_min"], spec["policy_set"]))

    runs = []
    for condition_index, (cni, churn_rate, policy_set) in enumerate(conditions):
        for repetition in range(repetitions):
            run_id = f"run-{condition_index:04d}-{repetition:03d}"
            runs.append(
                Run(
                    run_id=run_id,
                    cni=cni,
                    churn_rate_per_min=churn_rate,
                    policy_set=policy_set,
                    repetition=repetition,
                )
            )
    return runs
