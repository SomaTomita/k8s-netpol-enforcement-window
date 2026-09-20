"""Per-condition summary and pairwise CNI comparison: pure functions, no I/O.

One row per (CNI, churn rate, policy timing) condition, reporting median + percentile
bootstrap 95% CI (`npw.analysis.stats.bootstrap_ci`) as
`experiments/exp1-window/preregistration.md`'s Analysis plan requires,
plus the counts that plan promises to report alongside them: the
exclusion rate ("reported alongside results, not silently absorbed"),
both censoring counts, the B<->C divergence flag count, and the probe
interval `p` (docs/methodology.md: `p` "is therefore always reported
alongside any window value").

Censoring is the whole difficulty here, and the rules below exist to
stop a reported median being quietly biased -- downward by dropped
right-censored trials, upward by left-censored floors read as
measurements:

1. A **right-censored** trial (no sustained block ever seen; see
   `trial.TrialResult.right_censored`) is *included* by rank
   substitution, never dropped. Its true window is larger than every
   window actually observed in the condition, so it occupies a known
   rank at the top of the order. A median only needs ranks, so it stays
   exact while fewer than half the trials are censored. Filtering these
   rows out -- e.g. `window_ns is not None` -- would delete precisely
   the largest windows and reintroduce the downward bias that keeping
   them as censored observations exists to remove.
2. When **half or more** of a condition's included trials are
   right-censored, the median's own order statistic is one of the
   censored ones and no point estimate is identifiable. The condition
   then reports a lower bound (`median_lower_bound_ns`, "> x") and *no*
   interval, rather than a number that would read as measured. That
   bound is built by holding each trial at the lowest value its window
   could take (`_lower_bound_values`), which for a left-censored trial
   is `-inf`, not its recorded floor -- so a condition with enough
   floors honestly reports no bound at all.
3. A condition is reported as "no window detected above the floor", per
   preregistration.md's negative-results rule, when every included
   window is at or below that trial's probe interval `p`. Those are the
   protocol's own words -- "every trial's window is at or below the
   observation floor (`p`, and any residual harness latency)" -- and
   docs/methodology.md adds that a window of the same order as `p`
   "describes the measurement instrument, not the phenomenon". This is a
   predicate about values. Left-censoring is a different thing: "the
   harness's first-look floor" is not "the observation floor `p`", and a
   condition of floors recorded at tens of milliseconds is rule 4's
   upper bound, not a negative result.
4. When a left-censored trial sits **at or above the median's lowest
   rank**, the condition reports an upper bound
   (`median_upper_bound_ns`, "<= x") and no interval. This is the mirror
   of rule 2: a left-censored trial's true window is at or below its
   recorded value (the value is the harness's first-look floor), so such
   a trial at the median's rank overstates the median -- and one above
   it may really lie below the median and drag it down, which is why the
   test is a suffix of the order and not the median's rank alone. The
   bound is "<=" rather than "<" because it is attainable: if every
   floor's true window equals its record, the true median *is* that
   value. The check is rank-positional, never a count: a left-censored
   trial's recorded `t_blocked - t_ready` can sit anywhere in the
   condition's order, so a "half or more are left-censored" rule would
   fire both when it should not and not when it should.
5. A condition that keeps its median (rule 4 did not fire) can still
   hold floors *below* the median's rank, and those floors do reach its
   bootstrap CI: a resample can draw one into the median's rank. Since
   a recorded floor is an upper bound, a lower CI limit taken from one
   is not conservative -- and the lower limit is the one supporting
   "the window is at least X". The CI is therefore computed over
   `rank_substituted` values, with each floor at `-inf`, so such a
   limit comes out unbounded and is rendered as such instead of being
   printed as a measured number. The median itself is unaffected: rule
   4 guarantees every remaining floor is already below the median's
   rank, and moving it further down cannot change which trial occupies
   that rank.

Cross-CNI comparison is Mann-Whitney U per (churn level, policy
timing) with Holm-Bonferroni across the pairs tested. Note that preregistration.md
freezes only "pairwise, with multiple-comparison correction (method to
be named in the amendment that adds Calico/Antrea support -- not yet
decided)": the *method* implemented here is therefore not itself frozen
yet, and naming it in that amendment before Experiment 1's real runs is
still an open action for the author. Mann-Whitney is a rank test, so it
composes with the rules above: censored trials enter it at the extremes
of the ranking, right-censored tied at the top (valid exactly as far as
rule 1's assumption holds, which
`right_censoring_order_violated` reports on) and left-censored tied at
the bottom. The bottom end is a choice among the rankings the data
permits rather than a known rank -- see `rank_substituted` -- so each
pair reports its left- and right-censored counts and a p-value from a
heavily censored arm is read accordingly.
"""

