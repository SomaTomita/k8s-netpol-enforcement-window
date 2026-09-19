# Pre-registration: Experiment 1 (unprotected window)

**Registered:** not yet submitted to an external registry (OSF). This
document is the frozen protocol; the OSF registration step (creating the
project, uploading this file, timestamping it) is a manual step for the
author to complete before Experiment 1's real runs begin. Filling in a
registration date or URL here ahead of that would misrepresent this
document as already registered — it is not.

**Frozen as of:** commit that introduces this file. Any change to a value
below after that point is an amendment, not an edit — see "Amendment
policy".

## RQ

Does a time window exist, at Pod startup, during which a `NetworkPolicy`
that should block traffic to that Pod has not yet been enforced? If such a
window exists, how does its size vary with CNI implementation and Pod
churn rate?

## Primary outcome

```
window = t_blocked - t_ready
```

- `t_blocked`: computed from `cmd/prober`'s raw observation stream via
  `first_sustained(observations, k, want="blocked")`
  (`src/npw/analysis/window.py`, mirrored by `internal/probe/sustained.go`).
- `t_ready`: **candidate C** (`cmd/victim`'s self-report — the instant its
  listener starts accepting connections), per
  `docs/adr/0003-t-ready-definition.md`. Candidate B (CRI `startedAt`) is
  recorded alongside every run as a cross-validation check, not as the
  primary outcome. Candidate A (`cmd/watcher`) is recorded for diagnostic
  purposes only — ADR 0003 found its bias dominated by the Kubernetes
  readiness probe's `periodSeconds >= 1` polling cadence, two orders of
  magnitude larger than the windows this project measures, and disqualified
  it as a basis for reported results.

`window > 0` means an unprotected window existed. `window <= 0` means
enforcement was already in effect at or before `t_ready` (healthy).

## Frozen parameters

