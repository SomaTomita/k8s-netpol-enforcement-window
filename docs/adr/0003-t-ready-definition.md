# ADR 0003: t_ready definition — candidate C (victim self-report) over candidate A

## Status

Accepted

## Context

`docs/methodology.md` documents three candidate definitions of `t_ready`:

- **A** — watch-local timestamp (`cmd/watcher`, the initial implementation)
- **B** — CRI-reported container start time (read via `crictl inspect` on
  the kind node, since a kind node is itself a plain Docker container —
  see `scripts/collect.sh`)
- **C** — victim self-report (`cmd/victim`, timestamped the instant its own
  listener starts accepting connections)

A pilot ran all three simultaneously, anchored to the same `-run-epoch`,
across 3 trials against a live Cilium cluster:

| Run | A − B | A − C | B − C |
|---|---|---|---|
| 20260808T163634Z | 939.031 ms | 933.611 ms | −5.421 ms |
| 20260808T164053Z | 749.968 ms | 745.544 ms | −4.425 ms |
| 20260808T164456Z | 21.431 ms | 16.953 ms | −4.478 ms |

Two findings from this:

1. **B and C agree tightly and consistently** (−4.4 to −5.4 ms across all 3
   trials, C always slightly after B — the expected causal order, since C
   is timestamped a few milliseconds into the Go runtime's own startup,
   after the container process B measures has already started).
2. **A diverges from B/C by a large, highly variable amount** (17 ms to
   939 ms across just 3 trials) — two orders of magnitude larger than the
   window this project measures (tens of milliseconds), and not a fixed
   bias that could simply be subtracted out.

The most likely explanation for (2): candidate A's `t_ready` is gated on
the Kubernetes `PodReady` condition, which is itself gated on
`deploy/workloads/victim.yaml`'s `readinessProbe`. The Kubernetes API
requires `periodSeconds >= 1` (a positive integer number of seconds) —
there is no supported way to configure a sub-second readiness-probe
period. If the container's listener actually becomes ready at a
essentially-random point within that 1-second probe cycle, the observed
delay until the *next* probe tick lands somewhere in `[0, ~periodSeconds]`
— which is exactly consistent with the scatter observed (17 ms, 750 ms,
939 ms look like three roughly-uniform draws from that range, not three
measurements of one fixed latency).

This is a stronger disqualification than the bias `docs/methodology.md`
originally anticipated for candidate A ("apiserver notification time plus
watcher-side receive latency" — expected to be small and roughly constant,
single-digit-to-tens of milliseconds). The actual dominant term is the
readiness-probe's periodic polling cadence, which is architecturally
bounded below at 1 full second by the Kubernetes API itself, and behaves
as effectively-random noise rather than a correctable constant offset.

## Decision

**Candidate C (victim self-report) is the primary `t_ready` definition
used for window computation going forward.** Candidate B remains a useful
independent cross-check (it requires no in-Pod instrumentation, so it
generalizes to any future victim workload, instrumented or not), and the
two should keep being collected side by side as a sanity check on each
other. Candidate A (`cmd/watcher`) is retained as a diagnostic signal —
comparing it against B/C remains informative if e.g. a future workload's
`readinessProbe.periodSeconds` changes — but it is disqualified as the
basis for reporting `window = t_blocked - t_ready`: its own noise floor
(up to ~1 second, unpredictably) is orders of magnitude larger than the
phenomenon being measured.

## Consequences

- Positive: window computation going forward uses a `t_ready` whose
  measurement noise (the B↔C gap, 4.4-5.4 ms across these three trials)
  is roughly two orders of magnitude smaller than candidate A's, which
  is the comparison this decision actually rests on. **Corrected
  2026-09-19:** this bullet originally added "and now small relative to
  the windows observed so far (tens of milliseconds)". No window has
  ever been observed by this harness — none of the ten runs under
  `data/raw/` contains a witnessed `Allowed`→`Blocked` transition
  (`docs/threats-to-validity.md`) — so there
  is no observed window magnitude for the noise to be small relative to,
  and that clause is withdrawn. The A/B/C comparison above is unaffected:
  it compares the three candidates against each other, not against a
  window.
- Positive: B and C's mutual agreement is itself a validity check —
  future pilots should keep collecting both, and a sudden large B↔C
  divergence would itself be a signal worth investigating before trusting
  a reported window.
- Negative: candidate C requires the victim workload to be instrumented
  (`cmd/victim`), which is a maintained piece of this project's own code
  rather than an off-the-shelf test image. Any future experiment that
  swaps in a different victim workload needs that workload to emit the
  same self-report, or needs to fall back to candidate B.
