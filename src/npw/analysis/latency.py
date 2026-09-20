"""Enforcement latency per (CNI, policy timing): pure functions, no I/O.

`enforcement_latency_ns = t_blocked - policy_apply_issued_ns` (see
`trial.TrialResult`) is how long a CNI took from being asked to enforce a
NetworkPolicy to actually dropping traffic. Unlike the window, it is only
*defined* when a transition was witnessed:

- a **left-censored** trial was already Blocked at the first post-ready
  look, so its recorded latency is the harness's own floor -- an upper
  bound with no latency inside it. It is counted (`n_left_censored`) and
  left out of the median, never averaged in as if it were a measurement.
- a **right-censored** trial never enforced within the trial: its latency
  is at least the trial's remaining length, and unbounded above. One such
  trial in a cell makes the cell's median unidentifiable without a
  survival model this project does not pre-register, so the cell reports
  `right_censored_present` and no number. That a CNI failed to enforce a
  policy within 30 s is a headline on its own, not a value to fold in.
- an Experiment 1 trial has no `policy_apply_issued_ns` at all; counted
  as `n_no_apply_time`.

The median + percentile-bootstrap CI over the remaining trials follows
`experiments/exp1b-ordering/preregistration.md` (Analysis plan, item 2).
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import combinations

import numpy as np

from npw.analysis.report import _mann_whitney_p, holm_adjust
from npw.analysis.stats import bootstrap_ci
from npw.analysis.trial import TrialResult

LATENCY_FIELDS = (
    "cni",
    "policy_at",
    "n",
    "n_excluded",
    "n_no_apply_time",
    "n_left_censored",
    "n_right_censored",
    "n_used",
    "estimate_kind",
    "median_ns",
    "ci_low_ns",
    "ci_high_ns",
)

LATENCY_PAIRWISE_FIELDS = ("policy_at", "cni_a", "cni_b", "n_a", "n_b", "p_raw", "p_adj")


def _witnessed(results: Sequence[TrialResult]) -> list[TrialResult]:
    """Included trials whose latency is a measurement, not a bound."""
    return [
        r
        for r in results
        if r.excluded_reason is None
        and r.policy_apply_issued_ns is not None
        and not r.censored
        and not r.right_censored
        and r.enforcement_latency_ns is not None
    ]


def summarize_latency(results: Sequence[TrialResult]) -> list[dict]:
    """One row per (cni, policy_at), sorted; see the module docstring."""
    rows = []
    for cni, policy_at in sorted({(r.cni, r.policy_at) for r in results}):
        cell = [r for r in results if r.cni == cni and r.policy_at == policy_at]
        included = [r for r in cell if r.excluded_reason is None]
        no_apply = [r for r in included if r.policy_apply_issued_ns is None]
        with_apply = [r for r in included if r.policy_apply_issued_ns is not None]
        n_right = sum(1 for r in with_apply if r.right_censored)
        n_left = sum(1 for r in with_apply if r.censored and not r.right_censored)
        used = _witnessed(cell)
        values = [float(r.enforcement_latency_ns) for r in used]

        median: float | None = None
        ci_low: float | None = None
        ci_high: float | None = None
        if n_right > 0:
            kind = "right_censored_present"
        elif not values:
            kind = "no_transition"
        elif len(values) == 1:
            kind = "single"
            median = values[0]
        else:
            kind = "point"
            median = float(np.median(values))
            ci_low, ci_high = bootstrap_ci(values)

        rows.append(
            {
                "cni": cni,
                "policy_at": policy_at,
                "n": len(cell),
                "n_excluded": len(cell) - len(included),
                "n_no_apply_time": len(no_apply),
                "n_left_censored": n_left,
                "n_right_censored": n_right,
                "n_used": len(used),
                "estimate_kind": kind,
                "median_ns": median,
                "ci_low_ns": ci_low,
                "ci_high_ns": ci_high,
            }
        )
    return rows


def pairwise_latency(results: Sequence[TrialResult], policy_at: str) -> list[dict]:
    """Mann-Whitney U on witnessed latencies between CNIs, Holm across the pairs.

    Only trials with a witnessed transition take part (a floor has no
    latency to rank), so `n_a`/`n_b` can be smaller than the cell's `n`;
    both are printed so the reader can see how much of a cell the test
    actually rests on. A CNI with no witnessed transition at this
    `policy_at` has no group and is not compared.
    """
    used = [r for r in _witnessed(results) if r.policy_at == policy_at]
    groups = {cni: [r for r in used if r.cni == cni] for cni in sorted({r.cni for r in used})}
    pairs = list(combinations(groups, 2))
    p_raw = [
        _mann_whitney_p(
            [float(r.enforcement_latency_ns) for r in groups[a]],
            [float(r.enforcement_latency_ns) for r in groups[b]],
        )
        for a, b in pairs
    ]
    p_adj = holm_adjust(p_raw)
    return [
        {
            "policy_at": policy_at,
            "cni_a": a,
            "cni_b": b,
            "n_a": len(groups[a]),
            "n_b": len(groups[b]),
            "p_raw": raw,
            "p_adj": adj,
        }
        for (a, b), raw, adj in zip(pairs, p_raw, p_adj, strict=True)
    ]


def _ms(ns: float | None) -> str:
    return "n/a" if ns is None else f"{ns / 1e6:.1f}"


def _estimate(row: dict) -> tuple[str, str]:
    kind = row["estimate_kind"]
    if kind == "point":
        return _ms(row["median_ns"]), f"[{_ms(row['ci_low_ns'])}, {_ms(row['ci_high_ns'])}]"
    if kind == "single":
        return _ms(row["median_ns"]), "n/a (one witnessed transition)"
    if kind == "right_censored_present":
        return "n/a", f"n/a ({row['n_right_censored']} trial(s) never enforced within the trial)"
    return "n/a", "n/a (no witnessed transition in this cell)"


def render_latency_markdown(rows: Sequence[dict], pairwise: Sequence[Sequence[dict]]) -> str:
    lines = [
        "## Enforcement latency",
        "",
        (
            "`L = t_blocked - t_policy_issued`, in milliseconds: the time from issuing "
            "`kubectl apply` for the policy to the first observation of a sustained Blocked "
            "run. Only trials with a witnessed Allowed -> Blocked transition contribute to the "
            "median (`used`); a left-censored trial's value is the harness's floor, not a "
            "latency, and is counted but not averaged. Any right-censored trial in a cell "
            "withholds the estimate."
        ),
        "",
        "| CNI | policy at | n | excluded | no apply time | left-cens. | right-cens. | used | median L (ms) | 95% CI (ms) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        med, ci = _estimate(r)
        lines.append(
            f"| {r['cni']} | {r['policy_at']} | {r['n']} | {r['n_excluded']} | "
            f"{r['n_no_apply_time']} | {r['n_left_censored']} | {r['n_right_censored']} | "
            f"{r['n_used']} | {med} | {ci} |"
        )
    lines += [
        "",
        "## Pairwise CNI comparison, enforcement latency",
        "",
        (
            "Mann-Whitney U (two-sided) on witnessed latencies per policy timing, "
            "Holm-Bonferroni adjusted across the pairs tested."
        ),
        "",
        "| policy at | pair | n a | n b | p raw | p Holm |",
        "|---|---|---|---|---|---|",
    ]
    flat = [p for level in pairwise for p in level]
    if not flat:
        lines.append("| _no pair had two CNIs with a witnessed transition_ | | | | | |")
    for p in flat:
        lines.append(
            f"| {p['policy_at']} | {p['cni_a']} vs {p['cni_b']} | {p['n_a']} | {p['n_b']} | "
            f"{p['p_raw']:.3g} | {p['p_adj']:.3g} |"
        )
    return "\n".join(lines) + "\n"
