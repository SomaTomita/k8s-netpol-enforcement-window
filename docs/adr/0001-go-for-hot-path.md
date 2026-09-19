# ADR 0001: Go for the measurement hot path, Python for orchestration and analysis

## Status

Accepted

## Context

This project has two very different kinds of work:

1. **Timing-sensitive measurement.** The `prober` (in-cluster, dials the
   victim repeatedly) and the `watcher` (out-of-cluster, watches for
   Pod-Ready events) both sit directly in the path that produces
   `t_ready` and `t_blocked`. Any latency or jitter these two introduce
   becomes measurement error, not just an inconvenience.
2. **Everything else.** Expanding the experiment matrix, driving `kind`
   cluster lifecycle, loading JSONL output into DuckDB, computing window
   statistics (bootstrap confidence intervals), and producing figures.
   None of this sits in the timing path — it runs before or after a
   measurement run, never during one.

A single-language implementation was considered and rejected. A
garbage-collected runtime's GC pauses are exactly the kind of jitter that
would contaminate the timing-sensitive half of the work. Conversely,
hand-rolling statistics, plotting, and data-wrangling in Go would mean
reimplementing what NumPy, SciPy, DuckDB, and matplotlib already provide.

## Decision

- `cmd/prober/` and `cmd/watcher/`, plus the pure classification and
  sustained-detection logic they depend on (`internal/probe/`), are
  written in Go and compiled to static binaries that run against the
  cluster.
- `src/npw/` (matrix expansion, cluster lifecycle orchestration, analysis,
  statistics, figures) is written in Python.
- The boundary between the two is the JSONL schema defined in
  `internal/record/`: Go only ever writes it, Python only ever reads it.
  Neither side reaches into the other's runtime.

## Consequences

- Positive: the timing-sensitive path stays in a language with
  predictable, low-jitter runtime behavior and no GC-pause risk on the
  hot path, while orchestration and analysis get to use Python's mature
  data and statistics ecosystem instead of reimplementing it.
- Positive: the JSONL contract keeps the two halves independently
  testable. `internal/probe` is fully covered by Go table-driven tests
  with no network I/O; Python analysis code can be tested against
  fixture JSONL without a live cluster.
- Negative: two toolchains, two dependency managers, two lint/test setups
  to keep green in CI (`go test` / `go vet` / `golangci-lint` on one side,
  `uv run pytest` / `ruff` on the other).
- Negative: any change to what a measurement run records has to be made
  in Go and consumed in Python. `internal/record.SchemaVersion` exists to
  catch drift between the two sides of that contract.

## Related

- `internal/record/record.go` — the schema contract
- `CLAUDE.md` — repository layout table
