"""Tests for the pure per-condition summary and pairwise CNI comparison.

Every fixture here is synthetic: this repository has no Experiment 1
data yet, and CLAUDE.md forbids inventing any. These numbers exist to
pin down the *arithmetic* of the censoring rules, not to stand in for a
measurement.
"""

import math

import pytest

from npw.analysis.report import (
    holm_adjust,
    pairwise_cni,
    render_markdown,
    summarize,
)
from npw.analysis.trial import TrialResult

MS = 1_000_000


def _tr(
    cni="cilium",
    churn=1,
    rep=0,
    window_ns=1 * MS,
    censored=False,
    right_censored=False,
    censoring_time_ns=None,
    error_rate=0.0,
    b_c_skew_ns=0,
    b_c_flagged=False,
    excluded_reason=None,
    probe_interval_ns=1 * MS,
):
    """One TrialResult, keyword-only at the call site for readability."""
    return TrialResult(
        run_id=f"{cni}-{churn}-{rep}",
        cni=cni,
        churn_rate_per_min=churn,
        repetition=rep,
        window_ns=window_ns,
        censored=censored,
        right_censored=right_censored,
        censoring_time_ns=censoring_time_ns,
        error_rate=error_rate,
        b_c_skew_ns=b_c_skew_ns,
        b_c_flagged=b_c_flagged,
        excluded_reason=excluded_reason,
        probe_interval_ns=probe_interval_ns,
    )


def _right_censored(cni="cilium", churn=1, rep=0, censoring_time_ns=50 * MS):
    """A trial where enforcement never arrived: no window, only a bound."""
    return _tr(
        cni=cni,
        churn=churn,
        rep=rep,
        window_ns=None,
        right_censored=True,
        censoring_time_ns=censoring_time_ns,
    )


def test_summarize_reports_median_ci_and_exclusion_rate():
    results = [_tr(rep=i, window_ns=1 * MS + i * 1000) for i in range(30)]
    results.append(
        _tr(rep=99, window_ns=900 * MS, error_rate=0.9, excluded_reason="error_rate>0.05")
    )

    rows = summarize(results)

    assert len(rows) == 1
    row = rows[0]
    assert row["cni"] == "cilium"
    assert row["churn_rate_per_min"] == 1
    assert row["n"] == 30
    assert row["n_excluded"] == 1
    assert row["exclusion_rate"] == pytest.approx(1 / 31)
    assert row["estimate_kind"] == "point"
    assert row["n_left_censored"] == 0
    assert row["n_right_censored"] == 0
    assert row["ci_low_ns"] <= row["median_ns"] <= row["ci_high_ns"]
    assert row["probe_interval_ns"] == 1 * MS
    assert row["probe_interval_varies"] is False


def test_excluded_trial_never_moves_the_median():
    """An excluded trial keeps its window_ns (trial.py's contract) purely so
    the exclusion rate is reportable -- it must not reach a statistic."""
    clean = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(5)]
    poisoned = clean + [
        _tr(rep=9, window_ns=10_000 * MS, error_rate=0.5, excluded_reason="error_rate>0.05")
    ]

    assert summarize(poisoned)[0]["median_ns"] == summarize(clean)[0]["median_ns"] == 3 * MS


def test_right_censored_trials_are_included_by_rank_substitution():
    """The central ruling: a right-censored trial is the *largest* window
    the trial could have reported, so it sets its own rank rather than
    being dropped. Dropping the two censored trials here would give a
    median of 3ms; keeping them at the top of the order gives 4ms."""
    results = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(5)]
    results += [_right_censored(rep=10 + i, censoring_time_ns=60 * MS) for i in range(2)]

    row = summarize(results)[0]

    assert row["n"] == 7
    assert row["n_right_censored"] == 2
    assert row["estimate_kind"] == "point"
    assert row["median_ns"] == 4 * MS


