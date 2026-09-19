# ADR 0002: Single-node kind cluster

## Status

Accepted

## Context

Measuring `window = t_blocked - t_ready` requires comparing two timestamps
that are only meaningful if they were taken off a common clock. A
multi-node cluster would require correlating timestamps captured on
different physical (or virtual) machines, which in turn requires clock
synchronization tight enough not to swamp a window that may itself be
single-digit milliseconds. Building that (e.g. via PTP) is a project in
its own right, and is not the subject of this thesis.

## Decision

Run all experiments on a single-node `kind` cluster (the control-plane
node also schedules workloads). Every component involved in timing — the
`watcher`, the `prober`, and the victim workload — runs as a container on
that one node, and therefore shares one host clock.

## Consequences

- Positive: clock synchronization is eliminated as a source of
  measurement error. The monotonic-clock approach described in
  `docs/methodology.md` is valid without qualification.
- Negative: this design cannot measure any effect specific to enforcement
  propagating across nodes (e.g. per-node CNI agent convergence delay in a
  genuinely multi-node cluster). This is recorded as a threat to validity
  in `docs/threats-to-validity.md`.
- Future: extending to multi-node is explicitly out of scope for this
  thesis. It would need a PTP-based (or equivalent) clock-synchronization
  layer before any cross-node timestamp comparison could be trusted; do
  not add multi-node support without revisiting this ADR.

## Related

- `docs/methodology.md` — clocks section
- `docs/threats-to-validity.md` — internal validity section
