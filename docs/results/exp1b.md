# Experiment 1b: policy timing — enforcement latency and the same-instant race

Run date: 2026-09-20. Protocol frozen before the pilot in
[`experiments/exp1b-ordering/preregistration.md`](../../experiments/exp1b-ordering/preregistration.md);
the measurement definitions are in [`docs/methodology.md`](../methodology.md).
Experiment 1's results, which this builds on, are in
[`exp1.md`](exp1.md).

## Headline

Experiment 1 applied the NetworkPolicy about 2.4 s before the Pod reported
Ready and found every one of its 270 trials left-censored: enforcement was
already in place at the harness's first look. It could not say how long
the CNI had actually taken, nor what happens when the policy does not get
that head start. Experiment 1b answers both, in 180 trials across the same
three CNIs.

**Enforcement latency is tens of milliseconds, and it differs by CNI.**
With the Pod already Ready and the policy applied afterwards, every one of
the 90 `at-ready` trials witnessed an `Allowed` → `Blocked` transition —
the first uncensored windows this project has measured.

| CNI | median `L` (ms) | 95% CI (ms) | n |
|---|---|---|---|
| Antrea 2.7.0 | 51.1 | [50.5, 52.7] | 30 |
| Calico v3.32.2 | 55.9 | [55.2, 56.4] | 30 |
| Cilium 1.20.0 | 115.9 | [104.4, 137.0] | 30 |

**Two of the three enforce before `kubectl apply` returns to the caller.**
Comparing each trial's `L` against its own `kubectl apply` round trip:
Antrea beat the return in 30 of 30 trials (median 13.0 ms before it),
Calico in 30 of 30 (median 9.3 ms before it), and Cilium in 0 of 30
(median 44.4 ms after it). On a single node, for two of these three CNIs,
the policy is already being enforced by the time the command that created
it has finished.

**The same-instant race was won by the policy every time.** All 90
`with-victim` trials — policy applied immediately after the victim
Deployment's apply returned — were left-censored, on all three CNIs. The
pre-registration reserves the conclusion "the hazard was not reachable at
this scale with this ordering" for exactly this case, every trial censored
on every CNI, and that is what occurred.

Zero trials were excluded, zero right-censored, zero produced a negative
window, and the probe error rate was 0.0000 in all 180.

## Pre-registered hypotheses

### H1 — partly falsified

H1 predicted that every `at-ready` trial would be uncensored and that each
CNI's median `L` would fall in [100, 1000] ms.

The censoring half held exactly: 90 of 90 uncensored. **The interval did
not.** Antrea (51.1 ms) and Calico (55.9 ms) both fall below the 100 ms
lower bound; only Cilium (115.9 ms, CI [104.4, 137.0]) lies inside it.

The band was wrong for a reason worth recording. It was chosen to contain
a bracket — a 127.8 ms lower bound from Experiment 1's Addendum 3 and a
224.4–307.0 ms upper bound from the positive control. The lower figure is
a **Cilium** measurement, and Cilium's result here contains it (127.8 ms
sits inside [104.4, 137.0]). The error was generalising one CNI's latency
to all three when the pre-registration's own H3 predicted they would
differ. Two of the CNIs are roughly twice as fast as the bound built from
the third.

### H2 — confirmed, at its strongest form

H2 predicted at least 20 of 30 `with-victim` trials left-censored per CNI,
and expected a nontrivial minority of uncensored ones because the margin
looked like roughly 2x.

The outcome was 30 of 30 on all three CNIs. The margin was wider than
predicted, in both of its terms: the head start came out at +386.8 to
+570.6 ms (medians 416–492 ms, close to the 517.3 ms measured pre-freeze),
while `L` came out at 51–116 ms rather than the bracketed 128–307 ms. The
policy therefore won by roughly four to nine times, not two.

### H3 — confirmed

Mann-Whitney U on witnessed `L`, Holm-Bonferroni across the three pairs
within the `at-ready` arm:

| pair | n a | n b | p raw | p Holm |
|---|---|---|---|---|
| antrea vs calico | 30 | 30 | 0.00117 | 0.00117 |
| antrea vs cilium | 30 | 30 | 3.02e-11 | 9.06e-11 |
| calico vs cilium | 30 | 30 | 3.02e-11 | 9.06e-11 |

All three pairs differ. The Antrea/Calico separation (about 5 ms) is small
but consistent; both are separated from Cilium by about 60 ms.

## Configuration

| | |
|---|---|
| Cluster | single-node kind (ADR 0002), one cluster per CNI |
| CNIs | Cilium 1.20.0, Calico v3.32.2, Antrea 2.7.0 |
| `t_ready` | candidate C, victim self-report (ADR 0003) |
| Probe interval `p` | 1 ms; dial timeout 200 ms |
| Sustained-run length `k` | 3 |
| Churn | 1 Pod creation/min (no background Pods) |
| Repetitions | 30 per (CNI, arm) cell |
| Arms | `with-victim`, `at-ready` (`POLICY_DELAY_MS` = 0); `before` is Experiment 1 and was not re-run |

## Window, by condition

`window = t_blocked − t_ready`. In the `at-ready` arm this is a genuine
measurement; in `with-victim` every trial is left-censored, so the value
is the harness's first-look floor and bounds the window from above.