def test_median_is_a_lower_bound_when_half_the_trials_are_right_censored():
    """At >= 50% right-censoring the median's own order statistic is itself
    censored, so no point estimate is identifiable -- report "> x" and no
    interval rather than a number that looks measured."""
    results = [_tr(rep=0, window_ns=2 * MS), _tr(rep=1, window_ns=4 * MS)]
    results += [
        _right_censored(rep=2, censoring_time_ns=50 * MS),
        _right_censored(rep=3, censoring_time_ns=70 * MS),
    ]

    row = summarize(results)[0]

    assert row["n"] == 4
    assert row["n_right_censored"] == 2
    assert row["estimate_kind"] == "lower_bound"
    assert row["median_ns"] is None
    assert row["ci_low_ns"] is None and row["ci_high_ns"] is None
    # median of [2, 4, >=50, >=70] with each censored trial held at its own
    # bound: (4 + 50) / 2 = 27ms, a value the true median cannot be below.
    assert row["median_lower_bound_ns"] == 27 * MS


def test_all_left_censored_condition_reports_no_window_above_the_floor():
    """preregistration.md's negative-results rule: every trial at or below
    the observation floor is "no window detected above the floor for this
    condition", not a median of floors."""
    results = [_tr(rep=i, window_ns=500_000, censored=True) for i in range(30)]

    row = summarize(results)[0]

    assert row["n"] == 30
    assert row["n_left_censored"] == 30
    assert row["estimate_kind"] == "below_floor"
    assert row["median_ns"] is None
    assert row["ci_low_ns"] is None and row["ci_high_ns"] is None


def test_left_censoring_below_the_median_rank_still_reports_a_median():
    """One floor at the bottom of the order does not touch the median's own
    order statistic, so the median is still a measured value."""
    results = [_tr(rep=0, window_ns=1 * MS, censored=True)]
    results += [_tr(rep=i, window_ns=(2 * i + 1) * MS) for i in range(1, 5)]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "point"
    assert row["n_left_censored"] == 1
    assert row["median_ns"] == 5 * MS


def test_median_landing_on_a_left_censored_trial_reports_an_upper_bound():
    """The mirror of rule 2. Three floors at 1ms plus 5ms and 7ms puts a
    censoring floor on the median's own rank: reporting 1.00ms with a CI of
    [1.00, 7.00] would present the harness's first-look floor as a measured
    median and overstate the window, since a left-censored trial's true
    window is *below* its recorded value."""
    results = [_tr(rep=i, window_ns=1 * MS, censored=True) for i in range(3)]
    results += [_tr(rep=3, window_ns=5 * MS), _tr(rep=4, window_ns=7 * MS)]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "upper_bound"
    assert row["median_ns"] is None
    assert row["ci_low_ns"] is None and row["ci_high_ns"] is None
    assert row["median_upper_bound_ns"] == 1 * MS


def test_even_n_median_reports_an_upper_bound_if_either_middle_is_censored():
    """For even n the median averages two order statistics, so either one
    being a floor contaminates it."""
    contaminated = [_tr(rep=0, window_ns=1 * MS, censored=True)]
    contaminated += [_tr(rep=1, window_ns=3 * MS, censored=True)]
    contaminated += [_tr(rep=2, window_ns=5 * MS), _tr(rep=3, window_ns=7 * MS)]

    row = summarize(contaminated)[0]

    assert row["estimate_kind"] == "upper_bound"
    assert row["median_upper_bound_ns"] == 4 * MS