from __future__ import annotations

import math
import warnings
from collections.abc import Sequence
from itertools import combinations

import numpy as np
from scipy import stats
from scipy.stats import DegenerateDataWarning

from npw.analysis.stats import bootstrap_ci
from npw.analysis.trial import TrialResult

# Column order for the summary and pairwise tables. Declared here rather
# than derived from a row dict so the CSV header is stable even when
# there are no rows to read it off -- the report CLI writes both files
# unconditionally.
SUMMARY_FIELDS = (
    "cni",
    "churn_rate_per_min",
    "policy_at",
    "n",
    "n_excluded",
    "exclusion_rate",
    "n_left_censored",
    "n_right_censored",
    "n_negative_window",
    "n_b_c_flagged",
    "estimate_kind",
    "median_ns",
    "ci_low_ns",
    "ci_high_ns",
    "median_lower_bound_ns",
    "median_upper_bound_ns",
    "probe_interval_ns",
    "probe_interval_varies",
    "right_censoring_order_violated",
)

PAIRWISE_FIELDS = (
    "churn_rate_per_min",
    "policy_at",
    "cni_a",
    "cni_b",
    "n_a",
    "n_b",
    "n_right_censored_a",
    "n_right_censored_b",
    "n_left_censored_a",
    "n_left_censored_b",
    "p_raw",
    "p_adj",
)


def _included(results: Sequence[TrialResult]) -> list[TrialResult]:
    """Drop excluded trials, and fail fast on a broken censoring contract.

    `trial.py` deliberately keeps a populated `window_ns` on an excluded
    trial so the exclusion rate is reportable, and its docstring makes
    filtering on `excluded_reason` the consumer's job. This is the only
    place that filter is applied; note it does *not* also filter on
    `window_ns is not None`, which would silently drop right-censored
    trials (see rule 1 in the module docstring).

    It is also the one gate every included trial passes through, so it
    is where `TrialResult`'s censoring contract -- `window_ns is None`
    if and only if `right_censored`, and a right-censored trial always
    carries its `censoring_time_ns` bound -- is enforced. A violation
    would otherwise reach the median as a `None` or as a missing bound,
    and every function below may then assume the contract holds.
    """
    included = [r for r in results if r.excluded_reason is None]
    for r in included:
        if r.window_ns is None and not r.right_censored:
            raise ValueError(f"{r.run_id}: window_ns is None but right_censored is False")
        if r.right_censored and r.censoring_time_ns is None:
            raise ValueError(f"{r.run_id}: right_censored but censoring_time_ns is None")
    return included


def _upper_bound_values(results: Sequence[TrialResult]) -> list[float]:
    """Each trial held at the highest value its true window could take.

    Right-censored trials go to `inf`; every other trial keeps its
    recorded value, which for a left-censored trial is its first-look
    floor -- an upper bound on that trial's true window, and the reason
    this list is the one that yields `median_upper_bound_ns` and the
    recorded order the identifiability predicate is stated in. Every
    element is >= that trial's true window.
    """
    return [math.inf if r.right_censored else float(r.window_ns) for r in results]


