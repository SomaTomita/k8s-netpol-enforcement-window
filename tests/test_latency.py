"""Tests for the enforcement-latency summary. Synthetic fixtures only."""

from npw.analysis.latency import (
    LATENCY_FIELDS,
    pairwise_latency,
    render_latency_markdown,
    summarize_latency,
)
from npw.analysis.trial import TrialResult

MS = 1_000_000


def _tr(cni="cilium", rep=0, policy_at="at-ready", latency_ns=250 * MS, censored=False,
        right_censored=False, excluded_reason=None, apply_ns=100 * MS):
    return TrialResult(
        run_id=f"{cni}-{policy_at}-{rep}",
        cni=cni,
        churn_rate_per_min=1,
        repetition=rep,
        window_ns=None if right_censored else latency_ns,
        censored=censored,
        right_censored=right_censored,
        censoring_time_ns=30_000 * MS if right_censored else None,
        error_rate=0.0,
        b_c_skew_ns=0,
        b_c_flagged=False,
        excluded_reason=excluded_reason,
        probe_interval_ns=MS,
        policy_at=policy_at,
        policy_apply_issued_ns=apply_ns,
        enforcement_latency_ns=None if right_censored else latency_ns,
    )


def test_point_estimate_over_witnessed_transitions_only():
    rows = summarize_latency([
        _tr(rep=0, latency_ns=200 * MS),
        _tr(rep=1, latency_ns=300 * MS),
        _tr(rep=2, latency_ns=250 * MS),
        _tr(rep=3, latency_ns=5 * MS, censored=True),       # a floor, not a latency
        _tr(rep=4, excluded_reason="error_rate>0.05"),
        _tr(rep=5, apply_ns=None, latency_ns=None),         # exp1-style, no apply time
    ])
    assert len(rows) == 1
    r = rows[0]
    assert (r["n"], r["n_excluded"], r["n_no_apply_time"], r["n_left_censored"], r["n_used"]) == (6, 1, 1, 1, 3)
    assert r["estimate_kind"] == "point"
    assert r["median_ns"] == 250 * MS
    assert r["ci_low_ns"] <= 250 * MS <= r["ci_high_ns"]
    assert tuple(r) == LATENCY_FIELDS


def test_right_censoring_blocks_the_estimate():
    rows = summarize_latency([_tr(rep=0), _tr(rep=1, right_censored=True)])
    assert rows[0]["estimate_kind"] == "right_censored_present"
    assert rows[0]["median_ns"] is None and rows[0]["n_right_censored"] == 1


def test_single_witnessed_transition_has_no_ci():
    rows = summarize_latency([_tr(rep=0), _tr(rep=1, censored=True)])
    assert rows[0]["estimate_kind"] == "single"
    assert rows[0]["median_ns"] == 250 * MS and rows[0]["ci_low_ns"] is None


def test_no_transition_at_all():
    rows = summarize_latency([_tr(rep=0, censored=True), _tr(rep=1, censored=True)])
    assert rows[0]["estimate_kind"] == "no_transition" and rows[0]["n_used"] == 0


def test_rows_are_sorted_by_cni_then_policy_at():
    rows = summarize_latency([
        _tr(cni="cilium", policy_at="with-victim"),
        _tr(cni="antrea", policy_at="at-ready"),
        _tr(cni="cilium", policy_at="at-ready"),
    ])
    assert [(r["cni"], r["policy_at"]) for r in rows] == [
        ("antrea", "at-ready"), ("cilium", "at-ready"), ("cilium", "with-victim"),
    ]


def test_pairwise_latency_holm_across_three_pairs():
    results = []
    for cni, base in (("cilium", 200), ("calico", 400), ("antrea", 600)):
        results += [_tr(cni=cni, rep=i, latency_ns=(base + i) * MS) for i in range(5)]
    rows = pairwise_latency(results, policy_at="at-ready")
    assert [(r["cni_a"], r["cni_b"]) for r in rows] == [("antrea", "calico"), ("antrea", "cilium"), ("calico", "cilium")]
    assert all(r["p_adj"] >= r["p_raw"] for r in rows)
    assert all(r["n_a"] == 5 and r["n_b"] == 5 for r in rows)


def test_pairwise_latency_uses_only_witnessed_transitions():
    results = [_tr(cni="cilium", rep=i) for i in range(3)] + [
        _tr(cni="calico", rep=0), _tr(cni="calico", rep=1, censored=True)
    ]
    rows = pairwise_latency(results, policy_at="at-ready")
    assert rows[0]["n_a"] == 1 and rows[0]["n_b"] == 3  # calico(a)=1 witnessed, cilium(b)=3


def test_render_markdown_has_both_tables():
    rows = summarize_latency([_tr(rep=0), _tr(rep=1)])
    md = render_latency_markdown(rows, [pairwise_latency([_tr(rep=0)], "at-ready")])
    assert "## Enforcement latency" in md and "| cilium | at-ready | 2 |" in md
    assert "## Pairwise CNI comparison, enforcement latency" in md