- Negative (separate from the A/B/C decision, surfaced by the same pilot):
  the gap between candidate B/C and the prober's *first observation*
  (i.e. `t_blocked`'s own measurement path) was itself 648-904 ms in
  these same trials (**corrected 2026-09-19 — see the correction note
  below; this ADR originally said "650-670 ms"**) — dominated by how long
  a freshly-created prober Pod takes to reach `Running`, not by anything
  this ADR addresses. That is a
  separate, still-open resolution-floor problem, tracked as a follow-up
  (see `docs/threats-to-validity.md`); closing it will likely require the
  prober to already be running and watching for the victim's Pod
  internally (client-go, like `cmd/watcher` does) rather than being
  deployed fresh once the victim's IP is known.
- N=3, exploratory pilot runs, not a pre-registered experiment. Sufficient
  to make this design decision (the periodSeconds >= 1 constraint that
  disqualifies A is a fact about the Kubernetes API, not something that
  needed many trials to establish), but not sufficient to report as a
  result in its own right.

## Correction, 2026-09-19

Two corrections, both to the Consequences section, both recorded here
rather than by silently editing the original text: this file is the
citation record for these figures, and `CLAUDE.md` requires citations to
be checkable against the actual source.

### (a) The first-observation gap was 648-904 ms, not 650-670 ms

The Consequences section above originally recorded the gap between
candidates B/C and the prober's first observation as "650-670 ms". That
range is wrong: it excludes the largest of the three trials by 234 ms.
Recomputed directly from the three pilot runs' own `prober.jsonl`,
`victim.jsonl` and `cri.jsonl` streams (all offsets are nanoseconds from
each run's shared `-run-epoch`, so the subtraction is exact):

| Run | first observation | C | first − C | B | first − B |
|---|---|---|---|---|---|
| 20260808T163634Z | 1 692 434 958 | 788 370 222 | **904.065 ms** | 782 949 597 | 909.485 ms |
| 20260808T164053Z | 3 318 398 632 | 2 647 952 423 | **670.446 ms** | 2 643 527 548 | 674.871 ms |
| 20260808T164456Z | 1 367 657 011 | 719 439 844 | **648.217 ms** | 714 962 178 | 652.695 ms |

So the correct statement is 648-904 ms against candidate C (653-909 ms
against candidate B), not 650-670 ms. The A−B/A−C column of the pilot
table above is unaffected and was checked against the same streams; the
B−C column (−5.421, −4.425, −4.478 ms) is confirmed exactly as printed.

Nothing in the Decision changes. The gap is larger than was recorded, and
its cause (a freshly created prober Pod reaching `Running`) is unaltered.

Every observation in all three runs was classified `Blocked`, and in
each the first observation falls *after* candidate C. The same holds for
every later run held in the author's `data/raw/` tree, including those
under the current prober architecture — see
`docs/threats-to-validity.md`, which records those figures. (`data/raw/`
is gitignored by design, so these documents, not the raw streams, are
the committed record of what was measured.)

### (b) This ADR asserts a magnitude for a window that has never been observed

The Consequences section's first bullet originally said the B↔C noise was
"small relative to the windows observed so far (tens of milliseconds)".
No window has ever been observed by this harness: none of the ten runs
under `data/raw/` contains a witnessed `Allowed`→`Blocked` transition, so
no recorded outcome is a measured window
(`docs/threats-to-validity.md`;
`experiments/exp1-window/preregistration.md`, Addendum 1 D1). Note the
precise form of that claim. It is *not* "every run is `Blocked` from its
first observation", which two of the ten falsify: `20260808T104955Z` is
`Allowed` with no `Blocked` at all (42 568 `Allowed`, 20 `Error`) and
`20260808T140857Z` opens on an `Error`. The other eight — the three
pilot trials above, `20260809T022055Z`, `20260809T060534Z`,
`dev/dev-20260919-012023`, `archive/20260808T142646Z` and
`archive/20260808T144331Z` — are `Blocked` from their first observation.
What holds across all ten, and is the weaker and sufficient statement, is
that no transition was ever witnessed.

The clause is withdrawn in that bullet, and the same unsupported
comparison appears **twice more** in this file, in prose left standing as
the historical record of the reasoning at decision time:

- Context, finding 2: "two orders of magnitude larger than the window
  this project measures (tens of milliseconds)".
- Decision, final sentence: candidate A's noise floor is "orders of
  magnitude larger than the phenomenon being measured".

(The nearby "single-digit-to-tens of milliseconds" in the paragraph after
finding 2 is *not* in this class: it describes candidate A's originally
*expected* bias — a superseded expectation about the instrument — not a
claim about the window.)

Neither is a measurement, and neither should be cited as one. What the
pilot data actually establishes is a comparison *among the three
candidates* — A's spread of 17-939 ms against B↔C's 4.4-5.4 ms — which
needs no assumption about the window's size and is the comparison the
Decision rests on. The window's own magnitude remains unknown, which is
the point of Experiment 1.

## Related

- `docs/methodology.md` — t_ready candidate definitions
- `docs/threats-to-validity.md` — quantified candidate-A bias, and the
  still-open prober-creation-latency threat
- `cmd/victim/` — candidate C implementation
- `scripts/collect.sh` — candidate B extraction via `crictl`