def test_left_censoring_above_the_median_rank_also_reports_an_upper_bound():
    """A floor *above* the median's rank is not harmless: its true window is
    only bounded above, so it may really lie below the median and drag the
    median down with it. With floors at 8ms and 9ms over 1/2/3ms, the true
    median is identified only to [1ms, 3ms] -- set those floors to 0.1 and
    0.2ms and the data is equally consistent with a median of 1ms.

    Two wrong rules are pinned out here: a count rule (`2 * 2 >= 5` is
    false) and a "the median's own rank is a floor" rule (rank 3 is the
    measured 3ms) both call this a point estimate with a
    [1.00, 9.00] CI whose upper limit is itself a censoring floor."""
    results = [_tr(rep=0, window_ns=1 * MS), _tr(rep=1, window_ns=2 * MS)]
    results += [_tr(rep=2, window_ns=8 * MS, censored=True)]
    results += [_tr(rep=3, window_ns=9 * MS, censored=True)]
    results.append(_tr(rep=4, window_ns=3 * MS))

    row = summarize(results)[0]

    assert row["n_left_censored"] == 2
    assert row["estimate_kind"] == "upper_bound"
    assert row["median_ns"] is None
    assert row["ci_low_ns"] is None and row["ci_high_ns"] is None
    assert row["median_upper_bound_ns"] == 3 * MS


def test_even_n_upper_bound_when_a_floor_sits_above_both_middles():
    results = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(4)]
    results += [_tr(rep=4, window_ns=5 * MS, censored=True)]
    results += [_tr(rep=5, window_ns=6 * MS, censored=True)]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "upper_bound"
    assert row["median_upper_bound_ns"] == 3.5 * MS


def test_a_floor_tied_with_the_median_value_still_gives_an_exact_median():
    """A left-censored trial's true window is at or below its recorded one,
    so among equal values it belongs at the bottom of the tie block. With
    [1ms floor, 1ms, 2ms] the median is 1ms whatever the floor's truth is,
    so the condition is exactly identified and reports a point estimate."""
    results = [
        _tr(rep=0, window_ns=1 * MS, censored=True),
        _tr(rep=1, window_ns=1 * MS),
        _tr(rep=2, window_ns=2 * MS),
    ]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "point"
    assert row["median_ns"] == 1 * MS


def test_lower_bound_does_not_treat_a_left_censored_floor_as_exact():
    """A "> x" bound built from left-censored floors is a false bound: the
    floor is an *upper* bound on its trial's window, so holding it at its
    recorded value can place the printed bound above the true median. Two
    floors at 10/12ms with two right-censored at 50ms would print "> 31.00"
    while a truth of 0.1/0.2ms gives a median of 25.1ms."""
    results = [
        _tr(rep=0, window_ns=10 * MS, censored=True),
        _tr(rep=1, window_ns=12 * MS, censored=True),
        _right_censored(rep=2, censoring_time_ns=50 * MS),
        _right_censored(rep=3, censoring_time_ns=50 * MS),
    ]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "lower_bound"
    assert row["median_lower_bound_ns"] == -math.inf
    text = render_markdown([row], [])
    assert "> 31.00" not in text
    assert "no bound identified" in text


def test_lower_bound_is_still_reported_when_no_floor_reaches_the_median():
    results = [
        _tr(rep=0, window_ns=10 * MS),
        _tr(rep=1, window_ns=12 * MS),
        _right_censored(rep=2, censoring_time_ns=50 * MS),
        _right_censored(rep=3, censoring_time_ns=50 * MS),
    ]

    assert summarize(results)[0]["median_lower_bound_ns"] == 31 * MS


def test_all_left_censored_above_the_probe_interval_is_not_below_floor():
    """ "The harness's first-look floor" and "the observation floor `p`" are
    different things. Five floors recorded at 50ms with p = 1ms say nothing
    about windows being at or below `p`; the honest verdict is "< 50.00"."""
    results = [
        _tr(rep=i, window_ns=50 * MS, censored=True, probe_interval_ns=1 * MS) for i in range(5)
    ]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "upper_bound"
    assert row["median_upper_bound_ns"] == 50 * MS


def test_every_window_at_or_below_the_probe_interval_is_reported_as_below_floor():
    """preregistration.md's words are "every trial's window is at or below
    the observation floor (`p`, ...)" -- not "every trial is left-censored".
    A cleanly measured window of the same order as `p` describes the
    instrument (docs/methodology.md), so the pre-registered phrase applies."""
    results = [_tr(rep=i, window_ns=800_000, probe_interval_ns=1 * MS) for i in range(5)]

    row = summarize(results)[0]

    assert row["n_left_censored"] == 0
    assert row["estimate_kind"] == "below_floor"
    assert row["median_ns"] is None


