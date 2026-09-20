# Experiment 1b: policy timing — pre-registration

Frozen 2026-09-20, before the pilot. Amendments are dated, append-only
addenda at the end of this file; nothing above the first addendum is
edited after the fact. The freeze binds from the merge of the branch
that introduces this file — the point at which the protocol first
governs a collection, the pilot being the first run against it — so the
drafting and correction before that point are recorded in that branch's
commit history rather than as addenda, there being no collected data for
a change to be post-hoc to. From the merge onward the append-only
addendum rule above is the only way this protocol changes.

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

- `policy_apply_issued_ns` — offset from the run epoch to the instant
  before `kubectl apply` is invoked, like its sibling `t_ready_c_ns`; not
  a wall-clock timestamp.
- `policy_apply_returned_ns` — the same offset for the instant it
  returned.
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
  imposed 2000 ms delay; that excess additionally contains the 100 ms
  `kubectl logs` poll's lag in detecting candidate C and the `time.sleep`
  wake-up, neither of which is inside `L`, making it an upper bound. The
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
  `before` arm — it is what remains of that interval once the harness's
  whole setup pipeline up to the victim's own apply returning has elapsed
  (2118.8 ms of the 2636.2 ms to candidate C in the trial cited next,
  leaving 517.3 ms), not merely once the ~78 ms apply round trip has.
  A single pre-freeze instrument-check trial run while building this
  harness, on Cilium only (`data/raw/dev/smoke-with-victim/`;
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
  H2 is a prediction about *how often* the policy wins this race. It is
  not, on its own, a claim that the documentation's hazard is out of
  reach here: at exactly 20 of 30, ten trials per CNI witnessed the
  hazard directly. The two are therefore stated separately, and these
  three readings, and only these, are what a result licenses — they say
  what may be concluded, they do not partition the outcome space:

  - **Every `with-victim` trial left-censored, on all three CNIs** — and
    only then — supports "the hazard was not reachable at this scale with
    this ordering". Reaching it would then need a longer `L` (scale:
    nodes, policies, endpoints), which ADR 0002 places out of scope for
    this harness. The standing caveat holds: a left-censored trial bounds
    its window from above, it does not show the window is zero.
  - **Any uncensored `with-victim` trial** is a direct observation of the
    hazard the Kubernetes documentation describes — a Pod Ready and
    reachable while a policy that should have blocked it was not yet
    enforced — and its window is the measurement Experiment 1 could not
    make. That is the more significant of the two outcomes, not a failure
    of the experiment. It does not falsify H2 unless the censored count
    falls below 20.
  - **Fewer than 20 of 30 censored, on any CNI** — H2 is falsified: the
    race is closer than predicted.
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
arm, raw root `data/raw/exp1b-pilot`, never pooled. Run it as
`task exp1b:pilot`, which sets the matrix and that raw root together —
the pilot's six run ids are the same as the main matrix's first three
Cilium repetitions, so the two must never be selected independently.
Go criteria, all required: 6 of 6 trials complete with verifying
checksums; the 3
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


---

## Addendum 1 — 2026-09-20: pilot outcome and go decision

Six trials, Cilium, `data/raw/exp1b-pilot/`, never pooled with the main
run. All six completed and all six `checksums.sha256` verify.
`uv run python -m npw.gaps data/raw/exp1b-pilot` reports 0 of 6 over the
5000 ms threshold.

| run_id | arm | window (ms) | L (ms) | head start (ms) | left-cens. | right-cens. | error rate |
|---|---|---|---|---|---|---|---|
| run-0000-000 | with-victim | 96.9 | 1895.3 | +1798.4 | yes | no | 0.0 |
| run-0000-001 | with-victim | 8.0 | 1239.4 | +1231.4 | yes | no | 0.0 |
| run-0000-002 | with-victim | 2.2 | 1269.1 | +1266.8 | yes | no | 0.0 |
| run-0001-000 | at-ready | 1019.8 | 785.5 | −234.3 | no | no | 0.0 |
| run-0001-001 | at-ready | 513.4 | 329.1 | −184.2 | no | no | 0.0 |
| run-0001-002 | at-ready | 435.1 | 276.5 | −158.7 | no | no | 0.0 |

**Every pre-registered go criterion is met.** Six of six complete and
verifying; all three `at-ready` trials uncensored with `window` inside
[50, 2000] ms and `head_start < 0`; no gap over threshold. The harness
produces a witnessed `Allowed`→`Blocked` transition on demand in the
`at-ready` arm, and the three `with-victim` trials are left-censored,
the direction H2 predicts.

**Go — for the harness. The full run is additionally gated on host
quiescence, for a reason this pilot surfaced.**

Every one of the six trials records a candidate-B/candidate-C skew
between −46.3 ms and −140.9 ms, and five of the six tripped
`scripts/trial.sh`'s 50 ms runtime canary. Across Experiment 1's 270
trials that canary never fired once: their skews run from −24.2 ms to
−1.5 ms, median −3.9 ms. The pilot's whole timeline is slower in
proportion — median `t_ready_c` 5033 ms against Experiment 1's 2611 ms,
1.9x. The host was carrying a 15-minute load average of 16 while these
trials ran. Nothing here indicates a harness defect: the error rate is
0.0000 throughout, no gap exceeds the threshold, and the arms behave as
designed.

