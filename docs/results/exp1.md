# Experiment 1: the unprotected window across three CNIs

Run date: 2026-09-20. Protocol frozen before the run in
[`experiments/exp1-window/preregistration.md`](../../experiments/exp1-window/preregistration.md);
the measurement definition is [`docs/methodology.md`](../methodology.md).

## Headline

Across 270 trials — Cilium, Calico and Antrea, at 1, 10 and 60 Pod
creations per minute, 30 repetitions each — **no trial witnessed an
`Allowed` → `Blocked` transition after the victim became Ready.** Every
trial was left-censored: traffic was already blocked at the harness's
first look.

This is an **upper bound, not a measurement**. It says the window, if one
exists under this configuration, is shorter than the harness could see —
it does not say the window is zero. Per `docs/methodology.md`
(Observation resolution), a left-censored floor "is a different quantity
from the observation floor `p`, and must not be reported as 'no window
above the floor'."

The bound is a few milliseconds:

| statistic over all 270 first-look floors | ms |
|---|---|
| minimum | 1.01 |
| 25th percentile | 6.39 |
| median | 7.73 |
| 75th percentile | 9.61 |
| 95th percentile | 18.38 |
| maximum | 194.21 |

96.3% of trials have a floor at or below 20 ms.

## Configuration

| | |
|---|---|
| Cluster | single-node kind (ADR 0002) |
| CNIs | Cilium 1.20.0, Calico v3.32.2, Antrea 2.7.0 |
| `t_ready` | candidate C, victim self-report (ADR 0003) |
| Probe interval `p` | 1 ms |
| Sustained-run length `k` | 3 |
| Churn levels | 1, 10, 60 Pod creations/min |
| Repetitions | 30 per (CNI, churn) cell |
| Policy ordering | policy-first: the default-deny NetworkPolicy is applied **before** the victim Pod exists (`scripts/trial.sh`) |

## Per-condition results

`median` is the median of the per-trial first-look floors, so it is an
upper bound on the window for that cell. No confidence interval is
reported: a CI drawn from a censoring floor would describe the harness,
not the phenomenon.

| CNI | churn/min | n | excluded | left-cens. | right-cens. | neg. window | B/C flagged | median (ms) |
|---|---|---|---|---|---|---|---|---|
| antrea | 1 | 30 | 0 | 30 | 0 | 0 | 8 | <= 7.44 |
| antrea | 10 | 30 | 0 | 30 | 0 | 0 | 10 | <= 7.73 |
| antrea | 60 | 30 | 0 | 30 | 0 | 0 | 3 | <= 8.94 |
| calico | 1 | 30 | 0 | 30 | 0 | 0 | 14 | <= 8.15 |
| calico | 10 | 30 | 0 | 30 | 0 | 0 | 12 | <= 8.32 |
| calico | 60 | 30 | 0 | 30 | 0 | 0 | 6 | <= 9.48 |
| cilium | 1 | 30 | 0 | 30 | 0 | 0 | 11 | <= 5.97 |
| cilium | 10 | 30 | 0 | 30 | 0 | 0 | 5 | <= 6.13 |
| cilium | 60 | 30 | 0 | 30 | 0 | 0 | 3 | <= 7.65 |

Zero trials were excluded, zero were right-censored, zero produced a
negative window, and the probe error rate was 0.0000 in every trial.

The Mann-Whitney U comparisons (per churn level, Holm-Bonferroni across
the three pairs) all return p = 1. That is not evidence that the CNIs
behave identically — it is what the test returns when every observation
on both sides is tied at its censoring floor. The comparison is
uninformative here, and is reported rather than omitted only because the
pre-registration fixed it in advance.

`B/C flagged` counts trials where candidate B (CRI `startedAt`) and
candidate C (victim self-report) diverge by more than the pre-registered
5 ms threshold (`BC_GAP_MAX_NS`): 72 of 270 (26.7%). Since candidate C is
the primary definition and every trial is censored at C's own floor, this
divergence does not move any reported number; it is recorded because the
pre-registration requires it.

## Instrument check (positive control)

Every Experiment 1 trial being left-censored is consistent both with "the
window is below the floor" and with "the harness cannot see a window that
exists". `scripts/positive-control.sh` separates the two: it inverts the
ordering, applying the policy at a known delay *after* `t_ready`, so a
window of known size is imposed by construction. This is the late-policy
/ teardown-side case (the shape of CVE-2024-7598), **not** Experiment 1's
question, and its data is written to `data/raw/positive-control/` and
never pooled with `data/raw/exp1/`.

| CNI | imposed delay | recovered window |
|---|---|---|
| Cilium 1.20.0 | 2000 ms | 2307.0 ms |
| Calico v3.32.2 | 2000 ms | 2250.0 ms |
| Antrea 2.7.0 | 2000 ms | 2224.4 ms |

All three recover the imposed delay plus 224–307 ms. The excess is the
policy's own propagation cost — `kubectl apply` returning, the CNI
agent observing the new NetworkPolicy, and the dataplane being
programmed — which is included by construction because the imposed delay
is timed from `t_ready`, not from the point enforcement actually lands.
The detection path therefore works on all three CNIs, which is what makes
Experiment 1's censored result interpretable as a bound rather than as a
silent failure.