def test_one_window_above_the_probe_interval_is_not_below_floor():
    results = [_tr(rep=i, window_ns=1 * MS, probe_interval_ns=1 * MS) for i in range(4)]
    results.append(_tr(rep=4, window_ns=1 * MS + 1, probe_interval_ns=1 * MS))

    assert summarize(results)[0]["estimate_kind"] == "point"


def test_right_censoring_order_violation_is_flagged():
    """Rank substitution is exact only while every censoring time exceeds
    every observed window. `censoring_time_ns` is last_observation - t_ready,
    so a short or truncated trial can break that -- and then ranking it at
    the top pushes the median up. Surfaced, not silently assumed."""
    ok = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(3)]
    ok.append(_right_censored(rep=9, censoring_time_ns=60 * MS))
    violated = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(3)]
    violated.append(_right_censored(rep=9, censoring_time_ns=2 * MS))

    assert summarize(ok)[0]["right_censoring_order_violated"] is False
    assert summarize(violated)[0]["right_censoring_order_violated"] is True


def test_order_violation_flag_is_false_without_right_censoring():
    results = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(3)]

    assert summarize(results)[0]["right_censoring_order_violated"] is False


def test_condition_with_every_trial_excluded_reports_no_data():
    results = [
        _tr(rep=i, window_ns=1 * MS, error_rate=0.6, excluded_reason="error_rate>0.05")
        for i in range(3)
    ]

    row = summarize(results)[0]

    assert row["n"] == 0
    assert row["n_excluded"] == 3
    assert row["exclusion_rate"] == 1.0
    assert row["estimate_kind"] == "no_data"
    assert row["median_ns"] is None
    assert row["probe_interval_ns"] is None


def test_probe_interval_is_reported_and_variation_is_surfaced():
    """docs/methodology.md: `p` "is therefore always reported alongside any
    window value". A condition that mixed two intervals is not comparable
    within itself, so the mixture is shown, never averaged away."""
    same = [_tr(rep=i, probe_interval_ns=2 * MS) for i in range(2)]
    mixed = [_tr(rep=0, probe_interval_ns=1 * MS), _tr(rep=1, probe_interval_ns=5 * MS)]

    assert summarize(same)[0]["probe_interval_ns"] == 2 * MS
    assert summarize(same)[0]["probe_interval_varies"] is False
    assert summarize(mixed)[0]["probe_interval_ns"] is None
    assert summarize(mixed)[0]["probe_interval_varies"] is True


def test_b_c_flagged_trials_are_counted_per_condition():
    results = [_tr(rep=0, b_c_skew_ns=9 * MS, b_c_flagged=True), _tr(rep=1)]

    assert summarize(results)[0]["n_b_c_flagged"] == 1


def test_summarize_emits_one_row_per_condition_sorted():
    results = [
        _tr(cni="cilium", churn=60),
        _tr(cni="antrea", churn=1),
        _tr(cni="cilium", churn=1),
    ]

    rows = summarize(results)

    assert [(r["cni"], r["churn_rate_per_min"]) for r in rows] == [
        ("antrea", 1),
        ("cilium", 1),
        ("cilium", 60),
    ]


def test_summarize_rejects_a_trial_that_violates_the_censoring_contract():
    broken = _tr(window_ns=None, right_censored=False)

    with pytest.raises(ValueError, match="window_ns is None"):
        summarize([broken])


def test_holm_adjust_enforces_monotonicity_and_caps_at_one():
    assert holm_adjust([0.01, 0.02, 0.03]) == pytest.approx([0.03, 0.04, 0.04])
    assert holm_adjust([0.5, 0.6, 0.7]) == pytest.approx([1.0, 1.0, 1.0])
    assert holm_adjust([]) == []


