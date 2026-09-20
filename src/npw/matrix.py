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

# When, relative to the victim, the trial script applies the NetworkPolicy.
# "before" is Experiment 1's ordering and the default, so a matrix that
# does not mention policy_at expands exactly as it always has.
# scripts/trial.sh validates the same three strings; keep them in sync.
POLICY_AT_VALUES = ("before", "with-victim", "at-ready")


@dataclass(frozen=True)
class Run:
    """A single concrete experiment run: one point in the matrix."""

    run_id: str
    cni: str
    churn_rate_per_min: int
    policy_set: str
    repetition: int
    policy_at: str = "before"


def expand(spec: dict) -> list[Run]:
    """Expand a matrix spec into the list of Runs it describes.

    `spec` must contain `cni`, `churn_rate_per_min`, and `policy_set`
    (each a list of values) and `repetitions` (a positive int). Missing
    keys raise `KeyError`; `repetitions <= 0` raises `ValueError`.
    `policy_at` is optional and defaults to `["before"]`; a value outside
    `POLICY_AT_VALUES` raises `ValueError`.

    The cartesian product of `cni` x `churn_rate_per_min` x `policy_set`
    x `policy_at` is taken in the order the spec lists them, then repeated
    `repetitions` times. `run_id` is derived from the index of each
    (condition, repetition) pair in that fixed order, so it is unique and
    identical across calls on the same spec. Because `policy_at` is the
    innermost factor and defaults to a single value, a spec without it
    yields the same run_ids it did before the factor existed.
    """
    for key in _REQUIRED_KEYS:
        if key not in spec:
            raise KeyError(key)

    repetitions = spec["repetitions"]
    if repetitions <= 0:
        raise ValueError(f"repetitions must be a positive int, got {repetitions!r}")

    policy_at_values = list(spec.get("policy_at", ["before"]))
    unknown = [v for v in policy_at_values if v not in POLICY_AT_VALUES]
    if unknown:
        raise ValueError(f"policy_at must be one of {POLICY_AT_VALUES}, got {unknown!r}")

    conditions = list(
        product(spec["cni"], spec["churn_rate_per_min"], spec["policy_set"], policy_at_values)
    )

    runs = []
    for condition_index, (cni, churn_rate, policy_set, policy_at) in enumerate(conditions):
        for repetition in range(repetitions):
            run_id = f"run-{condition_index:04d}-{repetition:03d}"
            runs.append(
                Run(
                    run_id=run_id,
                    cni=cni,
                    churn_rate_per_min=churn_rate,
                    policy_set=policy_set,
                    repetition=repetition,
                    policy_at=policy_at,
                )
            )
    return runs