The positive control also caught a live defect in the analysis layer: see
preregistration Addendum 3 (D2) and `src/npw/analysis/trial.py`, where
`t_blocked` is now searched only over observations at or after `t_ready`.
Before that fix the Antrea positive control reported −3.278 ms instead of
2224.4 ms.

## Trials excluded from the dataset for host-level reasons

Four trials were collected while the host entered a macOS "Maintenance
Sleep" state mid-measurement, producing inter-observation gaps of 4.8 to
68.7 minutes against a 200 ms dial timeout. They are not measurements and
were moved to `data/raw/exp1-failed/<run_id>/`, each with a `REASON.md`
recording the cause, the `pmset -g log` evidence and the gap size. Like
the rest of `data/raw/`, those directories are not committed — only
per-trial checksums are — so the record that survives in this repository
is this table and preregistration Addendum 4:

| run_id | observed gap |
|---|---|
| run-0000-022 | 4 119 386.7 ms |
| run-0001-021 | 1 944 252.5 ms |
| run-0001-022 | 285 127.8 ms |
| run-0002-021 | 1 116 322.2 ms |

Per the repository's data-immutability rule they were moved, not deleted.
The runner re-offered their conditions and fresh trials were collected,
so the 270 trials analysed above are 270 complete trials, not 266 plus
four damaged ones. The remediation was `caffeinate -s -i -d -m -u` held
for the remainder of the run; the earlier `caffeinate -i -t 300` did not
cover this sleep path. Every subsequent trial was scanned for
inter-observation gaps over 5 s: the largest gap in the 270 analysed
trials is 616.5 ms.

## What this does and does not establish

**Does:** under policy-first ordering on a single-node kind cluster, all
three CNIs enforce a default-deny NetworkPolicy fast enough that the
window is below a ~6–9.5 ms per-cell floor, at churn rates up to 60 Pod
creations per minute. Raising churn 60-fold moves that floor by a few
milliseconds at most.

**Does not:** this is not a measurement of the window's size, and it is
not evidence that no window exists. Three limits matter in particular.

1. **Ordering.** The policy exists before the Pod does, and the CNI gets
   a substantial head start: across the 270 trials the victim reached
   candidate C between 2380 ms and 3571 ms after the run epoch (median
   2611 ms), while the policy was applied within the first ~80–320 ms.
   That is the favourable case for the CNI, and deliberately so — it is
   the ordering Budigiri et al. assume when they write that "the minimal
   performance impact of GH depends on the fact that SGs can be
   configured before a Pod becomes ready", relying on observed "Pod
   readiness times of 3-4s" as the available budget. Experiment 1 tests
   that assumption's ordering directly and finds it holds here with room
   to spare. It says nothing about the case Kubernetes' own documentation
   warns about — a Pod "created before the network plugin has completed
   NetworkPolicy handling" — where that head start is absent or negative.
   The positive control shows the harness can measure such a case when it
   is imposed deliberately; Experiment 1 does not sample it.
2. **Scale.** Single node, one victim, one policy. Real clusters have
   many nodes, large policy sets and a much larger endpoint churn surface,
   all of which lengthen propagation.
3. **Resolution.** The floor is the harness's, not the phenomenon's. A
   window shorter than the first-look floor is indistinguishable from no
   window with this instrument.

## Reproducing

```bash
task cluster:up CNI=cilium          # or calico / antrea
task exp1:run   CNI=cilium
task cluster:down CNI=cilium
task exp1:analyze                   # data/raw/exp1 -> data/processed/exp1
```

`data/processed/` is not committed; `data/raw/` carries a
`checksums.sha256` per trial so a regenerated analysis can be checked
against the raw stream it came from.

## Upstream

Not filed. The pre-run rule was to report to a CNI project if any window
landed above the floor and to `kubernetes/website` otherwise; nothing
landed above the floor, so the rule pointed at the docs. Reading the
current page first changed the answer.

The [Pod lifecycle](https://kubernetes.io/docs/concepts/services-networking/network-policies/#pod-lifecycle)
section already separates the two orderings this result speaks to —
`kubernetes/website` [#39875](https://github.com/kubernetes/website/issues/39875)
did that in November 2023 after an extended review. This result
corroborates that text rather than contradicting it, and there is no
defect to report. Nor does it contradict any CNI's own claim: Cilium
documents an unprotected window for *initializing* endpoints absent
`reserved:init` policies, and candidate C fires only once the victim is
already listening, so the two describe different instants.

What the page genuinely does not answer is how long the unhandled
interval lasts, which is what an operator needs in order to judge
whether its suggested init-container workaround is proportionate. That
question is worth asking upstream, but it is worth asking with a
measurement of the *unfavourable* ordering — a policy created
concurrently with the workload, which is the case the docs warn about
and the one this experiment did not sample — and at more than
single-node scale. Filing on the strength of a corroborating
single-node result would be duplicative.

A drafted issue exists and is deliberately unsent; this section is the
record of the decision, and will carry the URL if it is ever filed.