def rank_substituted(results: Sequence[TrialResult]) -> list[float]:
    """Window values with each censored trial ranked at its own extreme.

    Right-censored trials become `inf`, left-censored trials `-inf`, and
    everything else keeps its measured window. Both are rank
    placeholders, not claims about magnitude: `inf` encodes "larger than
    every window observed in this condition" and `-inf` "no larger than
    a floor that bounds it from above, and possibly far smaller". Any
    statistic that reads the *value* rather than the rank (a mean, say)
    would be meaningless on this list, which is why nothing here
    computes one.

    The `-inf` end is weaker than the `inf` end, and deliberately so. A
    right-censored trial's window really does exceed everything observed
    (as far as `right_censoring_order_violated` can confirm), so `inf`
    is the trial's true rank. A left-censored trial's window is only
    bounded above, so its true rank is unknown and `-inf` picks the
    lowest rank consistent with the data rather than a known one.
    Leaving it at its recorded floor -- the pre-fix behaviour -- picks
    the *highest* rank consistent with the data, which is the one choice
    that is certainly wrong, since the floor is an upper bound the truth
    need not attain. `summarize` only uses this list where the choice
    provably cannot move the median (rule 4 has already removed the
    conditions where it could), and `pairwise_cni` reports the
    left-censored counts so the exposure of a p-value to it is visible.

    Assumes `results` has already been through `_included`, which is
    what guarantees a non-right-censored trial has a `window_ns`.
    """
    values = []
    for r in results:
        if r.right_censored:
            values.append(math.inf)
        elif r.censored:
            values.append(-math.inf)
        else:
            values.append(float(r.window_ns))
    return values


def _lower_bound_values(results: Sequence[TrialResult]) -> list[float]:
    """Each trial held at the lowest value its true window could take.

    Every element is <= that trial's true window, so any order statistic
    of this list is a lower bound on the same order statistic of the
    truth. This is what makes the "> x" median of rule 2 defensible
    rather than arbitrary, and it only holds if each substitution really
    is a lower bound:

    - right-censored: `censoring_time_ns`, the bound the trial itself
      establishes (its true window is at least that).
    - left-censored: `-inf`. Its recorded value is an *upper* bound -- no
      Allowed was witnessed anywhere before `t_blocked`, so the truth is
      bounded above and not below -- and holding it at its recorded value
      would produce a "> x" that the data does not support.
    - uncensored: the measured window.

    A condition with enough floors can therefore have `-inf` as its
    honest lower bound, meaning the data establishes none. That is
    reported as such rather than rounded into a number.
    """
    values = []
    for r in results:
        if r.right_censored:
            values.append(float(r.censoring_time_ns))
        elif r.censored:
            values.append(-math.inf)
        else:
            values.append(float(r.window_ns))
    return values


def _median_is_identifiable(n: int, n_right_censored: int) -> bool:
    """True when the median's order statistic is an uncensored one.

    The top `n_right_censored` order statistics are the censored ones.
    The median needs order statistic `(n + 1) // 2` (odd n) or both
    `n // 2` and `n // 2 + 1` (even n); in both cases the highest rank it
    touches is uncensored exactly when `2 * n_right_censored < n`.
    """
    return n > 0 and 2 * n_right_censored < n


