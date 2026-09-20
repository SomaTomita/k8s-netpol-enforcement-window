# Measurement Methodology

This document defines what every number in this repository means. Read it
before touching any measurement code, and before reading any result this
project produces. Nothing here is a measurement result — it is the
definition that gives measurement results their meaning.

## What is measured

```
window = t_blocked - t_ready
```

- `t_ready`: the moment a Pod becomes able to receive traffic.
- `t_blocked`: the moment traffic that a NetworkPolicy should have blocked
  was actually first observed to be blocked.
- `window > 0`: the Pod was Ready while traffic that should have been
  blocked was still getting through — an unprotected window existed.
- `window <= 0`: enforcement was already in effect before (or at the
  instant) the Pod became Ready. No unprotected window.

This project measures exactly one quantity and its distribution across CNI
implementations and Pod churn conditions. Everything else in this
repository — the prober, the watcher, the classifier, the analysis layer —
exists to produce that one number honestly.

## `t_ready`: three candidate definitions

Kubernetes' `metav1.Time` type serializes as RFC3339, which is
second-precision. The existence of a separate `MicroTime` type is itself
evidence that the standard API timestamps cannot represent a sub-second
value: Kubernetes needed a different type specifically because
`metav1.Time` cannot do it. A Pod's `Ready` condition's
`lastTransitionTime` is therefore reported to the second, which is useless
for measuring a window that may be millisecond-scale. `t_ready` cannot
come from reading that field off the API response — it has to be captured
some other way.

Three candidate sources exist:

| Candidate | How it's captured | What it actually measures | Trade-offs |
|---|---|---|---|
| **A — watch-local timestamp** | `cmd/watcher/` subscribes to the Kubernetes API via a `client-go` watch and stamps a monotonic clock the instant it *receives* the event carrying `PodReady=True` (not when it finishes decoding or processing it). | The apiserver's notification time, plus watcher-side receive latency. It does **not** measure when the Pod itself became able to receive traffic — only when the control plane told an external watcher about it. | Cheapest to implement; requires no changes to the workload under test, so the harness itself stays out of the causal path being measured. Adds apiserver + watch-channel latency on top of the true readiness instant. That latency biases the measured window **downward** (`t_ready` is recorded too late) — it can only make a reported window look smaller than the true one, never larger. |
| **B — CRI-level timestamp** | Read a container start/ready signal from the Container Runtime Interface (via kubelet or a CRI shim), closer to where the container process itself starts. | Closer to when the container process actually started, ahead of whatever kubelet has reported upward to the apiserver. | Removes apiserver/watch latency from the measurement. Heavier to implement and to keep CNI-agnostic (requires instrumenting or querying the CRI layer directly). Still not the same instant as "can receive traffic" — a started container process is not necessarily an accepting listener yet. |
| **C — victim self-report** | The victim workload (`deploy/workloads/victim.yaml`) emits its own timestamp the moment its listener socket is open and accepting connections. | The instant the workload can actually receive traffic — the definition conceptually closest to what `t_ready` is supposed to mean. | Most faithful to the concept. Requires in-Pod instrumentation that itself becomes a variable to control for, and only reflects the victim's own view, not what the control plane or CNI believed at that instant. |

**Candidate C is the primary implementation** (`cmd/victim`), per a live
pilot comparing all three (`docs/adr/0003-t-ready-definition.md`). Candidate
A was the initial implementation, on the reasoning that it is cheapest to
build correctly and keeps the harness itself out of the workload's path,
with an expected bias that is small and conservative (added latency
pushing the recorded `t_ready` later than the true instant, never earlier).
The pilot found that reasoning wrong in magnitude, not direction: candidate
A's actual bias is dominated by the Kubernetes readiness-probe's
`periodSeconds >= 1` polling cadence, not by apiserver/watch latency, and
behaves as effectively-random noise of up to ~1 second rather than a small
constant — two orders of magnitude larger than the 4.4-5.4 ms on which
candidates B and C agree with each other, which is the comparison the
decision rests on. (An earlier version of this sentence compared it
instead to "the windows this project measures". No window has ever been
observed by this harness, so it has no measured magnitude to compare
against; see `docs/threats-to-validity.md` and ADR 0003's
"Correction, 2026-09-19".) Candidate B (CRI-reported container start time, read via
`crictl inspect`) agreed with candidate C to within 4.4-5.4 ms across
ADR 0003's three pilot trials and is kept as an ongoing cross-check. That
range is ADR 0003's alone and is not a general property: of the six runs
that record both candidates, `20260809T060534Z` has a B−C gap of
−11.494 ms, more than twice any ADR 0003 trial (the six values are
tabulated in `experiments/exp1-window/preregistration.md`, Addendum 1
B2). Candidate A is kept only
as a diagnostic signal, not as the basis for reported windows. See
`docs/adr/0003-t-ready-definition.md` for the full pilot data and
reasoning.

