# Experiment 1b: policy timing — pre-registration

Frozen 2026-09-20, before the pilot. Amendments are dated, append-only
addenda at the end of this file; nothing above the first addendum is
edited after the fact.

## Question

Experiment 1 (`docs/results/exp1.md`) measured the favourable ordering —
NetworkPolicy created ~2.4 s before the Pod reported Ready — and found
all 270 trials left-censored below a 5.97–9.48 ms floor. The Kubernetes
documentation's hazard is the other ordering: a Pod "created before the
network plugin has completed NetworkPolicy handling". Experiment 1b asks:

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
  lies in [100, 1000] ms. Prior: the Addendum 3 positive control
  (`data/raw/positive-control/`, n = 1 per CNI) recovered 224.4–307.0 ms
  over an imposed 2000 ms delay. The interval is deliberately wide; it
  is a sanity bound, not a point prediction.
- **H2** (`with-victim`): ≥ 20 of 30 trials per CNI are left-censored.
  Reasoning: in this arm the policy is applied immediately after the
  victim Deployment's apply returns, so the CNI's head start is not the
  full Pod-creation interval Experiment 1 measured for the `before` arm —
  it is only what remains of it once the apply round-trip has elapsed. A
  single pre-freeze instrument-check trial run while building this
  harness (`data/raw/dev/smoke-with-victim/`, developer scratch, never
  pooled with `data/raw/exp1b/`) measured that head start at 517.3 ms,
  against the 224.4–307.0 ms `L` prior from the Addendum 3 positive
  control — a margin of roughly 2x, not the ~10x margin Experiment 1's
  `before` arm had (Pod creation to candidate C took 2380–3571 ms there,
  against the same `L` prior), which is why `before` censored all 270
  trials. Disclosure: setting this threshold from a disclosed
  instrument-check trial before freezing the protocol is the intended
  use of such a trial; a threshold derived instead from the `before`
  arm's ~10x margin — which does not apply to `with-victim` — would not
  be. Because the margin here is only ~2x, a nontrivial minority of
  uncensored trials is expected, unlike `before`. If H2 holds, the
  documentation's hazard is unreachable on one node with one policy;
  observing it requires lengthening `L` (scale: nodes, policies,
  endpoints), which ADR 0002 places out of scope for this harness.
- **H3**: CNIs differ in `L` (`at-ready` arm; two-sided; no direction).

## Analysis plan

1. `window` per (cni, churn, policy_at): `npw.analysis.report.summarize`,
   unchanged rules (rank substitution, identifiability, bounds).
2. `L` per (cni, policy_at): `npw.analysis.latency.summarize_latency` —
   median and percentile bootstrap 95% CI (10 000 resamples, fixed seed)
   over trials with a witnessed transition. Left-censored trials are
   counted and excluded from the median (a floor has no latency inside
   it). Any right-censored trial in a cell withholds that cell's
   estimate; the count is reported.
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
   re-collected. The count and the run ids are reported.

## Pilot

`experiments/exp1b-ordering/pilot-matrix.yaml`: Cilium, 3 repetitions per
arm, raw root `data/raw/exp1b-pilot`, never pooled. Go criteria, all
required: 6 of 6 trials complete with verifying checksums; the 3
`at-ready` trials are all uncensored with `window` in [50, 2000] ms and
`head_start < 0`; `npw.gaps` reports 0 over threshold. Any failure is a
no-go: fix the harness, record why as an addendum, re-pilot. The pilot
does not tune any parameter above.

## Amendment policy

Append-only dated addenda. No parameter above changes after the pilot's
go decision. A discovered defect is fixed in code, recorded here with its
evidence, and — if it affected collected trials — those trials are
re-collected, not re-interpreted.