def _left_censoring_reaches_the_median(inc: Sequence[TrialResult]) -> bool:
    """True when a left-censored trial sits at or above the median's rank.

    With true values `t_i <= v_i` (equality for uncensored), the recorded
    median `v_(m)` is the sharp *upper* bound on the true median, and the
    sharp lower bound is `u_(m - c)`, the `(m - c)`-th smallest uncensored
    value, where `c` is the number of left-censored trials. A floor below
    the median's rank is already below it whatever its truth turns out to
    be, so it cancels from both bounds; a floor at or above that rank does
    not, and its true window may really lie under the median and drag the
    median down. Upper bound equals lower bound -- i.e. a point estimate
    is exact -- exactly when no floor sits at or above the median's lowest
    rank, which is what this checks. Checking only the median's own rank
    (an earlier version) left the same defect one rank up.

    Rank-positional by necessity: a left-censored trial's recorded window
    is still `t_blocked - t_ready` and can sit anywhere in the condition's
    order, so counting left-censored trials says nothing about where they
    land. Odd `n` has one median order statistic, even `n` averages two,
    and the lowest of those is where the suffix starts.

    Ties sort the censored trial to the *bottom* of its tie block
    (`not r.censored` sorts `False` first). That is not a presentation
    choice: a floor's truth is at or below its recorded value, so the
    bottom of the block is where it belongs, and a floor tied with the
    median value then leaves the median exact -- `[1ms floor, 1ms, 2ms]`
    has median 1ms whatever the floor really was.

    The order is the *recorded* one (`_upper_bound_values`), never
    `rank_substituted`'s: the sharp-bound algebra above is stated in
    terms of recorded ranks, and sorting floors to `-inf` first would
    put every floor below every median rank and make this predicate
    answer "no" always.
    """
    order = sorted(
        zip(_upper_bound_values(inc), inc, strict=True),
        key=lambda pair: (pair[0], not pair[1].censored),
    )
    order = [r for _, r in order]
    n = len(order)
    lowest_median_rank = (n - 1) // 2 if n % 2 else n // 2 - 1
    return any(r.censored for r in order[lowest_median_rank:])


def _all_at_or_below_floor(inc: Sequence[TrialResult]) -> bool:
    """True when preregistration.md's negative-results phrase applies.

    The protocol's predicate is about values: "every trial's window is at
    or below the observation floor (`p`, and any residual harness
    latency)". Left-censoring is *not* that predicate -- "the harness's
    first-look floor" and "the observation floor `p`" are different
    things, and a condition of floors recorded at tens of milliseconds
    says nothing about windows being at or below `p`. Such a condition is
    an `upper_bound` (rule 4), not a negative result, so being
    left-censored is neither necessary nor sufficient here and is not
    tested for.

    A right-censored trial rules the condition out: it has no window at
    all, and "enforcement never arrived" is the opposite of "nothing
    above the floor".
    """
    if any(r.right_censored for r in inc):
        return False
    return all(r.window_ns <= r.probe_interval_ns for r in inc)


def _right_censoring_order_violated(inc: Sequence[TrialResult]) -> bool:
    """True when a censoring time fails to exceed an observed window.

    Rank substitution (rule 1) puts every right-censored trial above every
    observed window, which is exact only while each censored trial's true
    window really is larger -- i.e. while its `censoring_time_ns` bound
    already exceeds every window observed in the condition. A truncated or
    short trial breaks that, and ranking it at the top then pushes the
    median up. Surfaced as a column rather than silently assumed; it
    changes no number, it tells the reader which numbers to distrust.
    """
    observed = [r.window_ns for r in inc if not r.right_censored]
    bounds = [r.censoring_time_ns for r in inc if r.right_censored]
    return bool(observed and bounds and min(bounds) <= max(observed))


