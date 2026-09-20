# Experiment 1b: policy timing — pre-registration

Frozen 2026-09-20, before the pilot. Amendments are dated, append-only
addenda at the end of this file; nothing above the first addendum is
edited after the fact.

## Question

Experiment 1 (`docs/results/exp1.md`) measured the favourable ordering —
NetworkPolicy created ~2.4 s before the Pod reported Ready — and found
all 270 trials left-censored, with per-cell median floors of ~6–9.5 ms
(the full per-trial distribution: min 1.01 ms, median 7.73 ms, 95th
percentile 18.38 ms, max 194.21 ms; 3.7% of trials exceed 20 ms). The
Kubernetes documentation's hazard is the other ordering: a Pod "created
before the network plugin has completed NetworkPolicy handling".
Experiment 1b asks:

1. How long does each CNI take to enforce a policy once asked
   (`L = t_blocked − t_policy_issued`), measured with the Pod already
   Ready so Pod startup cannot mask it?
2. When the policy is created in the same instant as the Pod, does the
   Pod ever become Ready unprotected on a single node?

## Independent variable

`policy_at` (scripts/trial.sh `POLICY_AT`; src/npw/matrix.py):

- `with-victim` — policy applied immediately after the victim
  Deployment's `kubectl apply` returns, in the same shell.
- `at-ready` — policy applied the instant the victim's candidate-C
  self-report is observed by a 100 ms `kubectl logs` poll, with
  `POLICY_DELAY_MS = 0`.

`before` (Experiment 1) is the reference arm and is not re-run.

## Fixed parameters

Identical to Experiment 1's Addendum 1: `k = 3`; `p = 1 ms`; dial timeout
200 ms; `DURATION = 30 s`; single-node kind (ADR 0002); `t_ready` =
candidate C (ADR 0003); churn 1/min (no background Pods); Cilium 1.20.0,
Calico v3.32.2, Antrea 2.7.0 with the Helm values in `deploy/cni/`;
fresh cold namespace per trial; policy `deploy/policies/baseline/default-deny-ingress.yaml`.

Matrix: `experiments/exp1b-ordering/matrix.yaml` — 3 CNIs × 2 arms × 30
repetitions = 180 trials, raw root `data/raw/exp1b`.

## Recorded quantities (per trial, `trial.json`)

- `policy_apply_issued_ns` — instant before `kubectl apply` is invoked.
- `policy_apply_returned_ns` — instant it returned.
- `t_ready_c_ns`, `t_ready_b_ns` — as in Experiment 1.

Derived (`npw.analysis.trial`): `window_ns = t_blocked − t_ready_c_ns`;
`enforcement_latency_ns = t_blocked − policy_apply_issued_ns`;
`head_start_ns = t_ready_c_ns − policy_apply_issued_ns`.

## Hypotheses

- **H1** (`at-ready`): every trial is uncensored — an Allowed → Blocked
  transition after `t_ready` is witnessed — and each CNI's median `L`
  lies in [100, 1000] ms. Prior: `L` is bracketed between two sourced
  figures, neither of which is `L` itself. `policy_apply_issued_ns` is
  captured *before* `kubectl apply` runs (`scripts/trial.sh`), so `L`
  includes the API round trip by definition. **Lower bound, 127.8 ms
  (Cilium):** Experiment 1's preregistration Addendum 3 states this
  figure "from `kubectl apply` returning to the sustained-Blocked run's
  start" — measured from the apply *returning*, so it omits the round
  trip `L` includes, making it a lower bound on `L`, not a measurement of
  it. The one trial with both timestamps records that round trip
  directly: `data/raw/dev/smoke-with-victim/trial.json`,
  `policy_apply_returned_ns − policy_apply_issued_ns` = 77.7 ms — cited
  here as the one observed magnitude of that gap, not as a correction to
  apply to the 127.8 ms figure. **Upper bound, 224.4–307.0 ms (all three
  CNIs):** the Addendum 3 positive control (`data/raw/positive-control/`,
  n = 1 per CNI) recovered a window 224.4–307.0 ms in excess of the
  imposed 2000 ms delay; that excess additionally contains the
  `time.sleep` wake-up that `L` excludes, making it an upper bound. The
  `[100, 1000] ms` band is chosen to contain this bracket with margin on
  both sides, not a point estimate between the two bounds; it is
  deliberately wide and is a sanity bound, not a point prediction. Per
  Experiment 1's Addendum 1 B1, the 200 ms dial timeout does not blur
  `t_blocked` itself — the transition instant is still located to within
  about `p` — but on a dropping CNI it does cost wall-clock time to
  *confirm* the `k = 3` run, up to `(k − 1) × 200 ms = 400 ms` after the
  true transition, which the 30 s trial duration must have budget to
  carry.