def test_pairwise_covers_the_three_cni_pairs_with_holm_adjustment():
    results = (
        [_tr(cni="cilium", rep=i, window_ns=1 * MS + i) for i in range(30)]
        + [_tr(cni="calico", rep=i, window_ns=5 * MS + i) for i in range(30)]
        + [_tr(cni="antrea", rep=i, window_ns=1 * MS + i * 3) for i in range(30)]
    )

    out = pairwise_cni(results, churn_rate_per_min=1)

    assert {(p["cni_a"], p["cni_b"]) for p in out} == {
        ("antrea", "calico"),
        ("antrea", "cilium"),
        ("calico", "cilium"),
    }
    assert all(p["p_adj"] >= p["p_raw"] for p in out)
    assert all(p["n_a"] == 30 and p["n_b"] == 30 for p in out)
    assert all(p["churn_rate_per_min"] == 1 for p in out)


def test_pairwise_ranks_left_censored_trials_below_every_observed_window():
    """The mirror of the right-censored case. A floor is an upper bound on
    its trial's window, so leaving it at face value ranks that arm above
    where its truth can be. Here every recorded value is 1ms, so at face
    value the two arms are perfectly tied and p = 1; ranking calico's three
    floors below the observed values is what makes any difference visible."""
    results = [_tr(cni="cilium", rep=i, window_ns=1 * MS) for i in range(6)]
    results += [_tr(cni="calico", rep=i, window_ns=1 * MS) for i in range(3)]
    results += [_tr(cni="calico", rep=10 + i, window_ns=1 * MS, censored=True) for i in range(3)]

    (pair,) = pairwise_cni(results, churn_rate_per_min=1)

    assert (pair["cni_a"], pair["cni_b"]) == ("calico", "cilium")
    assert pair["n_left_censored_a"] == 3
    assert pair["n_left_censored_b"] == 0
    assert pair["p_raw"] < 1.0


def test_pairwise_ranks_right_censored_trials_above_every_observed_window():
    """Mann-Whitney is a rank test, so rank substitution is coherent with
    it: dropping these three censored trials would leave two identical
    all-1ms groups and p = 1."""
    results = [_tr(cni="cilium", rep=i, window_ns=1 * MS) for i in range(6)]
    results += [_tr(cni="calico", rep=i, window_ns=1 * MS) for i in range(3)]
    results += [
        _right_censored(cni="calico", rep=10 + i, censoring_time_ns=60 * MS) for i in range(3)
    ]

    (pair,) = pairwise_cni(results, churn_rate_per_min=1)

    assert (pair["cni_a"], pair["cni_b"]) == ("calico", "cilium")
    assert pair["n_a"] == 6 and pair["n_b"] == 6
    assert pair["n_right_censored_a"] == 3
    assert pair["p_raw"] < 1.0


def test_pairwise_ignores_excluded_trials():
    results = [_tr(cni="cilium", rep=i, window_ns=1 * MS) for i in range(5)]
    results += [_tr(cni="calico", rep=i, window_ns=1 * MS) for i in range(5)]
    results += [
        _tr(
            cni="calico",
            rep=9,
            window_ns=900 * MS,
            error_rate=1.0,
            excluded_reason="error_rate>0.05",
        )
    ]

    (pair,) = pairwise_cni(results, churn_rate_per_min=1)

    assert pair["n_b"] == 5


def test_pairwise_only_compares_the_requested_churn_level():
    results = [_tr(cni="cilium", churn=1, rep=i, window_ns=1 * MS) for i in range(4)]
    results += [_tr(cni="calico", churn=1, rep=i, window_ns=2 * MS) for i in range(4)]
    results += [_tr(cni="antrea", churn=60, rep=i, window_ns=3 * MS) for i in range(4)]

    out = pairwise_cni(results, churn_rate_per_min=1)

    assert len(out) == 1
    assert (out[0]["cni_a"], out[0]["cni_b"]) == ("calico", "cilium")


