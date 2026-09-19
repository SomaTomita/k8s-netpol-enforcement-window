"""Sustained-run detection and window arithmetic.

This mirrors, at the level of behavior, the sustained-blocked detection
planned for `internal/probe/sustained.go` (issue #4): a single dropped
packet or transient error must not be misread as the moment enforcement
started. Requiring an unbroken run of `k` matching observations trades
robustness to that noise against how long the harness must wait before it
can *know* enforcement has begun: `k - 1` further observations past the
first match. It does not shift the value returned -- `first_sustained`
reports the offset of the run's *first* observation, not its last -- so
`t_blocked` is the first blocked attempt of the run whatever `k` is. See
docs/methodology.md, "t_blocked: definition".

This module works on plain observation dicts (`{"offset_ns": int,
"outcome": str}`) rather than a typed record, because the raw per-probe
JSONL schema is emitted by the Go prober (cmd/prober, issue #6, not yet
built). Keeping the contract to "the two keys this function reads" means
this analysis code does not need to change in lockstep with that schema.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def first_sustained(
    observations: Sequence[Mapping[str, Any]],
    k: int,
    want: str = "blocked",
) -> int | None:
    """Return the offset (ns) at which a run of `k` consecutive `want`
    outcomes begins, or `None` if no such run exists.

    `observations` must already be ordered by `offset_ns` ascending, the
    order the probe emits them in -- this function does not sort, so
    passing an out-of-order list silently produces a meaningless answer
    rather than raising, mirroring the planned Go implementation's
    assumption that its input is a time-ordered probe stream, not an
    arbitrary set.

    Any observation whose outcome does not match `want` -- including one
    classified as "error" -- breaks the run and resets the run length to
    zero. This is not special-cased: it falls out of counting consecutive
    matches, so an Error observation breaks a run exactly like an
    "allowed" observation would when `want="blocked"`.

    `want` is compared case-insensitively against each observation's
    `outcome`, so this works whether outcome strings are already
    lowercase (as in `want`'s default) or capitalized the way Go's
    `Outcome.String()` renders them (e.g. "Blocked").

    Raises `ValueError` if `k` is not a positive integer.
    """
    if k <= 0:
        raise ValueError(f"k must be a positive int, got {k!r}")

    wanted = want.lower()
    run_start_ns: int | None = None
    run_length = 0

    for obs in observations:
        if str(obs["outcome"]).lower() == wanted:
            if run_length == 0:
                run_start_ns = obs["offset_ns"]
            run_length += 1
            if run_length >= k:
                return run_start_ns
        else:
            run_length = 0
            run_start_ns = None

    return None


def compute_window(t_ready_ns: int, t_blocked_ns: int) -> int:
    """Return the unprotected window in nanoseconds: `t_blocked - t_ready`.

    Per docs/methodology.md: a positive result means the Pod was Ready
    while traffic that should have been blocked was still getting
    through -- an unprotected window existed. A negative or zero result
    means enforcement was already active at or before readiness, i.e. no
    unprotected window existed. This mirrors `RunRecord.Window()` in
    `internal/record/record.go`.
    """
    return t_blocked_ns - t_ready_ns
