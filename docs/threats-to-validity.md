# Threats to Validity

This is a living document. It is a skeleton at this stage, not a final
list — new threats are appended as implementation and pilot runs surface
them. Each entry states the threat and, where already decided, the
accepted mitigation or trade-off.

## Construct validity — does `window = t_blocked - t_ready` measure what it claims to?

- **`t_ready` candidate A's bias is large and effectively random, not small
  and conservative as originally expected.** A 3-trial pilot comparing all
  three candidates side by side (`docs/adr/0003-t-ready-definition.md`)
  found candidate A (watch-event receipt) lagging candidates B (CRI start
  time) and C (victim self-report) by 17 ms, 750 ms, and 939 ms across the
  three trials — not the small, roughly-constant apiserver/watch latency
  originally assumed, but noise on the order of the Kubernetes readiness
  probe's `periodSeconds >= 1` polling cadence, which the API provides no
  way to reduce below one second. That is two orders of magnitude larger
  than the 4.4-5.4 ms on which candidates B and C agree with each other
  across all three trials, and it is resolved by no longer using
  candidate A as the basis for reported windows (ADR 0003). The
  comparison is deliberately against the other candidates' mutual
  agreement and not against "the windows this project measures", which an
  earlier version of this entry said: no window has ever been observed by
  this harness, so there is no measured window magnitude for any noise
  figure to be compared against.
- **`connection refused` requires the victim-always-listens invariant to
  hold.** If the victim's `readinessProbe` were ever satisfied before its
  listener socket is actually accepting connections, `Error`-classified
  observations near the start of a run could be misclassified as
  `Blocked`. Mitigated by design (`deploy/workloads/victim.yaml` probes
  the socket itself), but this is a design assumption, not a proof, until
  directly checked against pilot data.
- **The sustained-detection threshold `k` trades false positives for
  detection delay.** Requiring `k` consecutive `Blocked` observations
  before declaring `t_blocked` avoids treating a single dropped packet as
  the end of the window. It delays when the harness can *know* that
  enforcement has begun — by the span of `k - 1` further observations
  after the first blocked attempt — but it does not shift the value
  recorded: `first_sustained` returns the offset of the run's *first*
  observation, not its last (`src/npw/analysis/window.py`,
  `internal/probe/sustained.go`). The cost of the delay is therefore paid
  in trial duration and in right-censoring (a trial that ends inside the
  run is censored rather than mismeasured), not in a biased `t_blocked`.
  `k` and `p` are reported alongside every result regardless.