def test_pairwise_of_fully_tied_groups_reports_p_one_not_nan():
    """Every value identical means every permutation yields the same U, so
    the exact two-sided p-value is 1. scipy's asymptotic path divides by a
    zero tie-corrected variance and returns nan, which would then poison
    the whole Holm family."""
    results = [_tr(cni="cilium", rep=i, window_ns=1 * MS) for i in range(4)]
    results += [_tr(cni="calico", rep=i, window_ns=1 * MS) for i in range(4)]

    (pair,) = pairwise_cni(results, churn_rate_per_min=1)

    assert pair["p_raw"] == 1.0
    assert pair["p_adj"] == 1.0


def test_pairwise_returns_nothing_when_fewer_than_two_cnis_have_data():
    results = [_tr(cni="cilium", rep=i, window_ns=1 * MS) for i in range(4)]
    results += [
        _tr(
            cni="calico", rep=i, window_ns=1 * MS, error_rate=1.0, excluded_reason="error_rate>0.05"
        )
        for i in range(4)
    ]

    assert pairwise_cni(results, churn_rate_per_min=1) == []


def test_render_markdown_shows_each_censoring_verdict_and_the_probe_interval():
    results = [_tr(cni="cilium", rep=i, window_ns=(i + 1) * MS) for i in range(4)]
    results += [_right_censored(cni="calico", rep=i, censoring_time_ns=40 * MS) for i in range(2)]
    results += [_tr(cni="calico", rep=10 + i, window_ns=6 * MS) for i in range(2)]
    results += [_tr(cni="antrea", rep=i, window_ns=500_000, censored=True) for i in range(3)]

    text = render_markdown(summarize(results), [pairwise_cni(results, 1)])

    assert "no window detected above the floor" in text
    assert "> 23.00" in text  # calico's lower bound: median of [6, 6, >=40, >=40]
    assert "probe interval" in text.lower()
    assert "2.50" in text  # cilium's median, in ms


def test_render_markdown_reports_an_unbounded_ci_upper_limit_as_unbounded():
    """With right-censored values ranked at the top, a bootstrap resample
    can be >= half censored, which makes the 97.5th percentile of the
    resampled medians unbounded. Printing "inf" ms would read as a number."""
    results = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(3)]
    results += [_right_censored(rep=10 + i, censoring_time_ns=40 * MS) for i in range(2)]

    rows = summarize(results)
    assert math.isinf(rows[0]["ci_high_ns"])
    assert "unbounded" in render_markdown(rows, []).lower()


def test_point_row_ci_lower_limit_is_unbounded_when_a_floor_can_reach_it():
    """A floor below the median's rank leaves the median identified but
    still enters the bootstrap. Recorded values are upper bounds, so a
    lower CI limit taken from a floor is not conservative -- and the lower
    limit is the one that supports "the window is at least X". The median
    itself is unchanged, because the floor stays below the median's rank
    however far down it moves."""
    results = [_tr(rep=0, window_ns=1 * MS, censored=True)]
    results += [_tr(rep=i, window_ns=(2 * i + 1) * MS) for i in range(1, 5)]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "point"
    assert row["median_ns"] == 5 * MS
    assert row["ci_low_ns"] == -math.inf
    assert row["ci_high_ns"] == 9 * MS
    assert "unbounded" in render_markdown([row], [])


def test_point_row_with_censoring_at_both_ends_reports_no_interval():
    """A resample whose two middle values are a floor and a right-censored
    trial has an undefined median (-inf and +inf average to nan), so the
    percentile interval is undefined too. Report no interval rather than a
    nan that would print as a number-shaped blank."""
    results = [
        _tr(rep=0, window_ns=1 * MS, censored=True),
        _tr(rep=1, window_ns=3 * MS),
        _tr(rep=2, window_ns=5 * MS),
        _right_censored(rep=3, censoring_time_ns=50 * MS),
    ]

    row = summarize(results)[0]

    assert row["estimate_kind"] == "point"
    assert row["median_ns"] == 4 * MS
    assert row["ci_low_ns"] is None and row["ci_high_ns"] is None