## `t_blocked`: definition

`t_blocked` is the timestamp of the first probe attempt that begins an
unbroken run of **k** consecutive `Blocked` classifications (see
`internal/probe/sustained.go`). A single blocked attempt is not treated as
`t_blocked`, because a lone packet loss or transient error could otherwise
be misread as the moment enforcement started. The value of `k` is frozen
in the relevant experiment's pre-registration (`experiments/*/preregistration.md`)
before any run whose numbers get reported.

Choosing `k` trades detection delay for robustness to noise: a larger `k`
makes `t_blocked` less likely to be triggered by a spurious drop, at the
cost of needing `k - 1` further observations before the harness can *know*
that enforcement has begun. That delay does not shift the value recorded.
`first_sustained` returns the offset of the run's *first* observation, not
its last, so `t_blocked` is the first blocked attempt of the run whatever
`k` is. What the delay costs instead is trial duration — a trial that ends
part-way through the run records no `t_blocked` at all and is
right-censored (see "Observation resolution" below) — and it is why a
right-censored trial's lower bound can slightly overstate the truth.

## Outcome classification

Different CNI implementations surface a blocked connection differently.
The classifier in `internal/probe/outcome.go` exists specifically so that
difference never leaks into the reported numbers:

| Observation | Classification | Why |
|---|---|---|
| Connection succeeds | Allowed | |
| Connection attempt times out | Blocked | Typical of eBPF-based CNIs (e.g. Cilium, Calico's eBPF dataplane), which drop the packet rather than reply |
| Connection refused (RST received) | Blocked | Typical of iptables-based `REJECT` rules, which reply with a TCP reset |
| No route to host / network unreachable | Blocked | |
| Anything else | Error | Excluded from window computation; the error rate is reported alongside results, never silently dropped |

This table is deliberately CNI-agnostic. The same classifier runs
regardless of which CNI is under test, so the *detection method* itself
never becomes a confound in a cross-CNI comparison.

## Invariant: the victim always listens

`ECONNREFUSED` is ambiguous by itself — it can mean "a NetworkPolicy
actively rejected this connection" or "nothing was listening on that
port." This project removes that ambiguity with one invariant: the victim
workload's listener socket must already be accepting connections *before*
Kubernetes marks the Pod Ready. `deploy/workloads/victim.yaml`'s
`readinessProbe` checks the listening socket itself (not just process
liveness) to guarantee this. Under that invariant, once the Pod is Ready,
`connection refused` can only be attributed to policy enforcement, never
to a missing listener.

## Observation resolution

The prober's polling interval `p` (`cmd/prober/`, flag `-interval`) sets a
hard floor on precision: the measured window carries a quantization error
of up to `p`. If a measured window turns out to be the same order of
magnitude as `p`, the result describes the measurement instrument, not the
phenomenon under study. `p` is therefore always reported alongside any
window value. The pilot run (out of scope here) is what determines an
interval small enough that `p` is negligible relative to the windows
actually observed.

`p` is the interval between probe *attempts*, not the interval between
observations, and the two come apart once traffic is being blocked. The
prober dials synchronously on its `-interval` ticker with a per-attempt
`-timeout` (200 ms by default), so where a CNI drops the packet the dial
consumes the whole timeout before returning and consecutive `Blocked`
observations land ~200 ms apart regardless of `p`; where a CNI `REJECT`s,
the dial returns promptly and the cadence stays near `p`. This does not
shift `t_blocked`: `first_sustained` reports the *start* of the k-run, and
the last `Allowed` dial before the transition still returns promptly, so
the transition instant itself is located to within about `p`. What it does
cost is wall-clock time to *confirm* a run — up to `(k-1) × timeout` after
the transition on a dropping CNI — which a trial's duration has to carry.

Two things a trial can fail to see, distinguished because they bound the
truth in opposite directions:

- **Left-censored**: no `Allowed` observation precedes `t_blocked`
  anywhere in the stream, so no `Allowed`→`Blocked` transition was
  witnessed. The recorded `t_blocked - t_ready` is the harness's own
  first-look floor — an *upper* bound on the true window, not a
  measurement of it. This is a different quantity from the observation
  floor `p`, and must not be reported as "no window above the floor".
- **Right-censored**: no sustained run of `k` `Blocked` observations
  occurs within the trial at all. Enforcement never arrived while the
  trial was watching, so the true window is at *least* the bound the
  trial does record — `last_observation_offset_ns - t_ready_c_ns`, which
  is shorter than the trial's own length by its startup interval (see
  `experiments/exp1-window/preregistration.md`, Addendum 1 C3, which
  states the same bound). This is the largest window the trial could report, not a failed
  measurement, and dropping such trials would delete the worst outcomes
  and bias a reported median downward.