- **The in-cluster prober's own startup latency is a second resolution
  floor, distinct from the polling interval `p`.** The prober cannot
  report anything before its own first dial attempt. An early version of
  `scripts/collect.sh` only created the prober Pod after waiting for the
  victim to reach phase `Running`, which meant every observation in a
  first live run against Cilium came back `Blocked`, with the prober's
  first attempt already recorded roughly 200ms after `t_ready`. Polling
  for the Pod IP directly instead of waiting on `Running` narrowed that
  specific gap to ~37 ms — **but a separate,
  still-open gap remains**: the same pilot that produced the ADR 0003 data
  found 648-904 ms between the victim actually becoming ready (candidates
  B/C) and the prober's first observation (recomputed from those runs'
  own streams — see ADR 0003's "Correction, 2026-09-19"; the figure
  previously stated here and there, 650-670 ms, excluded the largest of
  the three trials by 234 ms), entirely attributable to how
  long a freshly-created prober Pod takes to reach `Running` — scheduling
  and sandbox setup for a brand-new Pod, not anything this project's own
  code controls the timing of. Any run under the architecture those
  pilots used (prober Pod created only once the victim's IP is known)
  inherits this floor.

  *Provenance of the two figures in the paragraph above, established
  2026-09-19.* Both were written against candidate A (the watcher's
  timestamp), before candidates B and C existed, and both are
  re-derivable from `data/raw/`. "Roughly 200ms" is
  `archive/20260808T142646Z`: first observation at 6 941 499 752 ns,
  candidate A at 6 743 350 000 ns, difference **198.150 ms**; all 301 of
  its observations are `Blocked`, which is the "came back `Blocked`"
  that sentence describes. "~37 ms" is `archive/20260808T144331Z`: first
  observation at 8 776 665 886 ns, candidate A at 8 738 697 000 ns,
  difference **37.969 ms**. Neither figure was ever "confirmed against
  candidates B/C" — an earlier version of this entry said so, and that
  clause is removed: neither of those two runs carries a `victim.jsonl`
  or a `cri.jsonl`, so neither records a candidate B or C at all. The
  two figures also entered this document separately, not as a matched
  pair: `9b0ca67` (#34, the commit that added this entry and made the
  `Running`-versus-Pod-IP change) states only the "roughly 200ms"
  before, and leaves the after open — "some residual gap … necessarily
  remains. To be quantified once a pilot run reports the prober's
  first-observation offset alongside `t_ready` under the current
  script." The "~37 ms" was written in later, by
  `49b4df2` (#38). Neither commit names either of the two archive run
  ids above (`49b4df2` does name run ids — the three ADR 0003 pilots —
  but not these), so pairing the two figures with the two runs above is
  inference from the matching magnitudes, not a record.
- **That floor is now closed, and the current architecture's residual
  floor is single-digit to low-tens of milliseconds.** `cmd/prober` takes
  `-target-selector` and watches the Kubernetes API for the victim Pod's
  IP internally (`deploy/workloads/prober.yaml`), so the prober Pod is
  created and already running before the victim exists, and the
  Pod-creation cost above is no longer on the measured path. Three runs
  under this architecture, held in the author's `data/raw/` tree
  (`20260809T022055Z`, `20260809T060534Z`,
  `dev/dev-20260919-012023`; `data/raw/` is gitignored by design, so this
  document is the committed record of the figures), place the first
  observation 6.04 ms, 14.75 ms and 8.49 ms after candidate C
  respectively. That is one to two orders of magnitude better than the
  648-904 ms above. It is **not** thereby negligible, and this document
  makes no claim about how it compares to the phenomenon: no run under
  `data/raw/` has measured the phenomenon (see the
  never-observed-a-window entry below), so there is no magnitude to
  compare it against. The nearest published figure,
  `docs/related-work.md`'s "< 600 ms" from Cilium's scalability report,
  measures policy-add to convergence rather than Pod-Ready to
  enforcement, so it cannot stand in either. A floor of unknown
  significance is a resolution floor, not a solved problem — and it is
  precisely because the comparison cannot be made that it stays on this
  list. The change also carries a cost of its own, recorded two entries
  below.
- **A window shorter than the harness's first look is left-censored, and
  a floor is not a measurement.** When no `Allowed` observation precedes
  `t_blocked` anywhere in the stream, no `Allowed`→`Blocked` transition
  was witnessed: the recorded `t_blocked - t_ready` is the harness's own
  first-look floor, an *upper* bound on the true window. Treating such a
  value as a measured window inflates a reported median, and treating a
  condition full of them as "no window detected above the floor" conflates
  it with the different claim that every window was at or below the probe
  interval `p`. Mitigated by reporting such trials as bounds rather than
  point estimates (`src/npw/analysis/report.py`; the rules are frozen in
  `experiments/exp1-window/preregistration.md`, Addendum 1), not by
  removing the censoring, which the instrument cannot do.
- **A trial in which enforcement never arrives is right-censored, and
  excluding it would bias the median downward.** When no sustained run of
  `k` `Blocked` observations occurs within a trial at all, that trial's
  true window is at least the trial's own length — the single worst
  outcome it could report. Dropping such trials would delete precisely the
  largest windows and understate the effect being measured. Mitigated by
  keeping them, carrying a censoring time as a lower bound, and reporting
  a condition's median as a bound when the median's own rank is censored.
  The rank substitution that makes this work assumes each censored trial's
  window really does exceed every window observed in its condition; that
  assumption is checked and flagged per condition
  (`right_censoring_order_violated`) rather than assumed.
- **The prober's observation cadence is set by the dial timeout, not by
  the probe interval `p`, while traffic is blocked.** `cmd/prober` dials
  synchronously on its `-interval` ticker with a 200 ms `-timeout`, so
  where a CNI drops the packet each blocked dial consumes the full timeout
  and consecutive `Blocked` observations are ~200 ms apart regardless of
  `p`; where a CNI `REJECT`s, the cadence stays near `p`. The cadence is
  thus CNI-dependent even though the classification is not. `t_blocked`
  itself is not shifted (`first_sustained` reports the *start* of the
  k-run, and the preceding `Allowed` dial returns promptly), but
  confirming a run costs up to `(k-1) × 200 ms` of wall clock, and it is
  the dial timeout — not `p` — that sets how far a right-censored trial's
  lower bound can overstate the truth.
- **The harness has never observed a non-zero window, so "no window" and
  "cannot see the window" are not yet distinguishable.** None of the ten
  runs under `data/raw/` contains a witnessed `Allowed`→`Blocked`
  transition — checked observation by observation across all ten, not
  inferred from their first observations. ADR 0003's three pilot trials
  record 648-904 ms between candidates B/C and the prober's first
  observation, and are `Blocked` from that first observation onward; so
  are the three runs under the current architecture, whose first look is
  only 6-15 ms after candidate C. Of the four archived runs (see the
  entry below), two are `Blocked` throughout, one is `Allowed` with no
  `Blocked` at all (42 568 `Allowed` and 20 `Error`), and one opens on an
  `Error` — none of them witnesses a transition either. The
  sustained-detection
  path has therefore never been
  exercised end to end against a real transition, which is a validity
  problem for a negative result specifically. The response recorded before
  Experiment 1 runs is a positive control — a deliberately delayed policy
  application producing a transition of known sign and approximate size —
  as an instrument check whose data is not pooled with Experiment 1's
  (`experiments/exp1-window/preregistration.md`, Addendum 1 D1).
- **`first_sustained` is not bounded below by `t_ready`, so a pre-ready
  `ECONNREFUSED` would be read as enforcement. Unguarded, but not
  observed.** The window computation runs the sustained-run search over
  the whole observation stream and only then subtracts `t_ready`; nothing
  constrains `t_blocked >= t_ready`. The prober starts dialing as soon as
  the victim Pod has an IP (`waitForTargetIP` returns on an assigned IP,
  deliberately not on readiness). *If* a kubelet status sync were to
  publish that IP before the victim's listener opens, the interval before
  the listener opens would fall inside the observation stream; a dial into
  it yields `ECONNREFUSED`, which the classifier treats as `Blocked`, and
  `k` such observations before `t_ready` would produce
  `t_blocked < t_ready` — a negative window reported as healthy, from a
  trial in which the protected state was never actually seen.
  **No run on which it can be checked has exercised this path.** Kubelet publishes Pod
  status at the start of a `syncPod` pass from the PLEG-cached status,
  while sandbox creation (which assigns the IP) and container start both
  happen later in that same pass, so the IP normally reaches a watcher on
  a subsequent sync. Every run that records a candidate C at all — the
  six in-cluster runs; the four archived ones predate candidates B/C and
  have neither — has its first observation after it: +904.07, +670.45,
  +648.22, +6.04, +14.75 and +8.49 ms. The "victim always listens" invariant does
  not close the gap either: it is stated against Kubernetes `Ready`, and
  candidate C is earlier than that. Not mitigated in code; the signature
  (a left-censored trial with a negative window) is visible in the
  per-trial output and would be investigated before a condition's numbers
  were trusted.
- **`data/raw/archive/` holds four runs that predate the current harness
  and must not be read as evidence about enforcement.** They are
  `20260808T104955Z`, `20260808T140857Z`, `20260808T142646Z` and
  `20260808T144331Z`. All four carry a `prober.jsonl` and a
  `watcher.jsonl` but no `victim.jsonl` and no `cri.jsonl`, so they have
  a candidate A and neither candidate B nor C, and no `window` in this
  project's current sense can be computed from any of them.

  Provenance, to the extent the repository establishes it. All four run
  ids are UTC instants on 2026-08-08 (10:49, 14:08, 14:26, 14:43) and
  therefore precede the merge of the in-cluster prober
  (`1c22524`, "run prober in-cluster instead of via kubectl port-forward
  (#32)", 2026-08-08 16:02 UTC), of the dial-timeout classification fix
  (`2e48a27`, #33, 16:05 UTC) and of candidates B/C (`49b4df2`, #38,
  which is what ADR 0003 decided on).

  - `20260808T104955Z` is **established**: it carries a `port-forward.log`
    written by the `scripts/collect.sh` of that era
    (`git show 1c22524^:scripts/collect.sh`), whose own header
    states that the prober "reaches the victim through `kubectl
    port-forward` rather than running in-cluster … port-forwarded traffic
    does not traverse the CNI's normal enforcement path, so this script's
    own prober.jsonl will typically show 'Allowed' throughout even though
    a real in-cluster prober would see the policy block it. Use this to
    sanity-check that the pipeline runs end-to-end, not to draw
    conclusions about window durations." Its stream is 42 568 `Allowed`
    and 20 `Error`, with no `Blocked` at all, which is exactly what that
    header describes. Its median inter-observation gap is 1.004 ms — the
    probe interval — so its dials were returning essentially instantly.
    It is an architecture artefact, not an observation about policy
    enforcement.
  - The other three were **not** produced by that path, and the evidence
    is two-sided. They carry no `port-forward.log`, which that era's
    `collect.sh` wrote unconditionally on the port-forward path, so its
    absence is evidence rather than missing data. And their median
    inter-observation gaps are 201.208 ms, 201.112 ms and 201.210 ms
    respectively — dials consuming the full 200 ms `-timeout`, i.e.
    packets being dropped on a real enforcement path. A dead port-forward
    would instead leave the local port closed and refuse instantly, which
    `internal/probe/outcome.go` would also classify `Blocked` but at
    roughly the 1 ms cadence `20260808T104955Z` in fact shows. The gap
    distribution, not the outcome label, is what separates the two.

    What produced them is not recorded. The most the commit history
    supports is that all four run ids fall in the development window of
    #32/#33/#34. Two of them carry the gaps
    discussed above (198.150 ms and 37.969 ms after candidate A); the
    paragraph above pairs each figure with its run and with the commit
    that introduced it. `20260808T140857Z`'s
    291 `Error` against 7 `Blocked`, at a 201 ms gap, is what #33
    describes — dial timeouts misclassified as `Error` — but that is
    inference from the commit message, not a record, and nothing here
    rests on it.

  They are retained rather than deleted because deleting measurement data
  is worse than labelling it, and `data/raw/` is immutable (`CLAUDE.md`).

## Internal validity — could something other than CNI enforcement explain the observed window?

- **Single-node kind eliminates cross-node clock-sync error, but also
  cannot detect any effect specific to cross-node enforcement propagation**
  (`docs/adr/0002-single-node-kind.md`). Any conclusion from this
  project's data applies to single-node conditions and should not be
  generalized to multi-node deployments without qualification.
- **Observation resolution sets a floor on what's measurable.** If the
  measured window is the same order of magnitude as the prober's polling
  interval `p`, the result reflects instrument resolution, not the
  underlying phenomenon (`docs/methodology.md`, observation resolution).
  `p` is reported with every result specifically so this can be judged.
- **A previous trial's state on the node is a confound on the next
  trial's enforcement onset, in two distinct ways.** Repeating trials in
  one namespace with an identical Pod label set lets the CNI reuse cached
  identity and policy state, so a later trial can see enforcement land
  sooner purely because the caches are warm; and a namespace that is
  still `Terminating` is still releasing CNI endpoints, identities and
  policy-map entries inside the same agent whose reaction latency is the
  dependent variable — which is the teardown-side effect CVE-2024-7598
  describes (`docs/related-work.md`), a different phenomenon from the
  startup-side one measured here. Mitigated by a freshly created
  namespace per trial (`t-<run_id>`) plus a cold-node guard that refuses
  to start measuring while any `t-*` namespace is `Terminating` or a
  previous prober Pod remains (`wait_for_cold_node`, implemented in both
  `scripts/trial.sh` and `src/npw/runner.py`), and by zero termination
  grace periods on the victim and churn Pods. Trials are additionally
  interleaved round-robin across churn levels within a CNI
  (`npw.runner.interleave`) so that any residual drift in machine state
  spreads across conditions rather than loading onto whichever condition
  runs last. Bounded at 180 s: a guard that times out fails the trial
  rather than letting it measure a node that is still tearing the last
  one down.

## External validity — how far do results generalize?

- **CNI coverage is limited to whichever implementations are actually
  tested** (planned: Cilium, Calico, Antrea). Results characterize those
  specific CNIs and their tested versions/configurations; they are not
  automatically representative of every CNI implementation, or of the
  same CNI under a different configuration (e.g. a different Cilium
  datapath mode).
- **`kind` is not a production cluster.** Findings from a single-node,
  disposable, local `kind` cluster may not transfer quantitatively to
  managed Kubernetes offerings with different control-plane latency
  characteristics, though the qualitative existence of a window is
  expected to generalize (per the Kubernetes documentation acknowledgment
  cited in `docs/related-work.md`).
- **Pod churn conditions tested are whatever the experiment matrix
  defines** (`experiments/*/matrix.yaml`). Conclusions about how window
  size varies with churn are bounded by the churn rates actually included
  in that matrix.

## To be extended

Anticipated but not yet filled in, pending implementation and pilot data:

- Any CNI-specific classifier edge cases discovered once real CNI traffic
  is observed. `internal/probe/outcome.go` is designed to be CNI-agnostic,
  but that is a claim to be tested against real observations, not an
  assumption to leave unchecked.
- Statistical power / sample size adequacy, once the pre-registered
  experiment (`experiments/exp1-window/preregistration.md`) is finalized.