def test_render_markdown_preamble_describes_the_current_bound_rules():
    text = render_markdown(summarize([_tr(rep=0, window_ns=5 * MS)]), [])

    assert "at or above" in text
    assert "no bound identified" in text
    assert "lands on one" not in text


def test_render_markdown_shows_an_upper_bound_median_as_less_than():
    results = [_tr(rep=i, window_ns=1 * MS, censored=True) for i in range(3)]
    results += [_tr(rep=3, window_ns=5 * MS), _tr(rep=4, window_ns=7 * MS)]

    text = render_markdown(summarize(results), [])

    # "<=", not "<": the bound is attainable -- if every floor's true window
    # equals its recorded value, the true median IS 1.00ms.
    assert "<= 1.00" in text
    assert "[1.00, 7.00]" not in text


def test_render_markdown_marks_a_condition_that_mixed_probe_intervals():
    """methodology.md makes `p` part of what a window value means, so a
    condition that mixed intervals is not internally comparable: the median
    cell has to say so, not only the `p` cell."""
    results = [_tr(rep=0, window_ns=5 * MS, probe_interval_ns=1 * MS)]
    results += [_tr(rep=1, window_ns=7 * MS, probe_interval_ns=4 * MS)]
    results += [_tr(rep=2, window_ns=9 * MS, probe_interval_ns=4 * MS)]

    text = render_markdown(summarize(results), [])

    assert "mixed p" in text


def test_render_markdown_names_conditions_whose_censoring_order_is_violated():
    results = [_tr(rep=i, window_ns=(i + 1) * MS) for i in range(3)]
    results.append(_right_censored(rep=9, censoring_time_ns=2 * MS))

    text = render_markdown(summarize(results), [])

    assert "cilium" in text
    assert "censoring time" in text.lower()


def test_negative_windows_are_counted_beside_a_below_floor_condition():
    """preregistration.md Addendum 1 D2's failure mode must be visible in the report.

    A pre-ready ECONNREFUSED run of k observations is classified
    `Blocked`, so the trial reports t_blocked < t_ready: a negative
    window. `_all_at_or_below_floor` returns True for it (it is
    certainly <= p), so a condition made entirely of this artifact
    still prints the pre-registered "no window detected above the
    floor" phrase. `n_negative_window` is what keeps that from being
    read as a result.
    """
    rows = summarize([_tr(window_ns=-3 * MS, rep=0), _tr(window_ns=-1 * MS, rep=1)])
    assert rows[0]["estimate_kind"] == "below_floor"
    assert rows[0]["n_negative_window"] == 2


def test_negative_window_count_is_zero_for_an_ordinary_condition():
    rows = summarize([_tr(window_ns=5 * MS, rep=0), _tr(window_ns=7 * MS, rep=1)])
    assert rows[0]["n_negative_window"] == 0


def test_negative_window_count_ignores_right_censored_trials_without_a_window():
    # window_ns is None there -- `None < 0` would raise, and counting it
    # would be wrong anyway: no window was measured at all.
    rows = summarize([_tr(window_ns=5 * MS, rep=0), _right_censored(rep=1)])
    assert rows[0]["n_negative_window"] == 0


def test_rendered_summary_table_carries_the_negative_window_column():
    rows = summarize([_tr(window_ns=-1 * MS)])
    md = render_markdown(rows, [])
    header = next(line for line in md.splitlines() if line.startswith("| CNI |"))
    separator = md.splitlines()[md.splitlines().index(header) + 1]
    body = next(line for line in md.splitlines() if line.startswith("| cilium |"))
    assert "neg. window" in header
    assert header.count("|") == separator.count("|") == body.count("|")