How each is carried into a reported statistic is frozen per experiment;
for Experiment 1 see `experiments/exp1-window/preregistration.md`,
Addendum 1.

## Policy timing

Experiment 1 applies the NetworkPolicy before the victim Deployment
exists. Experiment 1b (`experiments/exp1b-ordering/`) makes the apply
instant an independent variable, `policy_at`, with three values —
`before`, `with-victim` (immediately after the victim's apply returns),
`at-ready` (the instant candidate C is observed, plus an optional delay)
— implemented in one place, `scripts/trial.sh`, and recorded per trial:

- `t_policy_issued` (`trial.json` `policy_apply_issued_ns`): the offset
  from the run epoch to the instant before `kubectl apply` is invoked —
  an offset like `t_ready_c_ns`, not a wall-clock timestamp. Enforcement
  latency is measured from here so that the API round-trip is inside it,
  as it is for an operator.
- `t_policy_returned` (`policy_apply_returned_ns`): the same offset for
  the instant the apply returned, so the API part can be separated
  afterwards.

Two derived quantities join `window`:

```
L          = t_blocked  - t_policy_issued     enforcement latency
head_start = t_ready    - t_policy_issued     how long the CNI had before readiness
```

`L` shares `t_blocked` with `window` and therefore shares its censoring.
On a left-censored trial `L` is a floor, not a latency; the analysis
counts such trials and does not average them (`npw.analysis.latency`).
`head_start` is positive when the policy preceded readiness and negative
in the `at-ready` arm; it is reported so the ordering under test is a
measured fact per trial, not a label. A `window` that is positive
because `head_start` is negative is the late-policy case (the shape of
CVE-2024-7598), and is reported as such, not as a Pod having started
unprotected.

## Clocks

All per-observation timing in this project uses a monotonic clock (Go's
`time.Since` off a `time.Now()` epoch) — never `metav1.Time`. This is
workable specifically because the cluster is single-node
(`docs/adr/0002-single-node-kind.md`): every component that measures
`t_ready` or `t_blocked` shares the same host clock, so there is no
cross-machine clock-synchronization problem to solve.

### Combining `cmd/prober` and `cmd/watcher` output: the `-run-epoch` flag

By default each binary's `offset_ns` is `time.Since(processStart)`, where
`processStart` is a `time.Now()` captured independently at that binary's
own startup. That makes one binary's own stream of observations internally
consistent, but **not directly combinable with the other binary's stream**
— the two `offset_ns` columns are measured from two different, unrelated
origins.

Both `cmd/prober` and `cmd/watcher` accept `-run-epoch <unix-nanoseconds>`.
When a run orchestrator passes the same value to both binaries at launch,
each computes its `offset_ns` from that shared instant instead of its own
process start, so `run_epoch_ns + offset_ns` is comparable across the two
streams and gives an actual `nanoseconds since the Unix epoch` value — the
quantity `internal/record.RunRecord.TReadyNs`/`TBlockedNs` are documented
as holding.

This is the one deliberate exception to "never wall-clock": the shared
`-run-epoch` value is itself a wall-clock instant (Go's monotonic clock
reading has no meaning across process boundaries, so a cross-process
anchor cannot be anything else). The caveat this method otherwise avoids —
wall-clock drift from NTP correction — is not a practical risk here: the
anchor is read once, and every subsequent `offset_ns` within a run is still
a monotonic `time.Since` delta computed inside a single process, over the
run's duration of seconds to low minutes. If `-run-epoch` is omitted,
behavior is unchanged from before: each binary's `offset_ns` is relative to
its own process start, and the two streams are not combinable.