- **H2** (`with-victim`): ≥ 20 of 30 trials per CNI are left-censored.
  Reasoning: in this arm the policy is applied immediately after the
  victim Deployment's apply returns, so the CNI's head start is not the
  full run-epoch-to-candidate-C interval Experiment 1 measured for the
  `before` arm — it is only what remains of it once the apply round-trip
  has elapsed. A single pre-freeze instrument-check trial run while
  building this harness, on Cilium only (`data/raw/dev/smoke-with-victim/`;
  `trial.json`'s `node` is `npw-cilium-control-plane`; developer scratch,
  never pooled with `data/raw/exp1b/`; the trial's stream stays local but
  its `checksums.sha256` is committed, on the same rule as every other
  trial), measured that head start at 517.3 ms, against the 224.4–307.0 ms
  upper bound on `L` from the Addendum 3 positive control (see H1 — this
  is a bound, not a measurement, of `L`) — a margin of roughly 2x, not
  the ~10x margin Experiment 1's `before` arm had (candidate C fired
  2380–3571 ms after the *run epoch* there, which precedes Pod creation,
  against the same bound on `L`), which is why `before` censored all 270
  trials. This head start is measured on Cilium only; Calico and Antrea
  are assumed comparable on this quantity, not measured, and their
  dataplanes differ enough that the assumption is untested. Disclosure:
  setting this threshold from a disclosed instrument-check trial before
  freezing the protocol is the intended use of such a trial; a threshold
  derived instead from the `before` arm's ~10x margin — which does not
  apply to `with-victim` — would not be. Because the margin here is only
  ~2x, a nontrivial minority of uncensored trials is expected, unlike
  `before`. The 20-of-30 cutoff is itself a judgement call, not a
  computed one: n = 1 gives no dispersion estimate to derive a precise
  threshold from, so 20 (rather than, say, 15 or 25) is pre-registered as
  a judgement reflecting "most but visibly not all trials censored under
  a ~2x margin," not a value calculated from the instrument-check data.
  If H2 holds, the documentation's hazard is unreachable on one node with
  one policy; observing it requires lengthening `L` (scale: nodes,
  policies, endpoints), which ADR 0002 places out of scope for this
  harness.
- **H3**: CNIs differ in `L` (`at-ready` arm; two-sided; no direction).

## Analysis plan

1. `window` per (cni, churn, policy_at): `npw.analysis.report.summarize`,
   unchanged rules (rank substitution, identifiability, bounds).
2. `L` per (cni, policy_at): `npw.analysis.latency.summarize_latency` —
   median and percentile bootstrap 95% CI (10 000 resamples, fixed seed)
   over trials with a witnessed transition. A cell with exactly one
   witnessed trial reports that trial's value as the median with no CI
   (`estimate_kind="single"`), since a bootstrap needs more than one
   observation. Left-censored trials are counted and excluded from the
   median (a floor has no latency inside it). Any right-censored trial in
   a cell withholds that cell's estimate; the count is reported. A trial
   with no `policy_apply_issued_ns` (Experiment 1 trials; not applicable
   within this matrix) is counted separately as `n_no_apply_time`.
3. `head_start` per arm: min / median / max from `trials.csv`.
4. H3: Mann-Whitney U (two-sided) on witnessed `L` per CNI pair within
   `at-ready`, Holm-Bonferroni across the three pairs, α = 0.05.
   `window` pairwise per (churn, policy_at) is also reported, as in
   Experiment 1.
5. Exclusion: `error_rate > 0.05` only (as Experiment 1).
6. Host-level discard (formalising Experiment 1's ad-hoc procedure): a
   trial whose `prober.jsonl` has an inter-observation gap > 5000 ms
   (`uv run python -m npw.gaps`) was suspended mid-measurement; it is
   moved to `data/raw/exp1b-failed/<run_id>/` with a `REASON.md` and
   re-collected. As for Experiment 1's own discards (`docs/results/exp1.md`),
   the discarded trial's `checksums.sha256` is committed so a reader can
   verify the discarded stream is the one described; the `REASON.md` and
   the streams themselves stay local, per `.gitignore`, as all of
   `data/raw/` does. The count, the run ids, and the reasons are
   reproduced in the results document.

## Pilot

`experiments/exp1b-ordering/pilot-matrix.yaml`: Cilium, 3 repetitions per
arm, raw root `data/raw/exp1b-pilot`, never pooled. Go criteria, all
required: 6 of 6 trials complete with verifying checksums; the 3
`at-ready` trials are all uncensored with `window` in [50, 2000] ms and
`head_start < 0`; `npw.gaps` reports 0 over threshold. Any failure is a
no-go: fix the harness, record why as an addendum, re-pilot. The pilot
does not tune any parameter above.

This `window` band is a functional check — that the harness produces an
uncensored measurement in this arm at all — not a test of H1. `L` is
smaller than `window` in this arm, because `head_start` is negative here
(`L − window = head_start`); a pilot trial passing at the low end of
[50, 2000] ms could therefore still have an `L` below H1's [100, 1000] ms
band, and would not by itself confirm H1's band. H1's `L` distribution is
judged separately, over the full run's 30 trials per CNI, not by this
pilot.

## Amendment policy

Append-only dated addenda. No parameter above changes after the pilot's
go decision. A discovered defect is fixed in code, recorded here with its
evidence, and — if it affected collected trials — those trials are
re-collected, not re-interpreted.