| CNI | arm | n | left-cens. | right-cens. | neg. window | B/C flagged | median window (ms) | 95% CI (ms) |
|---|---|---|---|---|---|---|---|---|
| antrea | at-ready | 30 | 0 | 0 | 0 | 5 | 214.36 | [207.77, 217.90] |
| antrea | with-victim | 30 | 30 | 0 | 0 | 1 | <= 5.20 | n/a (floor at the median's rank) |
| calico | at-ready | 30 | 0 | 0 | 0 | 5 | 168.06 | [159.83, 177.25] |
| calico | with-victim | 30 | 30 | 0 | 0 | 4 | <= 5.27 | n/a (floor at the median's rank) |
| cilium | at-ready | 30 | 0 | 0 | 0 | 14 | 228.28 | [201.90, 308.22] |
| cilium | with-victim | 30 | 30 | 0 | 0 | 17 | <= 5.70 | n/a (floor at the median's rank) |

The `at-ready` window exceeds `L` by the candidate-C poll's detection lag,
which is what `head_start` records: −216.5 to −88.1 ms (Antrea), −253.9 to
−78.5 ms (Calico), −705.8 to −80.1 ms (Cilium). `window − L = −head_start`
holds by construction, and the poll lag is a property of the instrument,
not of the CNI — which is why `L`, not `window`, is the quantity reported
as enforcement latency.

The `with-victim` pairwise comparisons all return p = 1: every observation
on both sides is tied at its censoring floor, so the test is uninformative
there. It is reported because the pre-registration fixed it in advance.

## `kubectl apply` round trip

`policy_apply_issued_ns` is taken before `kubectl` is invoked, so the API
round trip is inside `L` by construction — that is what an operator
experiences. Across all 180 trials the round trip ran 59.7 to 315.9 ms,
median 65.0 ms. It is the dominant term in Antrea's and Calico's latency
and roughly half of Cilium's.

## Host state and comparability

The pilot (preregistration Addendum 1) found that a loaded host inflates
the candidate-B/candidate-C skew far outside Experiment 1's envelope and
stretches the whole timeline, which would make the two experiments
non-comparable. The full run was therefore started only after a check
trial's skew fell back inside that envelope, and the skew was watched
throughout.

Per-CNI B/C skew, Experiment 1 against Experiment 1b:

| CNI | Experiment 1 (n=90) | Experiment 1b (n=60) |
|---|---|---|
| cilium | median −4.0, max abs 18.5 | median −5.0, max abs 73.1 |
| calico | median −4.2, max abs 22.5 | median −4.3, max abs 36.6 |
| antrea | median −3.6, max abs 24.2 | median −3.1, max abs 35.7 |

The medians match Experiment 1 closely. Four trials of 180 exceeded
Experiment 1's observed maximum: three during a video call that started
mid-run (all in the `with-victim` arm, which is left-censored throughout
and where `L` is not computed), and one at the first trial of Antrea's
`at-ready` condition, consistent with a cold start. **None were discarded.**
The pre-registered host-level discard rule covers inter-observation gaps
over 5000 ms and nothing else; `npw.gaps` reports 0 of 180 over that
threshold. Adding a skew-based exclusion after seeing the data would be
exactly the post-hoc selection the pre-registration exists to prevent, so
the trials stay in and the counts are reported here and in the
`B/C flagged` column above.

The run was paused when the video call began and resumed after a fresh
check trial passed; 32 trials had been collected at that point and none
were recollected, since `pending()` resumes from the completion marker.

## What this does and does not establish

**Does.** On a single-node kind cluster, the three CNIs enforce a new
default-deny NetworkPolicy in 51–116 ms measured from the moment an
operator issues `kubectl apply`, they differ from one another by margins
that survive Holm correction, and two of them finish before that command
returns. When the same policy is created at the same instant as the Pod it
protects, the policy wins on every one of 90 trials: the Pod never became
Ready unprotected, and the documented hazard was not reachable in this
configuration.

**Does not.** Three limits, in decreasing order of how much they matter.

1. **Scale.** One node, one victim, one policy, one endpoint. Everything
   that makes policy distribution hard in a real cluster is absent.
   `L` here is the floor of what a real deployment would see, not an
   estimate of it. ADR 0002 places multi-node out of scope for this
   harness, and ADR 0004 records why the ordering was varied instead.
2. **The hazard was made unreachable, not shown not to exist.** The
   `with-victim` arm gives the CNI whatever head start Pod startup happens
   to provide — here 386–570 ms, against an `L` of 51–116 ms. A slower
   CNI, a larger policy set, or a faster-starting Pod moves that margin.
   What this experiment shows is that at this scale the margin is four to
   nine times, not that no configuration can close it.
3. **`L` includes the API round trip by design.** That is the right
   quantity for an operator, but it means these numbers are not a
   measurement of dataplane programming alone. The round trip's own
   distribution is given above so the two can be separated.

## Reproducing

```bash
task cluster:up CNI=cilium          # or calico / antrea
task exp1b:run  CNI=cilium
task cluster:down CNI=cilium
task exp1b:analyze                  # data/raw/exp1b -> data/processed/exp1b
task gaps -- data/raw/exp1b         # host-sleep check
```

`data/processed/` is not committed; `data/raw/` carries a
`checksums.sha256` per trial, all 180 of which verify.

## Upstream

Experiment 1's results were not filed upstream because they corroborated
the Kubernetes documentation rather than adding to it
([`exp1.md`](exp1.md), Upstream). This result does add something the page
does not have: the documentation says there is "no way to tell from the
Kubernetes API when exactly" a policy has been handled, and gives a reader
no sense of the magnitude involved. There is now a measured answer for
three CNIs at one scale, with the ordering the docs warn about tested
directly.

Filed as [kubernetes/website#57642](https://github.com/kubernetes/website/issues/57642)
on 2026-09-20, as a question rather than a PR: it asks whether a
non-normative note on the magnitude belongs on a concept page at all, and
says plainly that these are single-node figures and a floor rather than an
estimate. A maintainer judging it out of scope is an acceptable outcome and
the issue says so.