def summarize(results: Sequence[TrialResult]) -> list[dict]:
    """One summary row per (cni, churn_rate_per_min, policy_at) condition, sorted.

    `estimate_kind` says which of the module docstring's cases a row is,
    and is the field to branch on when reading the row:

    - `"point"`: `median_ns` plus `ci_low_ns`/`ci_high_ns`. Either limit
      can be unbounded: `ci_high_ns` may be `inf` when right-censored
      trials are present, and `ci_low_ns` may be `-inf` when
      left-censored ones are -- a bootstrap resample can reach a censored
      value at the median's rank even when the sample does not, and an
      unbounded limit is the honest report of that, not a bug. Both
      limits are `None` together in two cases -- `n == 1`, which has no
      resampling distribution, and a condition censored at both ends
      whose resampled medians come out `nan` -- and `median_ns` is still
      a number in both. Every one of these cases is handled below.
    - `"lower_bound"`: rule 2. `median_ns` and both CI bounds are `None`;
      `median_lower_bound_ns` carries the "> x" value.
    - `"below_floor"`: rule 3. No median is reported at all.
    - `"upper_bound"`: rule 4. `median_ns` and both CI bounds are `None`;
      `median_upper_bound_ns` carries the "<= x" value (attainable, and
      rendered as `<=`; see `_left_censoring_reaches_the_median`).
    - `"no_data"`: every trial in the condition was excluded.

    `right_censoring_order_violated` marks a condition where rule 1's
    ordering assumption does not hold in the data; it changes no number
    here, it says which numbers to distrust.
    """
    rows = []
    for cni, churn, policy_at in sorted(
        {(r.cni, r.churn_rate_per_min, r.policy_at) for r in results}
    ):
        condition = [
            r
            for r in results
            if r.cni == cni and r.churn_rate_per_min == churn and r.policy_at == policy_at
        ]
        inc = _included(condition)
        n = len(inc)
        n_right = sum(1 for r in inc if r.right_censored)
        intervals = {r.probe_interval_ns for r in inc}

        median_ns: float | None = None
        ci_low: float | None = None
        ci_high: float | None = None
        lower_bound: float | None = None
        upper_bound: float | None = None

        if n == 0:
            kind = "no_data"
        elif not _median_is_identifiable(n, n_right):
            kind = "lower_bound"
            lower_bound = float(np.median(_lower_bound_values(inc)))
        elif _all_at_or_below_floor(inc):
            kind = "below_floor"
        elif _left_censoring_reaches_the_median(inc):
            # Every recorded value is >= that trial's true window (a
            # left-censored floor is above the truth, an uncensored value
            # is the truth, `inf` is trivially above it), so the median of
            # the recorded values bounds the true median from above.
            kind = "upper_bound"
            upper_bound = float(np.median(_upper_bound_values(inc)))
        else:
            kind = "point"
            # Rank-substituted, including `-inf` for left-censored trials:
            # rule 4 has already excluded every condition with a floor at
            # or above the median's rank, so all remaining floors are
            # strictly below it and pushing them lower cannot move the
            # median. The CI is a different matter -- a resample can draw
            # a floor into the median's rank, and then the lower limit
            # must be unbounded rather than a floor read as a measurement.
            values = rank_substituted(inc)
            median_ns = float(np.median(values))
            if n >= 2:
                # np.errstate: with censored values ranked at `inf`, the
                # resampled medians can include `inf`, and scipy computes a
                # standard error over them (`inf - inf`) on its way to the
                # percentile interval. That standard error is not read here
                # and the percentiles themselves are unaffected, so the
                # warning it raises is noise, not a signal.
                with warnings.catch_warnings(), np.errstate(invalid="ignore"):
                    # scipy reports a censoring-induced `nan` in the
                    # resampled medians as "the BCa confidence interval
                    # cannot be calculated", which is doubly misleading
                    # next to these numbers: `bootstrap_ci` asks for the
                    # percentile method, not BCa, and the condition it
                    # describes is handled two lines below. Suppressed
                    # here, surfaced there.
                    warnings.simplefilter("ignore", DegenerateDataWarning)
                    ci_low, ci_high = bootstrap_ci(values)
                if math.isnan(ci_low) or math.isnan(ci_high):
                    # A condition censored at both ends can produce a
                    # resample whose two middle values are `-inf` and
                    # `inf`; their mean is `nan`, so that resample has no
                    # median and the percentile interval has no value
                    # either. Report no interval rather than a `nan` that
                    # would print in a number's place.
                    ci_low = ci_high = None
            # n == 1 leaves the CI empty: one observation has no resampling
            # distribution (`bootstrap_ci` rejects it), and a zero-width
            # interval printed next to a single trial would read as
            # precision this condition does not have.

        rows.append(
            {
                "cni": cni,
                "churn_rate_per_min": churn,
                "policy_at": policy_at,
                "n": n,
                "n_excluded": len(condition) - n,
                "exclusion_rate": (len(condition) - n) / len(condition),
                "n_left_censored": sum(1 for r in inc if r.censored),
                "n_right_censored": n_right,
                # preregistration.md Addendum 1 D2's failure mode, made
                # visible in the report and not only in trials.csv: a
                # pre-ready `ECONNREFUSED` run of k observations is
                # classified `Blocked`, so the trial reports
                # `t_blocked < t_ready`. `_all_at_or_below_floor` returns
                # True for such a window (it is certainly <= `p`), so a
                # condition made entirely of them would otherwise print
                # the pre-registered "no window detected above the floor"
                # phrase with nothing marking it -- an artifact rendered
                # as a result. Counted, never excluded here: D2's
                # recorded response is investigation, not a filter.
                "n_negative_window": sum(
                    1 for r in inc if r.window_ns is not None and r.window_ns < 0
                ),
                "n_b_c_flagged": sum(1 for r in inc if r.b_c_flagged),
                "estimate_kind": kind,
                "median_ns": median_ns,
                "ci_low_ns": ci_low,
                "ci_high_ns": ci_high,
                "median_lower_bound_ns": lower_bound,
                "median_upper_bound_ns": upper_bound,
                # A condition that mixed probe intervals is not internally
                # comparable, and averaging `p` away would hide that, so the
                # single value is reported only when there is one.
                "probe_interval_ns": next(iter(intervals)) if len(intervals) == 1 else None,
                "probe_interval_varies": len(intervals) > 1,
                "right_censoring_order_violated": _right_censoring_order_violated(inc),
            }
        )
    return rows