What it does mean is that a full run collected in this host state would
not be comparable with Experiment 1, and Experiment 1 is this
experiment's `before` arm. The effect is not uniform noise: the
`with-victim` head start came out at +1231 to +1798 ms here, against the
517.3 ms measured pre-freeze on a quiet host, so a loaded machine hands
the CNI two to three times the head start H2's threshold was calibrated
against. H2 would then be satisfied by the host's slowness rather than
by the CNI's speed.

**Consequence for the full run.** Before Experiment 1b's 180 trials are
collected, the host must be quiescent enough to reproduce Experiment 1's
own B/C envelope. This is not a new analysis parameter and changes
nothing above: the harness already records `b_c_skew_ns` per trial,
flags it at the pre-registered 5 ms in `b_c_flagged`, and reports
`n_b_c_flagged` per condition. The addition is procedural — the run is
started only when a check trial's skew falls inside Experiment 1's
observed range (|skew| at most ~25 ms), and the realised B/C
distribution is reported alongside the results either way, so a reader
can judge comparability directly rather than taking it on trust.

The pilot's own numbers are therefore reported here as evidence that the
instrument works, and are not used to calibrate anything.

---

## Addendum 2 — 2026-09-20: full run outcome

180 trials, Cilium 1.20.0 / Calico v3.32.2 / Antrea 2.7.0 x
{`with-victim`, `at-ready`} x 30, `data/raw/exp1b/`. All 180
`checksums.sha256` verify; `npw.gaps` reports 0 of 180 over the 5000 ms
threshold; 0 excluded, 0 right-censored, 0 negative windows, probe error
rate 0.0000 throughout. No analysis choice was changed after seeing the
data. Full tables: `docs/results/exp1b.md`.

**H1 — the censoring prediction held, the interval did not.** All 90
`at-ready` trials were uncensored, as predicted. Median `L` came out at
51.1 ms (Antrea, CI [50.5, 52.7]), 55.9 ms (Calico, [55.2, 56.4]) and
115.9 ms (Cilium, [104.4, 137.0]). Two of the three fall below the
pre-registered 100 ms lower bound, so H1 is falsified for Antrea and
Calico and holds for Cilium.

The band was built to contain a bracket whose lower end — Addendum 3's
127.8 ms — is a **Cilium** figure, and Cilium's result contains it. The
error was generalising one CNI's latency to all three while H3, in the
same document, predicted they would differ. Recorded here rather than
adjusted: the band was frozen before the pilot and stays as written.

**H2 — confirmed in the one form that licenses the strong conclusion.**
All 30 `with-victim` trials were left-censored on each of the three CNIs.
The inference rule above reserves "the hazard was not reachable at this
scale with this ordering" for exactly the case where every trial is
censored on all three CNIs, and that is what occurred. The margin was
wider than H2 argued: the head start came out at +386.8 to +570.6 ms
(medians 416-492 ms, close to the 517.3 ms measured pre-freeze) against
an `L` of 51-116 ms, so roughly four to nine times rather than two.

**H3 — confirmed.** Mann-Whitney U on witnessed `L` within `at-ready`,
Holm-adjusted across the three pairs: antrea vs calico p = 0.00117,
antrea vs cilium p = 9.06e-11, calico vs cilium p = 9.06e-11. All three
differ.

**Host state.** The run was started only after a check trial's B/C skew
fell inside Experiment 1's envelope (-8.9 and -22.9 ms against that
experiment's -24.2 ms maximum), per Addendum 1. Per-CNI skew medians came
out at -5.0 (cilium), -4.2 (calico) and -3.1 (antrea), against Experiment
1's -4.0 / -4.2 / -3.6 — the two experiments are comparable on this
quantity. Four trials of 180 exceeded Experiment 1's observed maximum:
three during a video call that began mid-run, all in the `with-victim`
arm where every trial is left-censored and no `L` is computed, and one at
the first trial of Antrea's `at-ready` condition, consistent with a cold
start.

**No trial was discarded.** The host-level discard rule in Analysis plan
item 6 covers inter-observation gaps over 5000 ms and nothing else, and
no trial met it. Introducing a skew-based exclusion after seeing which
trials it would remove is precisely the post-hoc selection this
pre-registration exists to prevent. The four trials remain in the dataset
and their counts are reported in the `B/C flagged` column.

**Interruption and resumption.** The run was paused when the video call
began, after 32 trials, and resumed once a fresh check trial passed.
Nothing was recollected: `pending()` resumes from each trial's completion
marker, and trial order is interleaved by repetition, so a pause changes
which wall-clock minute a trial ran in and nothing else.


---

## Addendum 3 — 2026-09-20: upstream report

The `at-ready` arm's latencies and the `with-victim` arm's outcome were
reported upstream as
[kubernetes/website#57642](https://github.com/kubernetes/website/issues/57642),
asking whether a non-normative note on the magnitude of the NetworkPolicy
handling delay is in scope for the Pod lifecycle section. It is a question,
not a proposed wording, and it states that the figures are single-node and a
floor rather than an estimate. Experiment 1's results were deliberately not
reported upstream (`docs/results/exp1.md`, Upstream) because they
corroborated the page without adding magnitude; this addendum records that
Experiment 1b changed that judgement and why.