| Parameter | Value | Basis |
|---|---|---|
| `t_ready` definition | Candidate C | `docs/adr/0003-t-ready-definition.md` — candidates B/C agreed to within 4.4-5.4ms across 3 pilot trials; candidate A diverged by 17-939ms, disqualifying it |
| Sustained-block threshold `k` | 3 | Design default carried from the original harness plan and `internal/probe/sustained.go`'s own tests; avoids a single dropped packet being misread as `t_blocked`. **Not yet validated against this project's own observed noise rate** — see "Known gaps" |
| Probe interval `p` | 1ms | `cmd/prober`'s existing default, used in every pilot run to date. The smallest gap this project has measured so far between two independent `t_ready` candidates (B↔C) is ~4.4ms; 1ms keeps quantization error well below that |
| CNI | Cilium 1.20.0 / Calico (TBD) / Antrea (TBD) | Cilium is implemented and piloted (`deploy/kind/cilium.yaml`, `deploy/cni/cilium-values.yaml`). **Calico and Antrea have no deploy manifests yet** — see "Known gaps" |
| Pod churn rate | 1 / 10 / 60 pods per minute | Provisional. Chosen to span "effectively no churn" (this project's every pilot to date, N=1 pod) up to a rate plausible under autoscaling. **Not validated against actual multi-pod resource usage on the machine these will run on** — no churn-rate implementation exists yet to run that check against (see "Known gaps"), and no specific RAM budget has been confirmed sufficient for the 60/min level |
| Repetitions per condition | 30 | Not derived from a formal power analysis — this project has no prior estimate of the true effect size (that is the open question), so no power calculation could target a specific detectable effect. 30 is a practical minimum for bootstrap CI stability (`src/npw/analysis/stats.py`'s `bootstrap_ci`). If pilot data collected under this exact protocol shows per-condition variance high enough that 30 repetitions yield unstable CIs, repetitions will be increased via a **documented amendment made before analysis of the full dataset**, never after seeing results that would motivate increasing N |
| Policy under test | `default-deny-ingress` (`deploy/policies/baseline/default-deny-ingress.yaml`) | The only baseline policy implemented; sufficient for this RQ (observing enforcement onset does not require an accompanying allow rule) |

## Analysis plan

- Report **median + bootstrap 95% CI** (`src/npw/analysis/stats.py`), not
  point estimates alone.
- CNI-to-CNI comparisons are pairwise, with multiple-comparison correction
  (method to be named in the amendment that adds Calico/Antrea support —
  not yet decided, since only one CNI is currently implementable).
- **Negative results are reported.** A condition where every trial's window
  is at or below the observation floor (`p`, and any residual harness
  latency documented in `docs/threats-to-validity.md`) is reported as "no
  window detected above the floor for this condition," not omitted.
- Candidate B is compared against candidate C on every run; a run where
  they diverge by more than the ADR 0003 pilot's observed range (~5ms)
  is flagged and investigated before its `window` value is trusted.

## Exclusion criteria

A run is excluded from analysis if `error_rate > 0.05` (the fraction of
`cmd/prober` observations classified `Error` rather than `Allowed` or
`Blocked` — see `internal/probe/outcome.go`). The exclusion rate itself is
reported alongside results, not silently absorbed.

## Known gaps (must close before Experiment 1's real runs, not before this document is frozen)

Pre-registration freezes the *design*; these are readiness gaps in
executing it, tracked so this document isn't mistaken for "ready to run":

1. **Calico and Antrea have no `deploy/kind/`, `deploy/cni/` manifests.**
   Only Cilium is currently testable. Extending the CNI row above beyond
   Cilium requires implementing these first.
2. **No churn-rate implementation exists yet.** `experiments/exp1-window/matrix.yaml`
   (this experiment's frozen matrix) declares the three churn levels above,
   but no orchestrator exists to act on `churn_rate_per_min` — `src/npw/`
   currently has only matrix expansion (`matrix.py`) and analysis
   (`analysis/`), no run-driving module, and `scripts/collect.sh` deploys
   exactly one victim Pod per invocation. Every pilot to date has used
   exactly one victim Pod; the resource-usage check this parameter needs
   (see the churn-rate row above) cannot happen until this exists.
3. **`k=3` and the churn levels are reasoned defaults, not pilot-validated.**
   Both should ideally be checked against a short pilot once churn-rate
   support exists, before the full pre-registered run begins. If that
   pilot changes either value, it is an amendment (see below), made before
   the full dataset is collected.
4. **The harness's own resolution floor is still being closed.** See
   `docs/threats-to-validity.md` — the in-cluster prober's own Pod-creation
   latency (650-670ms, tracked as issue #39) is being addressed separately
   from this document.

## Amendment policy

Any change to a frozen parameter above, made after this document exists but
before the full pre-registered dataset is analyzed, must be recorded as a
dated addendum to this file (append, do not silently edit the table above)
stating what changed, why, and that it was decided before seeing the
outcome it might otherwise appear to be motivated by. No parameter is
changed after seeing results in order to produce a more favorable outcome.

---

## Addendum 1 — 2026-09-19, before any Experiment 1 data collection

**Status of the dataset at the time of writing:** no trial has been run
under this protocol. `data/raw/exp1/` does not exist in this repository
and no Experiment 1 result — not one trial, not one condition — has been
seen by anyone. Every decision recorded below was therefore made without
sight of the outcome it might otherwise appear to be motivated by, which
is what the Amendment policy requires of an addendum. Live-cluster numbers
do appear below — from ADR 0003's pilot trials
(`docs/adr/0003-t-ready-definition.md`) and from later development runs.
None of them was produced under this protocol. Not all of them predate
it: `dev/dev-20260919-012023` was run on 2026-09-19, 41 days after this
document was frozen (`2eeead9`, 2026-08-09), as a harness development
run and not as an Experiment 1 trial. They are cited as instrument
characteristics, never as results.

This addendum does three things: it closes Known gaps 1 and 2; it names
the analysis decisions the "Analysis plan" section above deliberately left
open; and it records, before the fact, the limitations that building the
harness surfaced. Nothing in the frozen-parameters table above is edited
by it — where this addendum adds detail to a frozen row, the row stands
and the detail is here.

### A. Infrastructure and protocol

**A1. CNI versions (closes Known gap 1).** The CNI row above is now fully
specified:

| CNI | Version | Install | Manifests |
|---|---|---|---|
| Cilium | 1.20.0 | `cilium/cilium` Helm chart | `deploy/kind/cilium.yaml`, `deploy/cni/cilium-values.yaml` |
| Calico | v3.32.2 | `projectcalico/tigera-operator` Helm chart, default (iptables) Linux dataplane — `calicoNetwork.linuxDataplane` is not set | `deploy/kind/calico.yaml`, `deploy/cni/calico-values.yaml` |
| Antrea | 2.7.0 | `antrea/antrea` Helm chart | `deploy/kind/antrea.yaml`, `deploy/cni/antrea-values.yaml` |

The three version strings are pinned in `Taskfile.yml`
(`CILIUM_VERSION`/`CALICO_VERSION`/`ANTREA_VERSION`) and are recorded into
every trial's `meta.json` as `cni_version` by `npw.runner.write_meta`, so
each trial's own record states the version it ran under rather than
relying on this table.

The three datapaths do not surface a denied connection the same way:
Cilium's eBPF datapath drops the packet (the dial times out), Calico's
iptables dataplane `REJECT`s (connection refused), and Antrea's OVS
datapath has not been characterised against this harness. All three are
normalised to `Blocked` in exactly one place,
`internal/probe/outcome.go`, per `CLAUDE.md`'s CNI-agnostic-classification
rule, and nowhere else. The classification is CNI-agnostic; the
*cadence* at which observations arrive is not, which is recorded as B1
below.

**A2. Overlay / encapsulation, and why it is not a confound here.**
Recorded so that this is on the record as considered rather than missed:

- Cilium runs the chart's defaults, `routingMode: tunnel` with
  `tunnelProtocol: vxlan`; `deploy/cni/cilium-values.yaml` overrides
  neither.
- Calico's IPPool is set to `encapsulation: VXLANCrossSubnet`
  (`deploy/cni/calico-values.yaml`), chosen to match Cilium's VXLAN rather
  than the chart's IPIP default.
- Antrea runs the chart default `trafficEncapMode: encap`
  (`deploy/cni/antrea-values.yaml`), deliberately not overridden.

The cluster is single-node by design (ADR 0002), so all pod-to-pod traffic
on the measured path is node-local and is never actually encapsulated
under any of the three settings — `VXLANCrossSubnet` in particular
encapsulates only traffic crossing a subnet boundary, of which there is
none here. What this experiment measures is enforcement *onset*, a
control-plane property (when the CNI agent finishes programming its
datapath), not a property of how a forwarded packet is wrapped. The
encapsulation settings are therefore inert on the measured path and are
not equalised further. If this protocol is ever extended to multiple
nodes, that reasoning lapses and the setting becomes a live confound;
ADR 0002 would have to be revisited first in any case.

One further cross-arm configuration difference is recorded here for the
same reason. The Cilium cluster runs with `kubeProxyMode: none` and
`kubeProxyReplacement: true`, while the Calico and Antrea clusters keep
kind's default kube-proxy (`deploy/kind/*.yaml`). This is not equalised,
because each is the CNI's own supported default deployment and forcing a
non-default combination would test a configuration nobody runs. It is not
expected to touch the measured path — the prober dials the victim Pod's
IP directly, never a Service, so kube-proxy is not on that path — but it
is a real difference between the arms and is stated rather than omitted.

**A3. Churn, operationalised (closes Known gap 2).**
`churn_rate_per_min = r` is a *combined* rate: the victim Pod is the first
of the `r` Pods per minute, and the remaining `r - 1` are background Pods
(`npw.churn.background_rate`). At `r = 1` there is no background churn and
no churn driver is started at all.

- Background Pods are created in the trial's own namespace on an evenly
  spaced, jitter-free schedule of `60 / (r - 1)` seconds
  (`npw.churn.churn_schedule`), so a trial's achieved rate is checkable
  against its configured one. It is recorded so that it can be:
  `scripts/churn.py`'s summary line carries `elapsed_s` and the
  wall-clock stamps of its first and last successful creation, and
  `npw.runner` writes the derived rate into each trial's `churn.log`
  (`achieved_rate_per_min`). **No tolerance gates a trial on it.**
  Picking a threshold now, with no pilot data, would be the post-hoc
  calibration the Amendment policy forbids — the same objection B2
  records for the B↔C divergence threshold — so the tolerance for
  flagging a degraded achieved rate is to be set by the pilot and
  recorded here as a dated amendment. Each is deleted once it is at least 15 s
  old, reaped at the next scheduled creation rather than on a timer of
  its own; any still short of that when the schedule stops are reclaimed
  by the trial namespace's own deletion.
- They carry `role=churn` (`scripts/churn.py`). That label is matched by
  the policy under test — `default-deny-ingress`'s `podSelector: {}`
  (`deploy/policies/baseline/default-deny-ingress.yaml`) selects every Pod
  in the namespace — so the CNI agent must realise policy for them
  concurrently with the victim. That concurrent realisation load is what
  this variable manipulates. It is *not* matched by the prober's
  `-target-selector=role=victim` (`deploy/workloads/prober.yaml`), so a
  churn Pod can never be mistaken for the measurement target.
- Each churn Pod is created with `terminationGracePeriodSeconds: 0`
  (`scripts/churn.py`'s strategic-merge override), for the same reason the
  victim is (`deploy/workloads/victim.yaml`): a Pod lingering in
  `Terminating` holds CNI endpoint, identity and policy-map state on the
  single node and both depresses the achieved creation rate and bleeds
  into the next trial.
- Churn is warmed up for 30 s (`npw.runner.CHURN_WARMUP_S`) before
  `scripts/trial.sh` is invoked, so background creation is already under
  way when the measurement starts rather than starting with it.

**A trial whose churn did not verifiably happen fails; it is not
recorded.** `npw.runner.verify_churn` requires the churn driver's own
summary line to report `cut_short=true` — positive proof that churn was
still actively scheduled at the instant the trial finished, given the
deliberately over-long schedule (`CHURN_OVERRUN_MARGIN_S`) — and requires
that no Pod creation failed at or before the measurement window closed,
where the window's close is anchored on `prober.jsonl`'s last observation
(`npw.runner._window_close_s`). A trial failing either check is moved to
`data/raw/exp1-failed/<run_id>/` with a `REASON.md` and re-offered by
`npw.runner.pending`; `data/raw/` is never overwritten (`CLAUDE.md`). A
verification step that cannot read what it needs fails the trial rather
than passing it (`npw.runner._check_churn`).

**A4. One namespace per trial, and a cold node between trials.** Each
trial runs in a freshly created namespace `t-<run_id>`
(`scripts/trial.sh`), and no trial begins measuring until no `t-*`
namespace is `Terminating` and no prober Pod from a previous trial remains
(`wait_for_cold_node`, 180 s bound, implemented in both
`scripts/trial.sh` and `npw.runner` — the runner's copy runs *before*
churn starts, so churn's warm-up is not burned against a namespace that is
still going away).

Two reasons, both about not measuring the previous trial:

1. Repeating trials in one namespace with an identical Pod label set lets
   the CNI reuse cached identity and policy state, so a later trial can
   see enforcement land sooner purely because the caches are warm. A new
   namespace per trial makes the label set new every time.
2. A namespace that is still `Terminating` is still releasing CNI
   endpoints, identities and policy-map entries inside the same agent
   whose reaction latency is this experiment's dependent variable. That
   teardown-side effect is the subject of CVE-2024-7598
   (`docs/related-work.md`), and it is a distinct phenomenon from the
   startup-side one measured here. Allowing it to overlap the next
   trial's measurement would mix the two.

**A5. Policy before the victim.** `scripts/trial.sh` applies
`default-deny-ingress` to the trial namespace before the victim Deployment
is created, and the prober is already deployed and watching by then. The
research question is about a Pod starting under a policy that already
exists; applying a policy to an already-serving Pod measures the
late-policy / teardown case instead, which is CVE-2024-7598's shape and
not this protocol's subject.

**A6. Trial order and cluster layout.** One `kind` cluster per CNI
(`npw-<cni>`, `deploy/kind/<cni>.yaml`); the three CNIs are never
co-resident. Within a CNI, trials are interleaved round-robin across churn
levels (`npw.runner.interleave`): every churn level for repetition *N*
runs before any run of repetition *N+1*. This spreads drift in machine
state — thermal, cache warmth, leftover load — across conditions instead
of loading it onto whichever condition happens to run last. The order is
derived deterministically from `experiments/exp1-window/matrix.yaml` via
`npw.matrix.expand`, and a resumed run reproduces the same remaining
order, because the runner keeps no state of its own and re-derives what is
pending from the filesystem (`npw.runner.pending`, keyed on
`scripts/trial.sh`'s own `checksums.sha256` completion marker).

**A7. `k = 3` and the churn levels are retained, pending the pilot.**
Neither is changed by this addendum. Both are written into every trial's
`meta.json` by `npw.runner.write_meta` (`sustained_k: 3`,
`churn_rate_per_min`, alongside `probe_interval_ns: 1000000` and
`dial_timeout_ns: 200000000`), so a trial carries the values it ran
under. Known gap 3 stays open: if the pilot
changes either value, Addendum 2 records that before the full dataset is
collected, never after.

Known gap 4's specific remedy — the prober already running and watching
for the victim internally rather than being created fresh once its IP is
known — is now implemented (`cmd/prober`'s `-target-selector`,
`deploy/workloads/prober.yaml`). Three runs under it place the prober's
first observation 6.04, 14.75 and 8.49 ms after candidate C, against
648-904 ms under the architecture ADR 0003's pilots used
(`docs/threats-to-validity.md` records both sets). The residual floor is
therefore one to two orders of magnitude smaller. Whether it is small
*relative to the window* is not stated here and cannot be: no window has
ever been observed (D1), so the phenomenon has no measured magnitude to
compare a floor against, and the nearest published number
(`docs/related-work.md`'s "< 600 ms" for policy-add to convergence)
measures a different interval from a different starting point. The floor
also rests on three runs, none of them under churn or under the full
matrix. It is therefore carried as a live resolution floor of unknown
significance rather than as a closed problem, and quantifying it against
an actually observed window is part of what the D1 positive control is
for. The
frozen Known gap 4 above cites the superseded "650-670ms" figure; it is
corrected in ADR 0003's "Correction, 2026-09-19" and is not edited there,
per the append-only rule. The change also carries a cost of its own,
recorded as D2 below.

**Two further statements in the frozen text above are flagged here rather
than edited**, for the same reason:

- **Known gaps 4** — "650-670ms". Superseded; the recomputed range is
  648-904 ms (ADR 0003's correction, and A7 above).
- **Primary outcome**, on candidate A — "two orders of magnitude larger
  than the windows this project measures". No window has ever been
  observed by this harness (D1), so there is no measured window
  magnitude for candidate A's bias to be two orders of magnitude larger
  than. What the pilot actually establishes is a comparison among the
  three candidates: A's 17-939 ms spread against B↔C's 4.4-5.4 ms
  agreement. The disqualification of candidate A is unaffected — it never
  depended on the window's size — but the stated ratio is not a measured
  one. The same wording has been rewritten where it appears in
  `docs/methodology.md` and `docs/threats-to-validity.md`. In ADR 0003 it
  is deliberately left in place — in the Context's finding 2 and in the
  Decision's final sentence — as the historical record of the reasoning
  at decision time, and withdrawn by that file's
  "Correction, 2026-09-19", subsection (b), which names both sites.

### B. Measurement resolution

**B1. The dial timeout, not `p`, sets the cadence while traffic is
blocked.** `cmd/prober`'s probe loop dials synchronously on a 1 ms ticker
with a 200 ms per-attempt dial timeout (`probeLoop`). Where a CNI drops
the packet (Cilium, and Calico's eBPF dataplane, which this experiment
does not use), a blocked dial consumes the full 200 ms before it returns,
so consecutive `Blocked` observations are ~200 ms apart regardless of the
1 ms interval. Where a CNI `REJECT`s (Calico's iptables dataplane, used
here), the dial returns promptly and the cadence stays near `p`. The
cadence is therefore CNI-dependent even though the classification is not.

Three consequences, recorded rather than corrected:

- **`t_blocked` is not shifted by this.** `first_sustained` returns the
  offset of the *first* observation in the k-run, and each observation's
  offset is captured immediately before its dial (`cmd/prober`'s
  `observation`). The last `Allowed` dial before the transition returns
  promptly, so the transition instant is still located to within about
  `p`.
- **Confirming a run costs wall-clock time.** Establishing `k = 3`
  consecutive `Blocked` observations takes up to `(k - 1) x 200 ms` after
  the transition on a dropping CNI, and a single interrupting `Error`
  restarts that cost. A trial's duration must carry that headroom past the
  transition, and a trial that ends inside it is right-censored (C3).
- **The right-censored lower bound is coarser than `p` would suggest.**
  Its slack is set by this cadence rather than by the probe interval;
  quantified in D3.

**B2. Two B↔C divergence thresholds, deliberately not the same number.**
The Analysis plan above flags a run whose candidates B and C diverge by
more than "the ADR 0003 pilot's observed range (~5ms)". That criterion is
implemented at exactly 5 ms as the analysis-time threshold
(`npw.analysis.trial.BC_GAP_MAX_NS`), which sets `b_c_flagged` on a trial
and is counted per condition as `n_b_c_flagged`. `scripts/trial.sh`
separately carries a 50 ms runtime canary (`B_C_SKEW_LIMIT_NS`) which
emits a warning into `trial.json`. These answer different questions at
different times: the canary asks "is the harness badly broken right now",
while the 5 ms criterion decides whether a *reported* window can be
trusted. A trial can pass the canary and still be flagged by the analysis
threshold without either number being wrong. They are frozen here as two
numbers and are not to be harmonised into one.

**The 5 ms criterion sits below the maximum of the range it was derived
from, and will therefore fire on most runs. It is retained anyway.**
Recomputed from every run that records both candidates — six of the ten
under `data/raw/`; the four archived ones predate candidates B and C
entirely (`docs/threats-to-validity.md`) — as `b_c_skew_ns = B − C`, the
same sign convention as `trial.json` and ADR 0003's "B − C" column:

| Run | B − C | Exceeds 5 ms? |
|---|---|---|
| 20260808T163634Z (ADR 0003 pilot) | −5.421 ms | yes |
| 20260808T164053Z (ADR 0003 pilot) | −4.425 ms | no |
| 20260808T164456Z (ADR 0003 pilot) | −4.478 ms | no |
| 20260809T022055Z | −5.378 ms | yes |
| 20260809T060534Z | −11.494 ms | yes |
| dev/dev-20260919-012023 | −5.294 ms | yes |

**Four of six recorded runs exceed the criterion**, and one of the four
is `20260808T163634Z` — an ADR 0003 pilot trial, and the very trial that
defines the upper end of the "−4.4 to −5.4 ms" band the threshold was
derived from. Rounding that band to "~5ms" and then implementing the
threshold at exactly 5 ms put it *below* the maximum of its own source
range. That is a derivation error made when the criterion was written,
not something discovered in the data, and the data merely makes it
visible.

The threshold is **not** adjusted. Recomputing how often a diagnostic
fires and then moving it so that it fires less is precisely what this
document's Amendment policy exists to prevent, and the fact that no
Experiment 1 data exists yet does not make it a different act: the
counting has already happened. A diagnostic that fires often and gets
investigated is a better failure direction than one calibrated to stay
quiet. Concretely, then: `n_b_c_flagged` is expected to be large — on the
present evidence, a majority of trials — and a large value is not by
itself a reason to distrust a condition.

What the pilot must settle, before the full dataset is collected, is
which of two things is actually wrong: the threshold (derived below its
own source range, in which case it moves to a value justified from the
pilot's own B↔C distribution rather than from a rounded three-trial
band), or the B↔C agreement itself (`20260809T060534Z`'s −11.494 ms is
more than twice any ADR 0003 trial, and ADR 0003's whole case for keeping
candidate B as a cross-check rests on the two agreeing tightly). Either
resolution is a dated amendment recorded before the full dataset, per the
Amendment policy — never after seeing which resolution flatters the
results.

### C. Analysis plan

**C1. Multiple-comparison method (names what the Analysis plan left
undecided).** Cross-CNI comparison is a two-sided Mann-Whitney U test per
churn level (`npw.analysis.report._mann_whitney_p`), Holm-Bonferroni
adjusted across the CNI pairs tested (`holm_adjust`), **with the family
scoped to a single churn level** (`pairwise_cni`). Three CNIs give three
pairs per level; three churn levels therefore print nine comparisons
controlled as three families of three, not one family of nine. No further
correction is applied across churn levels, because the levels are separate
pre-registered conditions rather than repeated tests of one question. Any
write-up of that table must carry this sentence with it.

Two properties of the test as implemented, recorded now so they are not
discovered as surprises later:

- When every value in both arms is identical the exact two-sided p-value
  is 1, and the implementation returns exactly 1.0 rather than letting
  SciPy's tie-corrected variance produce `nan` and void the whole family.
- Any censored trial creates ties, which forces SciPy onto the normal
  approximation rather than the exact permutation distribution. That
  approximation is anti-conservative at small *n* (a 3-vs-3 complete
  separation reports p = 0.0636 where the exact two-sided minimum is 0.1).
  At the pre-registered 30 repetitions per condition this is fine; a
  pilot-sized comparison read off this table is indicative only and must
  be labelled as such.

**C2. Left-censoring: a floor, not a measurement.** A trial in which no
`Allowed` observation appears anywhere in the stream before `t_blocked`
has no witnessed `Allowed`→`Blocked` transition
(`npw.analysis.trial.evaluate`, `censored`). Its recorded
`t_blocked - t_ready` is the harness's own first-look floor: the true
window is at or below it. It is an **upper bound on the truth, not a
measurement of it**, and every statistic below treats it as one.

**C3. Right-censoring: the worst outcome, kept.** A trial in which
`first_sustained` finds no k-run of `Blocked` at all
(`npw.analysis.trial.evaluate`, `right_censored`) has `window_ns = None`
and carries `censoring_time_ns` — the last observation's offset minus
`t_ready` — as a **lower bound**: the true window is at least that.

Such a trial is **not excluded**. It is the single largest unprotected
window that trial could report, and dropping it would delete precisely the
worst outcomes from the sample and bias the reported median downward —
understating the very effect this experiment exists to measure. It is
therefore carried into every statistic by rank substitution (C4), and
`npw.analysis.report._included` filters only on `excluded_reason`, never
on `window_ns is not None`.

**C4. Rank treatment of censored trials, and its assumption.**
`npw.analysis.report.rank_substituted` places every right-censored trial
at `+inf` (above every observed window in its condition) and every
left-censored trial at `-inf` (below every observed window). Both are rank
placeholders, never magnitudes: no statistic computed here reads their
value, only their position. The two ends are not equally strong:

- The `+inf` end rests on an assumption: that each right-censored trial's
  true window really does exceed every window observed in its condition,
  i.e. that its `censoring_time_ns` already exceeds the largest observed
  window. A truncated or short trial breaks this, and ranking it at the
  top then pushes the median up. **The assumption is checked and reported
  per condition** as `right_censoring_order_violated`
  (`_right_censoring_order_violated`). It changes no number; it names the
  rows to distrust.
- The `-inf` end is weaker by construction. A left-censored trial's window
  is bounded above only, so its true rank is unknown; `-inf` picks the
  lowest rank the data permits rather than a known one. Leaving it at its
  recorded floor would pick the highest rank consistent with the data,
  which is the one choice that is certainly wrong. Each pairwise row
  therefore reports `n_left_censored_a` / `n_left_censored_b` so a
  p-value's exposure to that choice is visible.

**C5. When a median is reported as a bound rather than a point.**
`npw.analysis.report.summarize` reports one of five `estimate_kind`s per
condition, and this is the field to branch on when reading a row. They
are decided in a fixed order — `no_data`, `lower_bound`, `below_floor`,
`upper_bound`, `point` — so a condition that is at least half
right-censored reports a lower bound even when it also holds floors:

- **`lower_bound`** ("> x", no CI) when the median's own order statistic
  is a right-censored one. Identifiability condition:
  `2 * n_right_censored < n` must hold for a point estimate
  (`_median_is_identifiable`); the bound is the median of each trial held
  at the lowest value it could take (`_lower_bound_values`: the censoring
  time for a right-censored trial, `-inf` for a left-censored one, the
  measured value otherwise). A condition with enough floors can honestly
  yield `-inf` — reported as "no bound identified", not rounded into a
  number.
- **`upper_bound`** ("<= x", no CI) when a left-censored trial sits at or
  above the median's lowest rank in the *recorded* order
  (`_left_censoring_reaches_the_median`). The test is rank-positional, not
  a count of floors: a floor's recorded value can sit anywhere in the
  order, so "half or more are left-censored" would fire both when it
  should not and not when it should. A floor strictly below the median's
  rank cancels from both the sharp upper and lower bounds on the true
  median and so leaves a point estimate exact; one at or above it does
  not. The bound is `<=` rather than `<` because it is attainable.
- **`point`** otherwise, with a percentile bootstrap 95% CI computed over
  the rank-substituted values, so that a CI limit drawn from a floor comes
  out unbounded rather than printing as a measured number. `ci_high` may
  be unbounded where right-censored trials are present. A condition
  censored at both ends can leave the interval undefined, which is
  reported as such rather than as `nan`.
- **`below_floor`**: see C6.
- **`no_data`**: every trial in the condition was excluded.

No bound carries a confidence interval. This is the point of reporting a
bound at all.

**C6. "Every trial's window is at or below the observation floor",
operationalised.** The protocol's negative-results phrase is implemented
as a predicate about values (`_all_at_or_below_floor`): **every included
trial's window is at or below that trial's own probe interval `p`, and the
condition contains no right-censored trial.** A right-censored trial rules
the condition out outright — "enforcement never arrived" is the opposite
of "nothing above the floor".

Two clarifications of the frozen wording, both decided here and not in
response to any data:

- The implemented threshold is `p` alone. The phrase above reads "`p`, and
  any residual harness latency documented in
  `docs/threats-to-validity.md`"; that residual latency is measured on
  only three runs under the present architecture (A7) and on none under
  churn, so folding it into the threshold would loosen the predicate by
  an amount this project cannot yet state. Taking `p` alone is the
  stricter reading of the *predicate*: a condition whose windows lie
  between `p` and any residual floor is *not* labelled a negative result,
  and gets a median (or a bound) instead.

  **The direction this cuts must be stated plainly.** A narrower floor
  means fewer conditions are reported as "no window detected above the
  floor" and more report a window — so this choice makes the headline
  finding *more* likely to be positive, not less. It is a stricter
  predicate and a more permissive headline. It is recorded here, before
  any data, precisely because a threshold that favours the project's own
  hypothesis must not be chosen after seeing results. A reader who thinks
  the wider floor is the right reading can recompute: `trials.csv` carries
  every trial's `window_ns` and `probe_interval_ns`, so the predicate is
  reproducible against any threshold. If that residual latency is ever
  properly quantified, widening this threshold is an amendment.
- Left-censoring is deliberately **not** part of this predicate. "The
  harness's first-look floor" and "the observation floor `p`" are
  different quantities, and a condition of floors recorded at tens of
  milliseconds says nothing about windows being at or below 1 ms. Such a
  condition is an `upper_bound`, not a negative result.

**C7. Exclusion criteria are unchanged: exactly one criterion.**
`error_rate > 0.05` (`npw.analysis.trial.ERROR_RATE_MAX`) remains the only
reason a trial is excluded from analysis. No further exclusion category
has been added, and two candidates were explicitly rejected:

- "no sustained block found" is right-censoring, and is kept (C3).
- "no observations" was implemented as an exclusion category during
  development and **removed**: an empty `prober.jsonl` is a corrupt trial
  directory (`scripts/trial.sh` refuses to checksum one), and reporting it
  as an exclusion would inflate the pre-registered exclusion rate with a
  data-integrity failure instead of surfacing it as one. It now raises.

An excluded trial keeps its computed `window_ns` so that what was excluded
stays visible, per this document's requirement that "the exclusion rate
itself is reported alongside results". Consumers filter on
`excluded_reason`.

### D. Limitations recorded before the run

These are recorded now, before any Experiment 1 data exists, so that none
of them can later be mistaken for a post-hoc rescue of an inconvenient
result.

**D1. The harness has never observed a non-zero window, and "no window" is
currently indistinguishable from "cannot see the window".** No run
recorded in this repository contains a witnessed `Allowed`→`Blocked`
transition — checked observation by observation across all ten runs
under `data/raw/`, including the four archived ones
(`docs/threats-to-validity.md`). ADR 0003's
three pilot trials record 648-904 ms between the victim actually becoming
ready (candidates B/C) and the prober's first observation, and are
`Blocked` from that first observation onward — so in those trials the
harness was blind for the first 648-904 ms after `t_ready` and saw no
transition afterwards either. (A window *longer* than that gap would have
left an `Allowed` first observation and a visible transition; none did.
What the gap rules out is seeing a window shorter than it, which is the
case of interest.) (That range is a correction: ADR 0003
originally stated 650-670 ms, which excluded its own largest trial by
234 ms. Recomputed per trial in ADR 0003's "Correction, 2026-09-19".)
Crucially, the pattern survives the fix: the three runs under the current
prober architecture bring the first look to 6.04, 14.75 and 8.49 ms after
candidate C — one to two orders of magnitude closer — and every one of
them is still `Blocked` from its first observation
(`docs/threats-to-validity.md`). The sustained-detection path
(`first_sustained` over a real transition) has therefore never been
exercised end to end against a live cluster; every run that this
protocol's analysis could evaluate at all — the six carrying candidates
B and C — is what C2 calls the left-censored case.

This is a validity problem for a negative result specifically: a
pre-registered report of "no window detected above the floor" carries no
weight from an instrument that has never been shown capable of detecting
one.

**Recommended before the full run: a positive control.** A trial in which
the `default-deny-ingress` policy is deliberately applied at a known delay
*after* `t_ready`, rather than before it, produces an
`Allowed`→`Blocked` transition of known sign and approximate size. If the
harness recovers it — an uncensored trial whose window is close to the
imposed delay — the detection path is demonstrated. If it does not,
Experiment 1's negative results are uninterpretable and the harness must
be fixed before the full run. This is an instrument check, not part of the
research question: it measures the late-policy case (CVE-2024-7598's
shape, per A5), its data is not pooled with Experiment 1's, and it is not
added to the matrix.

**D2. `first_sustained` has no lower bound at `t_ready`: an assumption,
not a guarantee.** `npw.analysis.trial.evaluate` runs `first_sustained`
over the entire observation stream and only afterwards subtracts
`t_ready`; nothing constrains `t_blocked >= t_ready`. The prober is
deployed and watching before the victim exists, and `cmd/prober`'s
`waitForTargetIP` returns on the first *assigned Pod IP*, not on
readiness — deliberately, since an assigned IP is the earliest
addressable signal. *If* a kubelet status sync were to publish the victim
Pod's IP before its listener opens, the prober would begin dialing into
that interval; a dial into it yields `ECONNREFUSED`, which
`internal/probe/outcome.go` classifies as `Blocked`, and `k = 3` such
observations before `t_ready` would make the trial report
`t_blocked < t_ready` — a negative window, read as "enforcement was
already in effect — healthy", from a trial in which the harness never
saw the victim's protected state at all.

The path is architecturally unguarded, but **no run on which it can be
checked has exercised it.** Kubelet publishes Pod status at the start
of a `syncPod` pass from the PLEG-cached status, while sandbox creation
(which assigns the IP) and container start both happen later in that same
pass, so the IP normally reaches a watcher on a subsequent sync — usually
the relist that fires because the app container is already running. Of
the ten runs under `data/raw/`, the six that record a candidate C at all
have their first observation after it — +904.07, +670.45, +648.22,
+6.04, +14.75 and +8.49 ms — and the other four predate candidates B and
C, so the question cannot be put to them
(`docs/threats-to-validity.md`). This is therefore recorded as an unguarded assumption of
the analysis, not as an observed failure mode, and it does not explain
the all-`Blocked` pilots of D1.

The "victim always listens" invariant (`docs/methodology.md`) does not
cover this: it is stated against Kubernetes `Ready`, whose gate is the
`readinessProbe` on the listening socket, and candidate C is earlier than
that. This is an assumption of the analysis, not a guarantee of the
harness.

**Recorded response, decided here:** a trial that is left-censored *and*
carries a negative `window_ns` is the signature of this failure mode. Both
fields are already in `trials.csv`, so no new analysis code is needed. Any
condition containing such a trial is investigated before its numbers are
trusted, and the positive control in D1 would also surface it.
`npw.analysis.report` additionally carries an `n_negative_window` column
in `summary.csv` and in the rendered `summary.md`, so that investigation
is triggered by reading the report and not only by reading `trials.csv`:
a negative window is at or below the observation floor by construction,
so a condition made entirely of this failure mode would otherwise print
C6's "no window detected above the floor" phrase with nothing marking
it.

**D3. The right-censored lower bound is slightly anti-conservative.**
`censoring_time_ns` is the *last* observation's offset minus `t_ready`,
but `first_sustained` requires `k` consecutive `Blocked` observations, so
up to `k - 1` trailing `Blocked` observations may already sit on the
stream of a right-censored trial. Had the trial run one observation
longer, `t_blocked` would be the start of that trailing run — earlier than
the censoring time by the offset span of the trial's last `k - 1`
observations, which at `k = 3` is a single inter-observation gap. That is
about 1 ms where dials return promptly, and up to about 200 ms where each
blocked dial consumes the full timeout (B1). So "> `censoring_time_ns`"
can overstate by that much. Accepted as small relative to the bound it
qualifies, and recorded rather than corrected; note that the magnitude is
set by the dial timeout, not by `p`.

**D4. Two arms that are entirely left-censored compare at exactly
p = 1.0.** Under C4 every trial in both arms is ranked at `-inf`, the
all-ties case applies, and the pairwise test returns 1.0. This is correct
— two sets of upper bounds carry no information about which underlying
distribution is larger — but it is a *changed printed number*: comparing
the recorded floors at face value would have shown separation, and that
separation would have been an artifact of reading floors as measurements.
Recorded here so that the first time it appears it is not read as a bug.

**D5. The pairwise table reports no direction and no effect size.** Its
columns are the two CNIs, the group sizes, the censoring counts and the
raw and Holm-adjusted p-values (`PAIRWISE_FIELDS`) — nothing more. A small
`p_adj` says the two rank distributions differ; it does not say which CNI
is slower to enforce. A direction must not be read off the summary
medians where either arm is censored, since those may be bounds rather
than point estimates (C5). Any directional or magnitude claim in the
write-up must be stated in terms of the summary rows' `estimate_kind` and
bounds, and must say so explicitly.