def holm_adjust(p_raw: Sequence[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values, in the input's order.

    Each p-value is multiplied by the number of hypotheses not yet
    rejected at its step, capped at 1, and forced non-decreasing in rank
    order (the enforced monotonicity is what makes Holm uniformly more
    powerful than plain Bonferroni while still controlling the
    family-wise error rate).
    """
    m = len(p_raw)
    adjusted = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(sorted(range(m), key=lambda i: p_raw[i])):
        running = max(running, min(1.0, p_raw[idx] * (m - rank)))
        adjusted[idx] = running
    return adjusted


def _mann_whitney_p(a: Sequence[float], b: Sequence[float]) -> float:
    """Two-sided Mann-Whitney U p-value, with the all-ties case handled.

    When every value in both groups is identical, every permutation of
    the labels yields the same U, so the exact two-sided p-value is 1.
    scipy takes its asymptotic path once ties are present and divides by
    a tie-corrected variance of zero, returning `nan` -- which would then
    propagate through `holm_adjust` and silently void the whole family.
    This case is reachable in practice: a condition where every trial is
    right-censored is exactly one long run of tied `inf` ranks.

    Ties matter for more than this extreme. scipy's `method="auto"` takes
    the exact permutation distribution only when there are no ties and the
    groups are small; any right-censored trial creates ties, which forces
    the normal approximation, and that is anti-conservative at small n (a
    3-vs-3 complete separation reports p = 0.0636 where the exact
    two-sided minimum is 0.1). At the pre-registered 30 repetitions per
    condition the approximation is fine; a pilot-sized comparison read off
    this table is not, and should be treated as indicative only.
    """
    if len(set(a) | set(b)) == 1:
        return 1.0
    return float(stats.mannwhitneyu(a, b, alternative="two-sided").pvalue)


def pairwise_cni(
    results: Sequence[TrialResult], churn_rate_per_min: int, policy_at: str = "before"
) -> list[dict]:
    """Pairwise Mann-Whitney U between CNIs in one (churn level, policy_at) cell, Holm-adjusted.

    Right-censored trials take part, tied at the top of the ranking (see
    `rank_substituted`); `n_right_censored_a`/`_b` are reported so a
    reader can judge how much of a pair's result rests on that
    substitution rather than on observed windows.

    Holm's family is the pairs tested within this one (churn level,
    policy_at) condition. Each call controls the family-wise error rate
    at 0.05 across its own level, so a report covering three churn levels
    prints up to nine comparisons controlled in three families of three,
    not one family of nine. That is what makes a printed `p_adj`
    interpretable, and it is the sentence to carry into any write-up of
    this table; treating the whole table as one family would require a
    further correction that is deliberately not applied here, because the
    churn levels are separate pre-registered conditions rather than
    repeated tests of one question.

    A CNI with no included trials at this churn level has no group and
    so is not compared -- adjusting across three pairs when only one was
    computable would be over-correcting against tests that never
    happened. The summary table still shows that CNI's row with
    `n = 0`, so a silently missing arm is visible there rather than only
    by a pair's absence here.
    """
    inc = [
        r
        for r in _included(results)
        if r.churn_rate_per_min == churn_rate_per_min and r.policy_at == policy_at
    ]
    groups = {cni: [r for r in inc if r.cni == cni] for cni in sorted({r.cni for r in inc})}
    pairs = list(combinations(groups, 2))
    p_raw = [
        _mann_whitney_p(rank_substituted(groups[a]), rank_substituted(groups[b])) for a, b in pairs
    ]
    p_adj = holm_adjust(p_raw)
    return [
        {
            "churn_rate_per_min": churn_rate_per_min,
            "policy_at": policy_at,
            "cni_a": a,
            "cni_b": b,
            "n_a": len(groups[a]),
            "n_b": len(groups[b]),
            "n_right_censored_a": sum(1 for r in groups[a] if r.right_censored),
            "n_right_censored_b": sum(1 for r in groups[b] if r.right_censored),
            "n_left_censored_a": sum(1 for r in groups[a] if r.censored),
            "n_left_censored_b": sum(1 for r in groups[b] if r.censored),
            "p_raw": raw,
            "p_adj": adj,
        }
        for (a, b), raw, adj in zip(pairs, p_raw, p_adj, strict=True)
    ]


def _ms(ns: float | None) -> str:
    """Nanoseconds as milliseconds, or a word for the non-numeric cases."""
    if ns is None:
        return "n/a"
    if math.isinf(ns):
        return "unbounded"
    return f"{ns / 1e6:.2f}"


def _estimate_cells(row: dict) -> tuple[str, str]:
    """The (median, 95% CI) cells for one summary row's `estimate_kind`."""
    kind = row["estimate_kind"]
    if kind == "point":
        if row["ci_low_ns"] is None:
            reason = "n = 1" if row["n"] == 1 else "censoring at both ends leaves it undefined"
            return _ms(row["median_ns"]), f"n/a ({reason})"
        return _ms(row["median_ns"]), f"[{_ms(row['ci_low_ns'])}, {_ms(row['ci_high_ns'])}]"
    if kind == "lower_bound":
        bound = row["median_lower_bound_ns"]
        if math.isinf(bound):
            # -inf: left-censored floors reach the median's rank, and a
            # floor bounds its trial from above only, so the data
            # establishes no lower bound at all for this condition.
            return (
                "no bound identified",
                "n/a (>= half right-censored, and left-censored floors reach the median)",
            )
        return f"> {_ms(bound)}", "n/a (>= half the trials right-censored)"
    if kind == "upper_bound":
        return (
            f"<= {_ms(row['median_upper_bound_ns'])}",
            "n/a (a left-censored floor sits at or above the median's rank)",
        )
    if kind == "below_floor":
        return "no window detected above the floor", "n/a"
    return "n/a (every trial excluded)", "n/a"


def render_markdown(summary_rows: Sequence[dict], pairwise_rows: Sequence[Sequence[dict]]) -> str:
    """Render the summary and pairwise tables as the thesis-facing report.

    `pairwise_rows` is one list per (churn level, policy timing) cell
    (the shape `pairwise_cni` returns), so the caller decides which cells
    to include rather than this function re-deriving them.
    """
    lines = [
        "# Experiment results: unprotected window by CNI, churn rate and policy timing",
        "",
        (
            "All times in milliseconds. `p` is the probe interval, the prober's polling "
            "period (docs/methodology.md, Observation resolution): a median of the same "
            "order of magnitude as `p` describes the instrument, not the phenomenon."
        ),
        "",
        (
            "Right-censored trials (enforcement never observed within the trial) are "
            "included by rank substitution, never dropped: they rank above every "
            "observed window, and a condition that is at least half right-censored "
            'reports "> x" -- or "no bound identified" where left-censored floors '
            "reach its median rank as well. Left-censored trials sit at the harness's "
            "first-look floor, so their value bounds the true window from above rather "
            "than measuring it; they rank below every observed window, and a condition "
            'with a floor at or above its median rank reports "<= x". Neither bound '
            "carries a confidence interval, and a CI limit drawn from a floor is "
            "reported as unbounded rather than as a measured number."
        ),
        "",
        (
            "`neg. window` counts included trials whose `t_blocked` precedes `t_ready` "
            "(preregistration.md Addendum 1 D2). Such a trial is at or below the "
            "observation floor by construction, so a non-zero count next to a "
            '"no window detected above the floor" row is the signal to investigate '
            "that row before reading it as a result."
        ),
        "",
        (
            "| CNI | churn/min | policy at | n | excluded | excl. rate | left-cens. | right-cens. | "
            "neg. window | B/C flagged | p (ms) | median (ms) | 95% CI (ms) |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for row in summary_rows:
        median_cell, ci_cell = _estimate_cells(row)
        probe = "varies" if row["probe_interval_varies"] else _ms(row["probe_interval_ns"])
        if row["probe_interval_varies"]:
            # `p` is part of what a window value means (methodology.md), so
            # a condition that mixed intervals is not internally comparable
            # -- the median cell has to carry that, not only the `p` cell.
            median_cell += " (mixed p)"
        lines.append(
            f"| {row['cni']} | {row['churn_rate_per_min']} | {row['policy_at']} | {row['n']} | "
            f"{row['n_excluded']} | {row['exclusion_rate']:.3f} | "
            f"{row['n_left_censored']} | {row['n_right_censored']} | "
            f"{row['n_negative_window']} | "
            f"{row['n_b_c_flagged']} | {probe} | {median_cell} | {ci_cell} |"
        )

    violated = [
        f"{row['cni']} @ {row['churn_rate_per_min']}/min, {row['policy_at']}"
        for row in summary_rows
        if row["right_censoring_order_violated"]
    ]
    if violated:
        lines += [
            "",
            (
                "Rank substitution assumes every right-censored trial's window exceeds "
                "every window observed in its condition. That does not hold for "
                + ", ".join(violated)
                + ": a censoring time there is at or below an observed window, so the "
                "ordering those rows rest on is not established by the data. Where a "
                "median was rank-substituted it is pushed up by the substitution; "
                'where a "> x" bound was computed from the censoring times it is '
                "pulled down, which is conservative. Read those rows as provisional."
            ),
        ]

    lines += [
        "",
        "## Pairwise CNI comparison",
        "",
        (
            "Mann-Whitney U (two-sided) per (churn level, policy timing) condition, "
            "Holm-Bonferroni adjusted across the pairs tested. Censored trials are "
            "ranked, never dropped: right-censored ones enter tied at the top of the "
            "ranking, left-censored ones tied at the bottom. Both counts are in the "
            "table below, per side of each pair."
        ),
        "",
        (
            "| churn/min | policy at | pair | n a | n b | right-cens. a | right-cens. b | "
            "left-cens. a | left-cens. b | p raw | p Holm |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    flat = [p for level in pairwise_rows for p in level]
    if not flat:
        lines.append("| _no pair had two CNIs with included trials_ | | | | | | | | | | |")
    for p in flat:
        lines.append(
            f"| {p['churn_rate_per_min']} | {p['policy_at']} | {p['cni_a']} vs {p['cni_b']} | "
            f"{p['n_a']} | {p['n_b']} | {p['n_right_censored_a']} | {p['n_right_censored_b']} | "
            f"{p['n_left_censored_a']} | {p['n_left_censored_b']} | "
            f"{p['p_raw']:.3g} | {p['p_adj']:.3g} |"
        )
    return "\n".join(lines) + "\n"
