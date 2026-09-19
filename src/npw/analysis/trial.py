"""Load one data/raw/exp1/<run_id>/ directory and evaluate it per the
pre-registration: window = first_sustained(k) - t_ready(C), exclusion on
error_rate > 0.05, B<->C divergence flag, and censoring at both ends of a
trial when no real transition was observed:

- left-censored: no Allowed observation was ever seen before t_blocked,
  so the sustained-block run's start is not a witnessed Allowed->Blocked
  transition -- it is the harness's own first-look floor, an *upper*
  bound on the true window (the transition happened at or before it),
  not a measurement.
- right-censored: no sustained k-run of Blocked was found at all within
  the trial's duration -- enforcement never arrived while this trial was
  watching, so the true window is at least the trial's own length. This
  is the single largest unprotected window this trial could report, not
  a failed measurement, so it does NOT exclude the trial
  (preregistration.md's "Exclusion criteria" names only
  error_rate > 0.05, and nothing else).

A trial whose `excluded_reason` is set still carries a fully computed
`window_ns` whenever one exists. Consumers (Task 9) must filter on
`excluded_reason is None` before aggregating -- `window_ns` is not
blanked out for an excluded row, because "the exclusion rate itself is
reported alongside results" (preregistration.md) requires seeing what
was excluded, not just that it happened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from npw.analysis.window import compute_window, first_sustained

# preregistration.md's Exclusion criteria is one closed sentence naming
# error_rate > 0.05 and nothing else. "no sustained block found" is
# deliberately NOT an entry here -- see right_censored on TrialResult --
# and must not be added without a documented pre-registration amendment.
ERROR_RATE_MAX = 0.05

# The pre-registered B<->C divergence threshold (preregistration.md,
# Analysis plan: "diverge by more than the ADR 0003 pilot's observed
# range (~5ms)"; ADR 0003's pilot measured 4.4-5.4ms across its 3 pilot
# trials. Larger skews have been recorded since -- 11.5ms in
# 20260809T060534Z; see preregistration.md's Addendum 1 B2).
# This is deliberately NOT the same value as scripts/trial.sh's own
# B_C_SKEW_LIMIT_NS (50ms). That constant is a coarse runtime canary --
# "is the harness badly broken right now" -- checked while a trial is
# still running; this one is the frozen analysis-time criterion that
# decides whether a *reported* window can be trusted. The two answer
# different questions at different times with different tolerances, and
# are not meant to agree: a trial can pass trial.sh's 50ms canary and
# still fail this 5ms check without either threshold being wrong. Do not
# "harmonise" them.
BC_GAP_MAX_NS = 5_000_000


@dataclass(frozen=True)
class Trial:
    """One trial directory's raw inputs, as loaded from disk.

    `observations` is the raw prober.jsonl stream, ascending by
    offset_ns (load_trial rejects anything else -- first_sustained's own
    docstring warns that unsorted input "silently produces a meaningless
    answer rather than raising"). `t_ready_b_ns`/`t_ready_c_ns` are
    nanosecond offsets from the run's shared -run-epoch anchor
    (docs/methodology.md, "Clocks"), so they are directly comparable to
    each other and to `observations`' offsets.
    """

    run_id: str
    cni: str
    churn_rate_per_min: int
    repetition: int
    k: int
    probe_interval_ns: int
    observations: list[dict[str, Any]]
    t_ready_c_ns: int
    t_ready_b_ns: int


@dataclass(frozen=True)
class TrialResult:
    """One evaluated trial: flat and `dataclasses`-CSV-friendly (Task 9).

    `window_ns` is populated whenever a sustained Blocked run was found
    at all -- including when `excluded_reason` is set, so an excluded
    trial's own window value stays visible for the exclusion-rate report
    preregistration.md requires. `window_ns is None` if and only if
    `right_censored` is True: it does NOT mean "zero window", it means no
    sustained Blocked run was ever found in the trial. (`evaluate` raises
    rather than returning a `TrialResult` for an empty observation
    stream, so that degenerate case can't also produce `window_ns is
    None` here.) Consumers must filter on `excluded_reason is None`
    before aggregating.

    `censored` (left-censoring): True when the sustained-block run's
    start was never preceded, anywhere in the stream, by an observed
    Allowed outcome. There is then no witnessed Allowed->Blocked
    transition, so `window_ns` is only the harness's first-look floor,
    not a measured window.

    `right_censored`: True when no sustained k-run of Blocked was found
    anywhere in the trial (`window_ns` is `None` in this case).
    Enforcement never arrived while this trial was watching, so the true
    window is *at least* the trial's own duration -- the worst case this
    project measures, not a failed measurement. It therefore never sets
    `excluded_reason` on its own. Because a right-censored trial has no
    measured window at all, `censoring_time_ns` carries the one number
    that makes it usable in a bounded estimate instead of only a count:
    "the window exceeded `censoring_time_ns` ns in this trial."

    `censoring_time_ns` is `last_observation_offset_ns - t_ready_c_ns`
    when `right_censored` is True, else `None`. It states the bound
    directly (unlike an observation count, which would need
    `probe_interval_ns` and the trial's dial-timeout behavior applied
    externally to mean the same thing).

    `b_c_skew_ns` is `t_ready_b_ns - t_ready_c_ns`: the same field name
    and sign convention as trial.json's `b_c_skew_ns` (scripts/trial.sh)
    and ADR 0003's pilot table ("B - C"). C is expected to follow B (the
    victim's listener opens after the container process B measures has
    already started), so the ordinary case is a small *negative* value.
    A positive value is the impossible-ordering alarm ADR 0003 warns
    about (C before B), not a cosmetic sign choice -- getting this sign
    backwards would silently invert the one signal that flags it.
    """

    run_id: str
    cni: str
    churn_rate_per_min: int
    repetition: int
    window_ns: int | None
    censored: bool
    right_censored: bool
    censoring_time_ns: int | None
    error_rate: float
    b_c_skew_ns: int
    b_c_flagged: bool
    excluded_reason: str | None
    probe_interval_ns: int


def _jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file, skipping blank lines.

    A malformed line raises `ValueError` naming the file and line number,
    rather than letting a bare `json.JSONDecodeError` -- which says
    nothing about which of this trial's several JSONL files was being
    read -- propagate unnamed.
    """
    records = []
    for lineno, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}: line {lineno} is not valid JSON: {e}") from e
    return records


def _one_t_ready_record(path: Path, method: str) -> dict[str, Any]:
    """Return the single record for candidate `method` ("B" or "C") in `path`.

    Mirrors scripts/trial.sh's own parsing of victim.jsonl: that file is
    `kubectl logs deploy/victim`, the victim container's entire stdout,
    which may hold lines other than the one candidate-C self-report, so
    trial.sh filters by `t_ready_method` first and only *then* requires
    exactly one match -- it does not require the whole file to already
    be exactly one record. Requiring the latter here would hard-fail a
    trial that trial.sh itself accepted, checksummed and marked
    complete. cri.jsonl is written by trial.sh as exactly one line, so
    this is equally correct there, just never exercised past its first
    match.
    """
    matching = [r for r in _jsonl(path) if r.get("t_ready_method") == method]
    if len(matching) != 1:
        raise ValueError(
            f"{path}: expected exactly one candidate-{method} record, found {len(matching)}"
        )
    return matching[0]


def load_trial(run_dir: Path) -> Trial:
    """Load one trial directory into a `Trial`.

    Fails clearly (via `ValueError`, naming the offending path) on the
    ways a real trial directory can violate what this module needs: a
    victim.jsonl/cri.jsonl without exactly one matching B/C record, a
    malformed JSONL line, or a prober.jsonl whose offsets are not
    ascending. A missing/malformed meta.json key raises a `KeyError`
    naming that key -- every key read here is written unconditionally by
    `runner.write_meta`, so this only fires on a hand-edited or
    otherwise corrupt meta.json.
    """
    meta = json.loads((run_dir / "meta.json").read_text())
    c = _one_t_ready_record(run_dir / "victim.jsonl", "C")
    b = _one_t_ready_record(run_dir / "cri.jsonl", "B")
    observations = _jsonl(run_dir / "prober.jsonl")
    offsets = [o["offset_ns"] for o in observations]
    if offsets != sorted(offsets):
        raise ValueError(
            f"{run_dir / 'prober.jsonl'}: offsets are not ascending -- "
            "first_sustained's result would be meaningless on unsorted input"
        )
    return Trial(
        run_id=meta["run_id"],
        cni=meta["cni"],
        churn_rate_per_min=int(meta["churn_rate_per_min"]),
        repetition=int(meta["repetition"]),
        k=int(meta["sustained_k"]),
        probe_interval_ns=int(meta["probe_interval_ns"]),
        observations=observations,
        t_ready_c_ns=int(c["offset_ns"]),
        t_ready_b_ns=int(b["offset_ns"]),
    )


def evaluate(t: Trial) -> TrialResult:
    n = len(t.observations)
    if n == 0:
        # An empty prober.jsonl is a corrupt trial directory, not a
        # trial outcome: scripts/trial.sh's own `[ -s ... ]` check
        # already refuses to checksum one, so this cannot occur in a
        # well-formed dataset. Reporting it as an excluded row (an
        # earlier version of this function used excluded_reason =
        # "no_observations") would add a category preregistration.md's
        # Exclusion criteria does not name, inflating the pre-registered
        # exclusion rate with a data-integrity failure instead of
        # surfacing it as one.
        raise ValueError(
            f"{t.run_id}: prober.jsonl has no observations; this is a corrupt trial "
            "directory (scripts/trial.sh refuses to checksum an empty prober.jsonl), "
            "not an excluded or censored trial"
        )

    errors = sum(1 for o in t.observations if str(o["outcome"]).lower() == "error")
    error_rate = errors / n
    excluded_reason = "error_rate>0.05" if error_rate > ERROR_RATE_MAX else None

    b_c_skew_ns = t.t_ready_b_ns - t.t_ready_c_ns
    b_c_flagged = abs(b_c_skew_ns) > BC_GAP_MAX_NS

    # first_sustained has no lower bound at t_ready on its own (Addendum 1
    # D2): a dial made before the victim's listener opens -- but after its
    # Pod already has an IP -- gets ECONNREFUSED, which
    # internal/probe/outcome.go classifies Blocked exactly like real
    # enforcement. If that pre-ready run happens to reach length k, an
    # unfiltered scan finds it first and reports t_blocked < t_ready: a
    # harness artefact printed as a negative "measurement" rather than as
    # what it is. This is not a hypothetical -- it was observed live
    # during the Task-11.5 positive control (Antrea, run
    # pc-antrea-2000ms): four pre-ready Blocked observations, immediately
    # followed by genuinely Allowed traffic once the listener opened.
    # Restricting the search to observations at or after t_ready removes
    # exactly that artefact and nothing else: t_ready is a fact about the
    # victim, not a policy decision, so no post-ready observation is ever
    # discarded by this filter.
    post_ready = [o for o in t.observations if o["offset_ns"] >= t.t_ready_c_ns]
    t_blocked = first_sustained(post_ready, t.k, want="blocked")
    right_censored = t_blocked is None
    window = compute_window(t.t_ready_c_ns, t_blocked) if t_blocked is not None else None
    censoring_time_ns = t.observations[-1]["offset_ns"] - t.t_ready_c_ns if right_censored else None

    # Left-censored: no Allowed observation was ever seen strictly
    # before t_blocked, so there is no witnessed Allowed->Blocked
    # transition -- the sustained run's start is only the harness's
    # first-look floor. Checked by scanning every observation's own
    # offset_ns against t_blocked, not by comparing t_blocked to
    # observations[0]'s offset: a leading Error (or any other
    # non-Allowed noise) before the sustained run must not be mistaken
    # for "a transition was witnessed", and two observations can
    # legitimately share an offset_ns, which an equality-on-offset check
    # against a single fixed observation would get wrong. Scanned over
    # post_ready, the same filtered stream t_blocked was found in, so a
    # pre-ready artefact observation cannot masquerade as the witnessed
    # Allowed half of a transition either.
    censored = t_blocked is not None and not any(
        str(o["outcome"]).lower() == "allowed" and o["offset_ns"] < t_blocked
        for o in post_ready
    )

    return TrialResult(
        run_id=t.run_id,
        cni=t.cni,
        churn_rate_per_min=t.churn_rate_per_min,
        repetition=t.repetition,
        window_ns=window,
        censored=censored,
        right_censored=right_censored,
        censoring_time_ns=censoring_time_ns,
        error_rate=error_rate,
        b_c_skew_ns=b_c_skew_ns,
        b_c_flagged=b_c_flagged,
        excluded_reason=excluded_reason,
        probe_interval_ns=t.probe_interval_ns,
    )
